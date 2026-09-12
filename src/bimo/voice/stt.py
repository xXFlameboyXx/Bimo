"""Speech-To-Text (STT) provider implementations for Bimo.

Provides:
- GoogleWebSTT: Free, zero-API-key Google Speech Recognition provider for development.
- MockSpeechToText: Deterministic mock STT for unit testing and offline development.
- LocalWhisperSTT: Stub for local on-device Whisper models.
"""

from __future__ import annotations

import io
import logging
import wave
from typing import Any

from bimo.interfaces.voice import BaseSpeechToText, STTResult

logger = logging.getLogger(__name__)

# Try importing speech_recognition
try:
    import speech_recognition as sr
    HAS_SR = True
except ImportError:
    HAS_SR = False


def normalize_pcm(
    pcm_bytes: bytes,
    target_peak: int = 22000,
    max_gain: float = 50.0,
    noise_floor_peak: float = 20.0,
) -> bytes:
    """Normalize 16-bit mono PCM amplitude so soft speech is clearly recognized.

    Acoustic characteristics & gain policy:
    - Silence & Empty: If buffer is empty or peak is 0, audio is returned unchanged.
    - Noise Floor (< noise_floor_peak, default 20.0): Ambient room noise (typically
      peak 4-8, RMS ~1.1) is not amplified, preventing background noise amplification.
    - Soft Speech (noise_floor_peak <= peak < target_peak): Scaled towards target_peak
      (default 22,000, ~-3 dBFS) clamped to a maximum linear gain factor of `max_gain`
      (default 50.0x / +34 dB). For example:
        - 100 peak (whispered speech): scaled by 50.0x -> 5,000 peak.
        - 250 peak (soft monosyllables like 'hi'/'hey'): scaled by 50.0x -> 12,500 peak.
    - High Amplitude (peak >= target_peak): Audio is already sufficiently loud and
      is left untouched to avoid distortion.
    - Clipping Protection: All scaled samples are strictly clipped to [-32768, 32767]
      before int16 conversion, eliminating integer overflow distortion.

    Args:
        pcm_bytes: Raw 16-bit mono PCM audio bytes.
        target_peak: Target peak amplitude in int16 range (default 22000).
        max_gain: Maximum linear multiplication gain (default 50.0x).
        noise_floor_peak: Minimum peak required to trigger amplification (default 20.0).

    Returns:
        Normalized 16-bit mono PCM audio bytes.
    """
    if not pcm_bytes:
        return pcm_bytes
    try:
        import numpy as np

        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return pcm_bytes

        peak = float(np.max(np.abs(samples)))
        if noise_floor_peak < peak < target_peak:
            scale = min(target_peak / peak, max_gain)
            scaled = np.clip(samples * scale, -32768, 32767).astype(np.int16)
            return scaled.tobytes()
    except Exception as exc:
        logger.debug("PCM normalization skipped due to error: %s", exc)
    return pcm_bytes


