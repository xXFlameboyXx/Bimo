"""Hardware-independent voice input and speech-to-text interfaces for Bimo.

Decouples the robot state machine and decision-making logic from physical
microphone hardware (PC microphone, Raspberry Pi I2S/USB microphones) and
swappable speech recognition providers (Google Web STT, local Whisper, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from bimo.core.events import Event, EventBus, EventType
from bimo.interfaces.devices import BaseDevice, DeviceCapability, DeviceStatus


@dataclass(frozen=True)
class AudioChunk:
    """Standardized PCM audio chunk captured from a microphone stream."""

    data: bytes
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2  # 16-bit PCM (2 bytes per sample)
    timestamp: float = field(default=0.0)


@dataclass(frozen=True)
class STTResult:
    """Result of speech-to-text transcription."""

    text: str
    confidence: float = 1.0
    is_final: bool = True
    error: str | None = None


@dataclass
class WakeWordResult:
    """Result of wake-word detection analysis on an audio chunk."""

    detected: bool
    model_name: str = ""
    score: float = 0.0
    error: str | None = None
    model: str = ""
    confidence: float = 0.0

    def __post_init__(self) -> None:
        # Cross-populate model and model_name
        if not self.model and self.model_name:
            self.model = self.model_name
        elif self.model and not self.model_name:
            self.model_name = self.model

        # Cross-populate score and confidence
        if self.confidence == 0.0 and self.score != 0.0:
            self.confidence = self.score
        elif self.confidence != 0.0 and self.score == 0.0:
            self.score = self.confidence


class BaseWakeWordDetector(ABC):
    """Abstract interface for local, on-device wake-word detection.

    Processes continuous microphone audio frames without transmitting data
    to cloud services. Supports evaluating multiple wake-word models concurrently.
    """

    def __init__(
        self,
        model_name: str | None = None,
        model_names: Sequence[str] | None = None,
        threshold: float = 0.5,
        cooldown_seconds: float = 1.5,
        event_bus: EventBus | None = None,
    ) -> None:
        if model_names:
            cleaned: list[str] = []
            for m in model_names:
                m_str = str(m).strip()
                if m_str and m_str not in cleaned:
                    cleaned.append(m_str)
            self.model_names: tuple[str, ...] = tuple(cleaned) if cleaned else ("hey_jarvis",)
        elif model_name:
            m_str = str(model_name).strip()
            self.model_names = (m_str,) if m_str else ("hey_jarvis",)
        else:
            self.model_names = ("hey_jarvis",)

        self.model_name = self.model_names[0]
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.event_bus = event_bus
        self._last_detection_time = 0.0

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the identifier for this wake-word provider."""

    @property
    def is_active(self) -> bool:
        """Return True if detector is initialized and ready to process audio."""
        return True

    def attach_event_bus(self, event_bus: EventBus) -> None:
        """Attach central event bus for publishing wake word events."""
        self.event_bus = event_bus

    def _publish_event(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:
        """Publish wake word domain event to attached EventBus."""
        if self.event_bus:
            self.event_bus.publish(
                Event(
                    type=event_type,
                    data=data or {},
                    source=f"wakeword_{self.provider_name}",
                )
            )

    @abstractmethod
    def process_audio(self, audio_chunk: bytes) -> WakeWordResult:
        """Process a 16-bit 16kHz mono PCM chunk and determine if wake word is detected.

        Args:
            audio_chunk: Raw 16-bit mono PCM audio data.

        Returns:
            WakeWordResult indicating detection status, confidence score, and errors.
        """

    @abstractmethod
    def reset(self) -> None:
        """Reset internal buffers and detection states."""


class BaseMicrophone(BaseDevice, ABC):
    """Abstract base class for all microphone audio input sources.

    Subclasses must implement raw audio streaming in 16-bit mono PCM format.
    """

    def __init__(
        self,
        id: str,
        name: str,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
    ) -> None:
        super().__init__(id=id, name=name, capabilities={DeviceCapability.AUDIO_INPUT})
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._is_recording = False

    @property
    def is_recording(self) -> bool:
        """Check if microphone stream is actively recording."""
        return self._is_recording

    @abstractmethod
    def start_stream(self) -> None:
        """Start capturing audio stream from microphone hardware."""

    @abstractmethod
    def stop_stream(self) -> None:
        """Stop capturing audio stream and release hardware buffers."""

    @abstractmethod
    def read_chunk(self, timeout: float | None = None) -> bytes | None:
        """Read a single raw 16-bit PCM chunk from the audio stream buffer."""

    def flush(self) -> None:
        """Discard any accumulated buffered audio chunks to sync with real time."""
        pass

    def stream_chunks(self) -> Iterator[bytes]:
        """Generator yielding audio chunks while recording is active."""
        while self.is_recording:
            chunk = self.read_chunk(timeout=0.2)
            if chunk:
                yield chunk


class BaseSpeechToText(ABC):
    """Abstract provider interface for converting speech audio into text."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of this speech-to-text provider."""

    @abstractmethod
    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> STTResult:
        """Convert raw 16-bit mono PCM audio data into transcribed text.

        Args:
            audio_data: Raw 16-bit mono PCM audio bytes.
            sample_rate: Audio sampling frequency in Hz (typically 16000).

        Returns:
            STTResult containing transcribed text, confidence, and any error message.
        """


class AgentInputInterface(ABC):
    """Contract for receiving transcribed speech into the agent architecture.

    Allows passing user speech input into the agent's turn buffer without
    prematurely invoking the LLM.
    """

    @abstractmethod
    def receive_user_input(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Receive user speech input into the agent subsystem.

        Args:
            text: Transcribed user speech text.
            metadata: Optional contextual metadata (confidence, audio length, etc.).
        """


class BaseTextToSpeech(BaseDevice, ABC):
    """Abstract base class for hardware-independent Text-To-Speech (TTS) output.

    Decouples robot state machine, dialogue managers, and callers from specific
    speech synthesis technologies and speaker hardware (Windows SAPI, Raspberry Pi ALSA/Piper, Mock).
    """

    def __init__(
        self,
        id: str,
        name: str,
        voice: str | None = None,
        speech_rate: int = 175,
        volume: float = 1.0,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__(id=id, name=name, capabilities={DeviceCapability.AUDIO_OUTPUT})
        self._voice = voice
        self._speech_rate = speech_rate
        self._volume = max(0.0, min(1.0, volume))
        self._is_speaking = False
        self._event_bus = event_bus
        self._current_utterance_id = 0

    def attach_event_bus(self, event_bus: EventBus) -> None:
        """Attach an EventBus for publishing speech lifecycle domain events."""
        self._event_bus = event_bus

    def _publish_event(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:
        """Publish a speech lifecycle domain event to the attached EventBus."""
        if self._event_bus:
            self._event_bus.publish(
                Event(
                    type=event_type,
                    data=data or {},
                    source=self.id,
                )
            )

    @property
    def voice(self) -> str | None:
        """Configured voice identifier or name."""
        return self._voice

    @voice.setter
    def voice(self, value: str | None) -> None:
        self._voice = value

    @property
    def speech_rate(self) -> int:
        """Speech rate in words per minute or provider-specific rate scale."""
        return self._speech_rate

    @speech_rate.setter
    def speech_rate(self, value: int) -> None:
        self._speech_rate = value

    @property
    def volume(self) -> float:
        """Volume level from 0.0 (mute) to 1.0 (max)."""
        return self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, value))

    @property
    def is_speaking(self) -> bool:
        """Return True if speech synthesis or audio playback is actively in progress."""
        return self._is_speaking

    def connect(self) -> bool:
        """Establish connection or initialize hardware communication."""
        self.set_status(DeviceStatus.ONLINE)
        return True

    def disconnect(self) -> None:
        """Safely disconnect or shut down audio output device."""
        self.stop()
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        """Perform a liveness and responsiveness check on the audio device."""
        return self.status != DeviceStatus.ERROR

    @abstractmethod
    def speak(self, text: str, block: bool = False) -> bool:
        """Synthesize text and play audio through speaker hardware.

        Args:
            text: Text to synthesize and speak.
            block: If True, blocks until playback finishes. If False (default), returns
                   immediately while speech plays asynchronously in a worker thread.

        Returns:
            True if speech output started successfully, False otherwise.
        """

    @abstractmethod
    def stop(self) -> None:
        """Immediately interrupt and stop any active speech playback."""
