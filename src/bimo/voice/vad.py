"""Voice Activity Detection (VAD) and silence detection for Bimo.

Provides zero-dependency RMS energy-based voice activity detection with
automatic noise floor tracking and configurable speech/silence thresholds.
"""

from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Check if numpy is available for fast vectorised RMS calculation
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def calculate_rms(pcm_bytes: bytes) -> float:
    """Calculate root-mean-square (RMS) energy of 16-bit mono PCM audio data."""
    if not pcm_bytes:
        return 0.0

    if HAS_NUMPY:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16)
        if len(samples) == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    else:
        count = len(pcm_bytes) // 2
        if count == 0:
            return 0.0
        shorts = struct.unpack(f"<{count}h", pcm_bytes[: count * 2])
        sum_sq = sum(s * s for s in shorts)
        return math.sqrt(sum_sq / count)


@dataclass
class VADResult:
    """Result of processing a single audio chunk through the VAD engine."""

    rms_energy: float
    is_speech: bool
    activity_started: bool = False
    activity_stopped: bool = False


class EnergyVAD:
    """Energy-based Voice Activity Detector (VAD).

    Tracks consecutive speech frames to confirm voice activity start, and
    consecutive silence frames to confirm speech completion.
    Features dynamic noise floor tracking to prevent room noise false triggers.
    """

    def __init__(
        self,
        energy_threshold: float = 450.0,
        speech_time_threshold: float = 0.06,
        silence_time_threshold: float = 1.0,
        continue_threshold_ratio: float = 0.60,
        sample_rate: int = 16000,
        chunk_size: int = 1024,
        adaptive_noise_floor: bool = True,
    ) -> None:
        self.energy_threshold = energy_threshold
        self.continue_threshold_ratio = continue_threshold_ratio
        self.continue_energy_threshold = energy_threshold * continue_threshold_ratio
        self.speech_time_threshold = speech_time_threshold
        self.silence_time_threshold = silence_time_threshold
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.adaptive_noise_floor = adaptive_noise_floor

        # Duration of a single chunk in seconds
        self.chunk_duration = float(chunk_size) / float(sample_rate)

        # State tracking
        self._is_speaking = False
        self._consecutive_speech_sec = 0.0
        self._consecutive_silence_sec = 0.0
        self._noise_floor: float = 80.0

    @property
    def is_speaking(self) -> bool:
        """Return whether voice activity is currently deemed ongoing."""
        return self._is_speaking

    @property
    def noise_floor(self) -> float:
        """Return current estimated ambient noise floor RMS."""
        return self._noise_floor

    def reset(self) -> None:
        """Reset internal speech/silence tracking counters."""
        self._is_speaking = False
        self._consecutive_speech_sec = 0.0
        self._consecutive_silence_sec = 0.0

    def process_chunk(self, pcm_chunk: bytes) -> VADResult:
        """Analyze an audio chunk and update voice activity state."""
        energy = calculate_rms(pcm_chunk)

        activity_started = False
        activity_stopped = False

        if not self._is_speaking:
            # During non-speech, adaptively track ambient room noise
            if self.adaptive_noise_floor and energy < (self.energy_threshold * 1.5):
                self._noise_floor = 0.92 * self._noise_floor + 0.08 * energy

            # When IDLE, evaluate against onset threshold (must exceed both base and dynamic floor)
            onset_threshold = max(self.energy_threshold, self._noise_floor * 2.2)
            is_speech = energy >= onset_threshold

            if is_speech:
                self._consecutive_speech_sec += self.chunk_duration
                self._consecutive_silence_sec = 0.0

                if self._consecutive_speech_sec >= self.speech_time_threshold:
                    self._is_speaking = True
                    activity_started = True
                    logger.debug(
                        "VAD: Voice activity started (RMS: %.1f, threshold: %.1f)",
                        energy,
                        onset_threshold,
                    )
            else:
                self._consecutive_speech_sec = 0.0
                self._consecutive_silence_sec += self.chunk_duration
        else:
            # While SPEAKING, use hysteresis threshold so soft trailing words/consonants are not cut off
            continue_threshold = max(self.continue_energy_threshold, self._noise_floor * 1.3)
            is_speech = energy >= continue_threshold

            if is_speech:
                self._consecutive_silence_sec = 0.0
                self._consecutive_speech_sec += self.chunk_duration
            else:
                self._consecutive_silence_sec += self.chunk_duration
                self._consecutive_speech_sec = 0.0

                if self._consecutive_silence_sec >= self.silence_time_threshold:
                    self._is_speaking = False
                    activity_stopped = True
                    logger.debug(
                        "VAD: Voice activity stopped after %.2fs of silence",
                        self._consecutive_silence_sec,
                    )

        return VADResult(
            rms_energy=energy,
            is_speech=is_speech,
            activity_started=activity_started,
            activity_stopped=activity_stopped,
        )