class GoogleWebSTT(BaseSpeechToText):
    """Development Speech-To-Text provider using Google Web Speech Recognition API.

    Requires no API keys and provides rapid transcription for development on PC.
    """

    def __init__(self, language: str = "en-US") -> None:
        self.language = language
        self._recognizer: Any = None
        if HAS_SR:
            self._recognizer = sr.Recognizer()

    @property
    def provider_name(self) -> str:
        return "GoogleWebSTT"

    @staticmethod
    def _normalize_pcm(pcm_bytes: bytes, target_peak: int = 22000, max_gain: float = 50.0) -> bytes:
        """Normalize PCM amplitude (delegates to normalize_pcm with max_gain=50.0x)."""
        return normalize_pcm(pcm_bytes, target_peak=target_peak, max_gain=max_gain)

    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> STTResult:
        """Transcribe raw 16-bit PCM mono audio using Google Web STT."""
        if not audio_data or len(audio_data) < 100:
            return STTResult(text="", confidence=0.0, is_final=True, error="Empty audio")

        if not HAS_SR or self._recognizer is None:
            return STTResult(
                text="",
                confidence=0.0,
                is_final=True,
                error="SpeechRecognition library not installed",
            )

        try:
            # 16-bit mono PCM has sample_width = 2
            pcm_to_send = self._normalize_pcm(audio_data)
            sr_audio = sr.AudioData(pcm_to_send, sample_rate, 2)
            logger.info("Transcribing audio buffer (%d bytes) via Google STT...", len(pcm_to_send))
            text = self._recognizer.recognize_google(sr_audio, language=self.language)
            logger.info("Google STT transcribed text: '%s'", text)
            return STTResult(text=text.strip(), confidence=0.95, is_final=True)
        except sr.UnknownValueError:
            logger.info("Google STT: Speech unintelligible or silence detected")
            return STTResult(text="", confidence=0.0, is_final=True, error="Unintelligible speech")
        except sr.RequestError as e:
            logger.error("Google STT network request error: %s", e)
            return STTResult(text="", confidence=0.0, is_final=True, error=f"STT network error: {e}")
        except Exception as e:
            logger.error("Unexpected error in Google STT: %s", e)
            return STTResult(text="", confidence=0.0, is_final=True, error=str(e))


class MockSpeechToText(BaseSpeechToText):
    """Deterministic mock speech-to-text provider for automated tests."""

    def __init__(
        self,
        default_text: str = "Hello Bimo",
        confidence: float = 1.0,
        simulated_error: str | None = None,
    ) -> None:
        self.default_text = default_text
        self.confidence = confidence
        self.simulated_error = simulated_error
        self.transcribe_calls: list[bytes] = []

    @property
    def provider_name(self) -> str:
        return "MockSTT"

    def set_next_result(
        self, text: str, confidence: float = 1.0, error: str | None = None
    ) -> None:
        """Configure the result for subsequent transcription calls."""
        self.default_text = text
        self.confidence = confidence
        self.simulated_error = error

    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> STTResult:
        self.transcribe_calls.append(audio_data)

        if not audio_data:
            return STTResult(
                text="",
                confidence=0.0,
                is_final=True,
                error="Empty audio buffer",
            )

        if self.simulated_error:
            err = self.simulated_error
            self.simulated_error = None  # One-shot error
            return STTResult(
                text="",
                confidence=0.0,
                is_final=True,
                error=err,
            )

        return STTResult(
            text=self.default_text,
            confidence=self.confidence,
            is_final=True,
        )


try:
    from faster_whisper import WhisperModel
    HAS_FASTER_WHISPER = True
except ImportError:
    WhisperModel = None  # type: ignore
    HAS_FASTER_WHISPER = False


