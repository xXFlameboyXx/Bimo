"""Voice input coordinator service for Bimo.

Orchestrates live microphone audio capture, local wake-word detection (openWakeWord),
voice activity detection, speech capture, Whisper STT transcription, and event bus publishing.
Adheres strictly to architectural boundaries: passes transcribed text to AgentInputInterface
without invoking the LLM.
"""

from __future__ import annotations

import collections
from enum import Enum
import logging
import math
import threading
import time
from typing import Any

from bimo.core.events import Event, EventBus, EventType
from bimo.interfaces.voice import (
    AgentInputInterface,
    BaseMicrophone,
    BaseSpeechToText,
    BaseWakeWordDetector,
    STTResult,
)
from bimo.voice.agent_input import DefaultAgentInput
from bimo.voice.vad import EnergyVAD

logger = logging.getLogger(__name__)


class VoicePipelineStage(str, Enum):
    """Internal operating stage of the voice processing pipeline."""

    WAITING_FOR_WAKE_WORD = "WAITING_FOR_WAKE_WORD"
    CAPTURING_SPEECH = "CAPTURING_SPEECH"
    TRANSCRIBING = "TRANSCRIBING"


class VoiceService:
    """Coordinates microphone streaming, wake-word detection, VAD, Whisper STT, and EventBus transitions."""

    def __init__(
        self,
        microphone: BaseMicrophone,
        stt: BaseSpeechToText,
        wake_word_detector: BaseWakeWordDetector | None = None,
        event_bus: EventBus | None = None,
        agent_input: AgentInputInterface | None = None,
        vad: EnergyVAD | None = None,
        min_speech_bytes: int = 3200,  # At least 0.1s of 16kHz audio
        speech_timeout: float = 5.0,  # Max seconds to wait for speech start after wake word
        max_speech_duration: float = 10.0,  # Max speech capture length in seconds
    ) -> None:
        self.microphone = microphone
        self.stt = stt
        self.wake_word_detector = wake_word_detector
        self.event_bus = event_bus
        self.agent_input = agent_input or DefaultAgentInput()
        self.vad = vad or EnergyVAD(
            sample_rate=microphone.sample_rate,
            chunk_size=microphone.chunk_size,
        )
        self.min_speech_bytes = min_speech_bytes
        self.speech_timeout = speech_timeout
        self.max_speech_duration = max_speech_duration

        if self.wake_word_detector and self.event_bus:
            self.wake_word_detector.attach_event_bus(self.event_bus)

        self._is_running = False
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # Pipeline stage
        self._stage = (
            VoicePipelineStage.WAITING_FOR_WAKE_WORD
            if self.wake_word_detector is not None
            else VoicePipelineStage.WAITING_FOR_WAKE_WORD
        )

        # Speech accumulation buffer
        self._speech_buffer = bytearray()
        self._capture_start_time = 0.0
        self._user_spoke_in_capture = False

        # Pre-roll ring buffer (stores ~0.35s of audio before speech onset to preserve the first word/syllables)
        preroll_duration = 0.35  # seconds
        chunk_duration = float(microphone.chunk_size) / float(microphone.sample_rate)
        preroll_count = max(4, int(math.ceil(preroll_duration / chunk_duration)))
        self._preroll_buffer: collections.deque[bytes] = collections.deque(maxlen=preroll_count)

    @property
    def is_running(self) -> bool:
        """Check if voice processing service is active."""
        return self._is_running

    @property
    def stage(self) -> VoicePipelineStage:
        """Current internal processing stage."""
        return self._stage

    def _publish_event(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:
        """Publish domain event to the central event bus."""
        if self.event_bus:
            self.event_bus.publish(
                Event(
                    type=event_type,
                    data=data or {},
                    source="voice_service",
                )
            )

    def start(self) -> None:
        """Start microphone audio stream and background processing loop."""
        with self._lock:
            if self._is_running:
                return

            try:
                self.microphone.start_stream()
            except Exception as e:
                logger.error("Failed to start microphone hardware: %s", e)
                self._publish_event(
                    EventType.SPEECH_ERROR,
                    {"error": f"Microphone hardware failure: {e}"},
                )
                raise

            self._stop_event.clear()
            self._is_running = True
            self._stage = VoicePipelineStage.WAITING_FOR_WAKE_WORD
            self.microphone.flush()
            self._preroll_buffer.clear()
            if self.wake_word_detector is not None:
                self.wake_word_detector.reset()
            self._worker_thread = threading.Thread(
                target=self._processing_loop,
                name="VoiceServiceWorker",
                daemon=True,
            )
            self._worker_thread.start()
            logger.info("VoiceService started with microphone: %s", self.microphone.name)

    def stop(self) -> None:
        """Stop processing loop and release microphone hardware."""
        with self._lock:
            if not self._is_running:
                return

            self._stop_event.set()
            self._is_running = False

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.5)
            self._worker_thread = None

        try:
            self.microphone.stop_stream()
        except Exception as e:
            logger.debug("Error stopping microphone stream: %s", e)

        logger.info("VoiceService stopped cleanly.")

    def trigger_wake_word(self, model_name: str = "manual_trigger") -> None:
        """Manually trigger wake-word activation (useful for testing and UI events)."""
        now = time.time()
        self._stage = VoicePipelineStage.CAPTURING_SPEECH
        self._capture_start_time = now
        self._user_spoke_in_capture = False
        self._speech_buffer.clear()
        self.vad.reset()

        self._publish_event(
            EventType.WAKE_WORD_DETECTED,
            {"model": model_name, "score": 1.0},
        )
        self._publish_event(
            EventType.LISTENING_STARTED,
            {"source": "manual", "model": model_name},
        )
        self._publish_event(
            EventType.SPEECH_CAPTURE_STARTED,
            {"timestamp": now},
        )

    def _processing_loop(self) -> None:
        """Continuously read audio chunks, detect wake-word/speech, and trigger transcription."""
        logger.debug("VoiceService processing thread active.")

        while not self._stop_event.is_set():
            try:
                chunk = self.microphone.read_chunk(timeout=0.1)
            except Exception as e:
                logger.error("Microphone read error: %s", e)
                self._publish_event(
                    EventType.SPEECH_ERROR,
                    {"error": f"Microphone read error: {e}"},
                )
                time.sleep(0.2)
                continue

            if not chunk:
                continue

            now = time.time()

            # Pipeline Mode 1: Wake-word detector is configured
            if self.wake_word_detector is not None:
                if self._stage == VoicePipelineStage.WAITING_FOR_WAKE_WORD:
                    # Rolling pre-roll buffer maintains ambient speech onset
                    self._preroll_buffer.append(chunk)

                    ww_res = self.wake_word_detector.process_audio(chunk)
                    if ww_res.detected:
                        logger.info(
                            "Wake word detected ('%s', score: %.2f). Switching to speech capture.",
                            ww_res.model_name,
                            ww_res.score,
                        )
                        self._stage = VoicePipelineStage.CAPTURING_SPEECH
                        self._capture_start_time = now
                        self._user_spoke_in_capture = False
                        self._speech_buffer.clear()
                        # Prepend preroll buffer chunks
                        for p in self._preroll_buffer:
                            self._speech_buffer.extend(p)
                        self._preroll_buffer.clear()

                        self._publish_event(
                            EventType.LISTENING_STARTED,
                            {"source": "wake_word", "model": ww_res.model_name},
                        )
                        self._publish_event(
                            EventType.SPEECH_CAPTURE_STARTED,
                            {"timestamp": now},
                        )
                        self.vad.reset()

                elif self._stage == VoicePipelineStage.CAPTURING_SPEECH:
                    self._speech_buffer.extend(chunk)
                    vad_res = self.vad.process_chunk(chunk)

                    if vad_res.activity_started or self.vad.is_speaking:
                        if not self._user_spoke_in_capture:
                            self._user_spoke_in_capture = True
                            self._publish_event(
                                EventType.VOICE_ACTIVITY_STARTED,
                                {"rms_energy": vad_res.rms_energy},
                            )
                        self._publish_event(
                            EventType.USER_SPEAKING,
                            {"audio_level": vad_res.rms_energy},
                        )

                    capture_elapsed = now - self._capture_start_time

                    # Timeout check: user triggered wake word but never spoke
                    if not self._user_spoke_in_capture and capture_elapsed >= self.speech_timeout:
                        logger.info("Speech capture timed out after %.1fs of silence.", capture_elapsed)
                        self._publish_event(
                            EventType.SPEECH_CAPTURE_STOPPED,
                            {"reason": "timeout"},
                        )
                        self._publish_event(
                            EventType.LISTENING_STOPPED,
                            {"reason": "Speech timeout"},
                        )
                        self._stage = VoicePipelineStage.WAITING_FOR_WAKE_WORD
                        self._speech_buffer.clear()
                        self.vad.reset()
                        continue

                    # End of speech condition: user spoke and silence threshold reached, or max duration exceeded
                    # Enforce a minimum capture duration (0.3s) to prevent immediate cutoff on wake-word release
                    if self._user_spoke_in_capture and (
                        (vad_res.activity_stopped and capture_elapsed >= 0.3)
                        or capture_elapsed >= self.max_speech_duration
                    ):
                        logger.info(
                            "End of user speech detected (captured %d bytes in %.2fs).",
                            len(self._speech_buffer),
                            capture_elapsed,
                        )
                        self._publish_event(EventType.VOICE_ACTIVITY_STOPPED)
                        self._publish_event(
                            EventType.SPEECH_CAPTURE_STOPPED,
                            {"reason": "completed", "duration": capture_elapsed},
                        )
                        self._publish_event(
                            EventType.LISTENING_STOPPED,
                            {"reason": "speech_captured"},
                        )

                        captured_audio = bytes(self._speech_buffer)
                        self._speech_buffer.clear()
                        self._stage = VoicePipelineStage.WAITING_FOR_WAKE_WORD
                        self.vad.reset()

                        if len(captured_audio) >= self.min_speech_bytes:
                            self._transcribe_and_dispatch(captured_audio)
                        else:
                            logger.debug("Discarding short audio pulse (< min_speech_bytes)")

                        # Flush any audio that accumulated in the microphone queue during transcription
                        # to ensure we listen to real-time audio and avoid backlog echo
                        self.microphone.flush()
                        self._preroll_buffer.clear()
                        if self.wake_word_detector is not None:
                            self.wake_word_detector.reset()

            # Pipeline Mode 2: No wake-word detector (direct continuous VAD mode from Phase 3)
            else:
                vad_res = self.vad.process_chunk(chunk)
                if vad_res.activity_started:
                    logger.info("Voice activity detected (RMS: %.1f). Starting speech capture...", vad_res.rms_energy)
                    self._speech_buffer.clear()
                    for pre_chunk in self._preroll_buffer:
                        self._speech_buffer.extend(pre_chunk)
                    self._speech_buffer.extend(chunk)
                    self._preroll_buffer.clear()
                    self._publish_event(
                        EventType.VOICE_ACTIVITY_STARTED,
                        {"rms_energy": vad_res.rms_energy},
                    )
                    self._publish_event(
                        EventType.USER_SPEAKING,
                        {"audio_level": vad_res.rms_energy},
                    )
                elif self.vad.is_speaking:
                    self._speech_buffer.extend(chunk)
                elif vad_res.activity_stopped:
                    logger.info(
                        "Voice activity stopped. Captured %d bytes of speech audio.",
                        len(self._speech_buffer),
                    )
                    self._publish_event(EventType.VOICE_ACTIVITY_STOPPED)

                    trailing_keep_chunks = int(math.ceil(0.35 / self.vad.chunk_duration))
                    total_silence_chunks = int(math.ceil(self.vad.silence_time_threshold / self.vad.chunk_duration))
                    excess_silence_chunks = max(0, total_silence_chunks - trailing_keep_chunks)
                    bytes_per_chunk = self.microphone.chunk_size * 2
                    excess_bytes = excess_silence_chunks * bytes_per_chunk

                    raw_captured = bytes(self._speech_buffer)
                    self._speech_buffer.clear()
                    self._preroll_buffer.clear()

                    if excess_bytes > 0 and len(raw_captured) > excess_bytes + (bytes_per_chunk * 4):
                        captured_audio = raw_captured[:-excess_bytes]
                    else:
                        captured_audio = raw_captured

                    if len(captured_audio) >= self.min_speech_bytes:
                        self._transcribe_and_dispatch(captured_audio)
                    else:
                        logger.debug("Discarding short audio pulse (< min_speech_bytes)")
                else:
                    self._preroll_buffer.append(chunk)

    def _transcribe_and_dispatch(self, audio_bytes: bytes) -> None:
        """Transcribe captured speech audio and emit domain events."""
        try:
            self._publish_event(
                EventType.SPEECH_TRANSCRIPTION_STARTED,
                {"audio_bytes_length": len(audio_bytes)},
            )
            result = self.stt.transcribe(audio_bytes, sample_rate=self.microphone.sample_rate)

            if result.error:
                # Speech recognition error occurred
                logger.warning("Speech recognition error: %s", result.error)
                self._publish_event(
                    EventType.SPEECH_TRANSCRIPTION_ERROR,
                    {"error": result.error},
                )
                self._publish_event(
                    EventType.SPEECH_ERROR,
                    {"error": result.error},
                )
            elif result.text:
                # Successful transcription
                logger.info(
                    "Transcribed speech: '%s' (Confidence: %.2f)",
                    result.text,
                    result.confidence,
                )
                self._publish_event(
                    EventType.SPEECH_TRANSCRIPTION_COMPLETED,
                    {
                        "transcript": result.text,
                        "confidence": result.confidence,
                    },
                )

                # Deliver to AgentInputInterface (strictly no LLM invocation in Phase 3/3.5)
                self.agent_input.receive_user_input(
                    result.text,
                    metadata={
                        "confidence": result.confidence,
                        "audio_bytes_length": len(audio_bytes),
                    },
                )

                # Emit SPEECH_RECEIVED event (transitions LISTENING -> THINKING)
                self._publish_event(
                    EventType.SPEECH_RECEIVED,
                    {
                        "transcript": result.text,
                        "confidence": result.confidence,
                    },
                )
            else:
                # Empty speech / silence
                logger.debug("No speech recognized in audio buffer.")
        except Exception as e:
            logger.error("Unexpected error during speech transcription: %s", e)
            self._publish_event(
                EventType.SPEECH_TRANSCRIPTION_ERROR,
                {"error": str(e)},
            )
            self._publish_event(
                EventType.SPEECH_ERROR,
                {"error": str(e)},
            )

    def process_audio_buffer(self, audio_bytes: bytes) -> STTResult:
        """Directly transcribe and dispatch a complete audio buffer (useful for testing)."""
        self._publish_event(
            EventType.SPEECH_TRANSCRIPTION_STARTED,
            {"audio_bytes_length": len(audio_bytes)},
        )
        result = self.stt.transcribe(audio_bytes, sample_rate=self.microphone.sample_rate)
        if result.text:
            self._publish_event(
                EventType.SPEECH_TRANSCRIPTION_COMPLETED,
                {"transcript": result.text, "confidence": result.confidence},
            )
            self.agent_input.receive_user_input(result.text, metadata={"confidence": result.confidence})
            self._publish_event(
                EventType.SPEECH_RECEIVED,
                {"transcript": result.text, "confidence": result.confidence},
            )
        elif result.error:
            self._publish_event(
                EventType.SPEECH_TRANSCRIPTION_ERROR,
                {"error": result.error},
            )
            self._publish_event(
                EventType.SPEECH_ERROR,
                {"error": result.error},
            )
        return result
