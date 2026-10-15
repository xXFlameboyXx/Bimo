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
from bimo.voice.normalizer import contains_wake_word, normalize_phonetic_text
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
        require_wake_word: bool = False,
        wake_word_models: Sequence[str] | None = None,
        hybrid_wake_word: bool = True,
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
        self.require_wake_word = require_wake_word
        self.wake_word_models = tuple(wake_word_models) if wake_word_models else ("bimo", "hey_jarvis", "alexa")
        self.hybrid_wake_word = hybrid_wake_word
        self._wake_word_detected_for_current_turn = False
        self._detected_wake_word_model: str | None = None

        if self.wake_word_detector and self.event_bus:
            self.wake_word_detector.attach_event_bus(self.event_bus)

        self._is_self_speaking = False
        if self.event_bus:
            self.event_bus.subscribe(EventType.WAKE_WORD_DETECTED, self._on_wake_word_event)
            self.event_bus.subscribe(EventType.SPEECH_OUTPUT_STARTED, self._on_speech_output_started)
            self.event_bus.subscribe(EventType.SPEECH_OUTPUT_FINISHED, self._on_speech_output_finished)
            self.event_bus.subscribe(EventType.SPEECH_OUTPUT_CANCELLED, self._on_speech_output_finished)
            self.event_bus.subscribe(EventType.SPEECH_OUTPUT_ERROR, self._on_speech_output_finished)

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

        # Barge-in and spoken interrupt tracking
        self._barge_in_buffer = bytearray()
        self._barge_in_silence_count = 0

    def _on_wake_word_event(self, event: Event) -> None:
        """Respond to externally triggered wake word events (e.g. keyboard UI, remote client)."""
        if event.source == "voice_service":
            return
        if self._is_running and self._stage == VoicePipelineStage.WAITING_FOR_WAKE_WORD and not self._is_self_speaking:
            logger.info("External wake word trigger from '%s'. Starting speech capture.", event.source)
            self.trigger_wake_word(model_name=str(event.data.get("keyword", "external")))

    def _on_speech_output_started(self, event: Event) -> None:
        """Acoustic echo cancellation: mute mic capture while robot is vocalizing."""
        with self._lock:
            self._is_self_speaking = True
            self._speech_buffer.clear()
            self._preroll_buffer.clear()
            self._barge_in_buffer.clear()
            self._barge_in_silence_count = 0
            self.vad.reset()
            if self._stage == VoicePipelineStage.CAPTURING_SPEECH:
                self._stage = (
                    VoicePipelineStage.WAITING_FOR_WAKE_WORD
                    if self.wake_word_detector is not None
                    else VoicePipelineStage.WAITING_FOR_WAKE_WORD
                )
        logger.debug("VoiceService: Robot speaking; muted mic capture to prevent self-trigger.")

    def _on_speech_output_finished(self, event: Event) -> None:
        """Unmute mic capture and flush reverberation after robot vocalization finishes."""
        def _delayed_unmute() -> None:
            time.sleep(0.18)  # Let speaker audio dissipate in the room
            with self._lock:
                self._is_self_speaking = False
                try:
                    self.microphone.flush()
                except Exception:
                    pass
                self._speech_buffer.clear()
                self._preroll_buffer.clear()
                self.vad.reset()
            logger.debug("VoiceService: Robot finished speaking; unmuted mic capture.")

        threading.Thread(target=_delayed_unmute, daemon=True).start()

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
        with self._lock:
            self._wake_word_detected_for_current_turn = True
            self._detected_wake_word_model = model_name
            self._stage = VoicePipelineStage.CAPTURING_SPEECH
            self._capture_start_time = now
            self._user_spoke_in_capture = False
            self._speech_buffer.clear()
            for p in self._preroll_buffer:
                self._speech_buffer.extend(p)
            self._preroll_buffer.clear()
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

            # Barge-in and spoken interrupt support: When Bimo is speaking
            if self._is_self_speaking:
                # 1. Wake word detector check
                if self.wake_word_detector is not None:
                    ww_res = self.wake_word_detector.process_audio(chunk)
                    if ww_res.detected:
                        logger.info("Barge-in wake word detected while speaking! Cancelling TTS.")
                        self._publish_event(EventType.SPEECH_OUTPUT_CANCELLED, {"reason": "barge_in"})
                        self._is_self_speaking = False
                        self._stage = VoicePipelineStage.CAPTURING_SPEECH
                        self._capture_start_time = now
                        self._user_spoke_in_capture = False
                        self._speech_buffer.clear()
                        self._barge_in_buffer.clear()
                        self.vad.reset()
                        self._publish_event(
                            EventType.LISTENING_STARTED,
                            {"source": "barge_in", "model": ww_res.model_name},
                        )
                        self._publish_event(
                            EventType.SPEECH_CAPTURE_STARTED,
                            {"timestamp": now},
                        )
                        continue

                # 2. Spoken interrupt check (e.g. saying "Stop", "Cancel", "Abort" into mic)
                vad_res = self.vad.process_chunk(chunk)
                if vad_res.is_speech or vad_res.activity_started:
                    self._barge_in_buffer.extend(chunk)
                    self._barge_in_silence_count = 0
                elif len(self._barge_in_buffer) > 0:
                    self._barge_in_buffer.extend(chunk)
                    self._barge_in_silence_count += 1
                    # After ~0.25s of silence or max 1.5s audio (approx 24000 bytes for 16kHz 16-bit mono)
                    if self._barge_in_silence_count >= 4 or len(self._barge_in_buffer) >= 24000:
                        barge_audio = bytes(self._barge_in_buffer)
                        self._barge_in_buffer.clear()
                        self._barge_in_silence_count = 0
                        try:
                            stt_res = self.stt.transcribe(barge_audio, sample_rate=self.microphone.sample_rate)
                            raw_txt = (stt_res.text or "").strip()
                            if raw_txt:
                                txt = normalize_phonetic_text(raw_txt)
                                logger.debug("Barge-in speech transcribed during TTS playback: '%s'", txt)
                                from bimo.agent.cancellation import TaskInterruptManager, InterruptCommand
                                cmd = TaskInterruptManager.classify(txt)
                                if cmd != InterruptCommand.NONE or any(
                                    w in txt.lower() for w in ("stop", "cancel", "shut up", "abort", "quiet", "halt", "pause", "never mind", "nevermind", "wait", "continue", "resume", "shh", "ruko", "rok do")
                                ):
                                    logger.info("Spoken interrupt '%s' (cmd=%s) confirmed while speaking! Cancelling TTS.", txt, cmd.value)
                                    self._publish_event(
                                        EventType.SPEECH_OUTPUT_CANCELLED,
                                        {"reason": "user_interrupt", "transcript": txt, "command": cmd.value},
                                    )
                                    self._publish_event(
                                        EventType.INTERRUPT_COMMAND_RECEIVED,
                                        {"command": cmd.value, "transcript": txt, "source": "barge_in"},
                                    )
                                    self._is_self_speaking = False
                                    self.vad.reset()
                                    try:
                                        self.microphone.flush()
                                    except Exception:
                                        pass

                                    # Forward interrupt to orchestrator/agent to pause, cancel, or resume task
                                    self.agent_input.receive_user_input(
                                        txt,
                                        metadata={"barge_in": True, "command": cmd.value},
                                    )
                                else:
                                    logger.debug("Ignoring non-interrupt audio transcribed during speech: '%s'", txt)
                        except Exception as b_err:
                            logger.debug("Barge-in transcription error: %s", b_err)
                continue

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
                        self._wake_word_detected_for_current_turn = True
                        self._detected_wake_word_model = ww_res.model_name
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

                    elif self.hybrid_wake_word:
                        # Hybrid / Speech-assisted wake word detection:
                        # If acoustic detector didn't fire, monitor VAD speech onset to capture potential "Bimo" wake phrases
                        vad_res = self.vad.process_chunk(chunk)
                        if vad_res.activity_started:
                            logger.debug("Voice onset in WAITING_FOR_WAKE_WORD; starting candidate capture...")
                            self._wake_word_detected_for_current_turn = False
                            self._detected_wake_word_model = None
                            self._stage = VoicePipelineStage.CAPTURING_SPEECH
                            self._capture_start_time = now
                            self._user_spoke_in_capture = True
                            self._speech_buffer.clear()
                            for p in self._preroll_buffer:
                                self._speech_buffer.extend(p)
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
                            self._publish_event(
                                EventType.SPEECH_CAPTURE_STARTED,
                                {"timestamp": now},
                            )

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
                    capture_audio_sec = len(self._speech_buffer) / float(self.microphone.sample_rate * 2)
                    if self._user_spoke_in_capture and (
                        (vad_res.activity_stopped and (capture_elapsed >= 0.2 or capture_audio_sec >= 0.2))
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

                    # Flush stale audio accumulated in microphone queue during transcription
                    self.microphone.flush()
                    self._preroll_buffer.clear()
                    self.vad.reset()
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
                # 1. Phonetic normalization across speech recognizer variants
                normalized_text = normalize_phonetic_text(result.text)

                # 2. Check for wake-word presence in the speech
                has_ww, matched_ww = contains_wake_word(
                    normalized_text, configured_models=self.wake_word_models
                )

                # 3. Urgent interruption command check (e.g. "wait", "stop", "continue", "pause", "cancel")
                from bimo.agent.cancellation import TaskInterruptManager, InterruptCommand
                cmd = TaskInterruptManager.classify(normalized_text)
                is_interrupt = (cmd != InterruptCommand.NONE) or any(
                    normalized_text.lower().startswith(w) for w in ("stop", "wait", "pause", "continue", "resume", "hold on", "cancel", "ruko", "rok do")
                )

                # 4. Check if wake word requirement was satisfied (acoustically, via speech, or urgent interrupt)
                is_wake_word_satisfied = self._wake_word_detected_for_current_turn or has_ww or is_interrupt

                if is_interrupt:
                    logger.info("VoiceService: Urgent interruption command '%s' (cmd=%s) recognized.", normalized_text, cmd.value)
                    self._publish_event(
                        EventType.INTERRUPT_COMMAND_RECEIVED,
                        {"command": cmd.value, "transcript": normalized_text},
                    )

                # 5. Strict gating: Drop utterance only if wake-word is required AND not an urgent interrupt
                if self.require_wake_word and not is_wake_word_satisfied:
                    logger.info(
                        "VoiceService: Speech ignored (wake word required, but not detected in '%s')",
                        normalized_text,
                    )
                    self._publish_event(
                        EventType.SPEECH_IGNORED,
                        {
                            "transcript": normalized_text,
                            "raw_transcript": result.text,
                            "reason": "no_wake_word",
                            "confidence": result.confidence,
                        },
                    )
                    return

                # If wake word detected via speech transcript, emit wake word event
                if has_ww and not self._wake_word_detected_for_current_turn:
                    ww_name = matched_ww or "Bimo"
                    logger.info("VoiceService: Wake word '%s' confirmed in transcript.", ww_name)
                    self._publish_event(
                        EventType.WAKE_WORD_DETECTED,
                        {"model": ww_name, "score": 1.0, "source": "transcript"},
                    )

                logger.info(
                    "Transcribed speech: '%s' (Confidence: %.2f)",
                    normalized_text,
                    result.confidence,
                )
                self._publish_event(
                    EventType.SPEECH_TRANSCRIPTION_COMPLETED,
                    {
                        "transcript": normalized_text,
                        "confidence": result.confidence,
                    },
                )

                utterance_id = f"utt-{time.time():.4f}-{len(audio_bytes)}"

                # Emit SPEECH_RECEIVED event (transitions LISTENING -> THINKING immediately)
                self._publish_event(
                    EventType.SPEECH_RECEIVED,
                    {
                        "transcript": normalized_text,
                        "confidence": result.confidence,
                        "utterance_id": utterance_id,
                    },
                )

                # Deliver to AgentInputInterface (strictly no LLM invocation in Phase 3/3.5)
                self.agent_input.receive_user_input(
                    normalized_text,
                    metadata={
                        "confidence": result.confidence,
                        "audio_bytes_length": len(audio_bytes),
                        "utterance_id": utterance_id,
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
            normalized_text = normalize_phonetic_text(result.text)
            has_ww, matched_ww = contains_wake_word(normalized_text, configured_models=self.wake_word_models)
            from bimo.agent.cancellation import TaskInterruptManager, InterruptCommand
            cmd = TaskInterruptManager.classify(normalized_text)
            is_interrupt = (cmd != InterruptCommand.NONE) or any(
                normalized_text.lower().startswith(w) for w in ("stop", "wait", "pause", "continue", "resume", "hold on", "cancel", "ruko", "rok do")
            )
            wake_word_satisfied = self._wake_word_detected_for_current_turn or has_ww or is_interrupt

            if self.require_wake_word and not wake_word_satisfied:
                logger.info("Speech buffer ignored (wake word required but not in '%s')", normalized_text)
                self._publish_event(
                    EventType.SPEECH_IGNORED,
                    {"transcript": normalized_text, "raw_transcript": result.text, "reason": "no_wake_word"},
                )
                return STTResult(text=normalized_text, confidence=result.confidence, is_final=True)

            if is_interrupt:
                self._publish_event(
                    EventType.INTERRUPT_COMMAND_RECEIVED,
                    {"command": cmd.value, "transcript": normalized_text},
                )

            if has_ww and not self._wake_word_detected_for_current_turn:
                ww_name = matched_ww or "Bimo"
                self._publish_event(
                    EventType.WAKE_WORD_DETECTED,
                    {"model": ww_name, "score": 1.0, "source": "transcript"},
                )

            self._publish_event(
                EventType.SPEECH_TRANSCRIPTION_COMPLETED,
                {"transcript": normalized_text, "confidence": result.confidence},
            )
            self.agent_input.receive_user_input(normalized_text, metadata={"confidence": result.confidence})
            self._publish_event(
                EventType.SPEECH_RECEIVED,
                {"transcript": normalized_text, "confidence": result.confidence},
            )
            return STTResult(text=normalized_text, confidence=result.confidence, is_final=True)
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