class WhisperSTT(BaseSpeechToText):
    """Local, on-device Speech-To-Text provider powered by faster-whisper (CTranslate2).

    Transcribes 16-bit mono PCM audio on CPU with minimal memory footprint (e.g. ~40MB
    for tiny.en in INT8), optimized for Raspberry Pi 3A+ and local embedded deployment.
    """

    def __init__(
        self,
        model_size: str = "base.en",
        model_path: str | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
        initial_prompt: str | None = "Bimo, Jarvis, Alexa, computer, switch, turn on, turn off, lights, time, weather.",
    ) -> None:
        self.model_size = model_size
        self.model_path = model_path
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self._model: Any = None
        self._is_initialized = False

    @property
    def provider_name(self) -> str:
        target = self.model_path or self.model_size
        return f"WhisperSTT({target})"

    def _ensure_model_loaded(self) -> None:
        """Lazy-load the WhisperModel on first transcription request."""
        if self._is_initialized:
            return

        if not HAS_FASTER_WHISPER:
            logger.warning(
                "faster-whisper is not installed. WhisperSTT cannot load model."
            )
            return

        target_model = self.model_path or self.model_size
        logger.info(
            "Loading local Whisper model '%s' (device=%s, compute_type=%s)...",
            target_model,
            self.device,
            self.compute_type,
        )
        try:
            self._model = WhisperModel(
                target_model,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._is_initialized = True
            logger.info("Whisper model '%s' loaded successfully.", target_model)
        except Exception as exc:
            logger.error("Failed to load Whisper model '%s': %s", target_model, exc)
            self._model = None
            raise

    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> STTResult:
        """Transcribe raw 16-bit mono PCM audio data using local Whisper.

        Args:
            audio_data: Raw 16-bit mono PCM audio bytes.
            sample_rate: Audio sampling frequency in Hz (default 16000).

        Returns:
            STTResult containing transcribed text, confidence, and error if any.
        """
        if not audio_data or len(audio_data) == 0:
            return STTResult(
                text="",
                confidence=0.0,
                is_final=True,
                error="Empty audio buffer",
            )

        # Normalize PCM amplitude to boost signal-to-noise ratio on quiet microphones
        norm_pcm = normalize_pcm(audio_data, target_peak=22000, max_gain=30.0)

        # Ensure audio byte length is even for 16-bit PCM
        if len(norm_pcm) % 2 != 0:
            norm_pcm = norm_pcm[:-1]
            if len(norm_pcm) == 0:
                return STTResult(text="", confidence=0.0, is_final=True, error="Empty audio buffer")

        try:
            import numpy as np

            # Fast silence check: if all bytes are zero or energy is near-zero
            samples_int16 = np.frombuffer(norm_pcm, dtype=np.int16)
            if len(samples_int16) == 0:
                return STTResult(text="", confidence=0.0, is_final=True, error="Empty audio buffer")

            peak_amp = float(np.max(np.abs(samples_int16)))
            if peak_amp == 0.0:
                logger.debug("Audio buffer is pure silence; returning empty text.")
                return STTResult(text="", confidence=1.0, is_final=True)

            self._ensure_model_loaded()

            if self._model is None:
                return STTResult(
                    text="",
                    confidence=0.0,
                    is_final=True,
                    error="faster-whisper model is not available or failed to load",
                )

            # Normalize audio samples to float32 [-1.0, 1.0] for Whisper
            audio_float32 = samples_int16.astype(np.float32) / 32768.0

            # Resample to 16000Hz if necessary
            if sample_rate != 16000:
                try:
                    from scipy import signal
                    num_target_samples = int(len(audio_float32) * 16000 / sample_rate)
                    audio_float32 = signal.resample(audio_float32, num_target_samples)
                except Exception as resample_err:
                    logger.debug("Scipy resampling failed: %s; proceeding without resampling.", resample_err)

            # Transcribe directly from in-memory numpy array without disk I/O
            segments, info = self._model.transcribe(
                audio_float32,
                beam_size=self.beam_size,
                best_of=self.beam_size,
                language="en",
                task="transcribe",
                initial_prompt=self.initial_prompt,
                condition_on_previous_text=False,
                vad_filter=False,  # External VAD is handled by Bimo VoiceService
            )

            # Concatenate segment texts
            transcript_parts = []
            segment_confidences = []
            for seg in segments:
                cleaned = seg.text.strip()
                if cleaned:
                    transcript_parts.append(cleaned)
                    # Convert log probability to approximate confidence (0.0 to 1.0)
                    if hasattr(seg, "avg_logprob") and seg.avg_logprob is not None:
                        conf = min(1.0, max(0.0, float(np.exp(seg.avg_logprob))))
                        segment_confidences.append(conf)

            full_text = " ".join(transcript_parts).strip()
            avg_confidence = (
                float(sum(segment_confidences) / len(segment_confidences))
                if segment_confidences
                else (1.0 if full_text else 0.0)
            )

            logger.info(
                "Whisper transcribed '%s' (Confidence: %.2f)",
                full_text,
                avg_confidence,
            )

            return STTResult(
                text=full_text,
                confidence=avg_confidence,
                is_final=True,
            )
        except Exception as exc:
            logger.error("Whisper transcription error: %s", exc)
            return STTResult(
                text="",
                confidence=0.0,
                is_final=True,
                error=str(exc),
            )


# Backwards compatibility alias
LocalWhisperSTT = WhisperSTT

