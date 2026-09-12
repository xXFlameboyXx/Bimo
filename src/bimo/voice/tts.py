"""Text-To-Speech (TTS) provider implementations for Bimo.

Provides:
- WindowsTTS: Live speech synthesis on Windows using Microsoft SAPI (SpVoice).
- MockTTS: Deterministic mock TTS for unit testing and offline development.
- RaspberryPiTTS: Hardware abstraction stub for future Pi deployment (ALSA / Piper / eSpeak).
"""

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import threading
import time
from typing import Any

from bimo.core.events import EventBus, EventType
from bimo.interfaces.devices import DeviceStatus
from bimo.interfaces.voice import BaseTextToSpeech

logger = logging.getLogger(__name__)

# Try importing pywin32 COM modules for native Windows SAPI TTS
try:
    import pythoncom
    import win32com.client
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    sd = None  # type: ignore
    HAS_SOUNDDEVICE = False

try:
    import piper
    from piper.voice import PiperVoice
    from piper.config import SynthesisConfig
    HAS_PIPER = True
except ImportError:
    piper = None  # type: ignore
    PiperVoice = None  # type: ignore
    SynthesisConfig = None  # type: ignore
    HAS_PIPER = False


class WindowsTTS(BaseTextToSpeech):
    """Windows Text-To-Speech implementation using native Microsoft SAPI.SpVoice.

    Features:
    - Zero external cloud dependencies or API keys.
    - Asynchronous playback in a dedicated worker thread (non-blocking).
    - Immediate speech cancellation/interruption support (purging buffers).
    - Configurable voice selection, speech rate, and volume.
    - Dispatches typed domain events (STARTED, FINISHED, ERROR, CANCELLED).
    """

    def __init__(
        self,
        id: str = "windows_tts",
        name: str = "Windows SAPI TTS",
        voice: str | None = None,
        speech_rate: int = 175,
        volume: float = 1.0,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            voice=voice,
            speech_rate=speech_rate,
            volume=volume,
            event_bus=event_bus,
        )
        self._playback_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._lock = threading.RLock()
        self._active_speaker: Any = None

    def connect(self) -> bool:
        """Verify audio output capability."""
        if not HAS_PYWIN32:
            logger.warning("pywin32 is not installed; falling back to PowerShell synthesis")
        self.set_status(DeviceStatus.ONLINE)
        return True

    def disconnect(self) -> None:
        """Stop any active speech and mark device offline."""
        self.stop()
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        """Check responsiveness of speech synthesis subsystem."""
        if not HAS_PYWIN32:
            return True
        try:
            pythoncom.CoInitialize()
            sp = win32com.client.Dispatch("SAPI.SpVoice")
            voices = sp.GetVoices()
            pythoncom.CoUninitialize()
            return len(voices) > 0
        except Exception:
            return False

    def get_available_voices(self) -> list[str]:
        """Return list of voice descriptions available on this Windows system."""
        voices: list[str] = []
        if not HAS_PYWIN32:
            return ["Default Windows Voice"]
        try:
            pythoncom.CoInitialize()
            sp = win32com.client.Dispatch("SAPI.SpVoice")
            for v in sp.GetVoices():
                voices.append(v.GetDescription())
            pythoncom.CoUninitialize()
        except Exception as exc:
            logger.warning("Failed to query SAPI voices: %s", exc)
        return voices

    def _convert_rate_to_sapi(self, rate_wpm: int) -> int:
        """Convert words-per-minute (approx 100-300) to SAPI rate (-10 to +10)."""
        sapi_val = int((rate_wpm - 175) / 12.5)
        return max(-10, min(10, sapi_val))

    def _convert_volume_to_sapi(self, vol: float) -> int:
        """Convert float volume (0.0 to 1.0) to SAPI volume (0 to 100)."""
        return max(0, min(100, int(vol * 100.0)))

    def _speak_worker(self, text: str, utterance_id: int) -> None:
        """Background thread worker for synthesizing and playing speech."""
        start_time = time.time()
        sapi_rate = self._convert_rate_to_sapi(self.speech_rate)
        sapi_volume = self._convert_volume_to_sapi(self.volume)

        try:
            if HAS_PYWIN32:
                pythoncom.CoInitialize()
                sp = win32com.client.Dispatch("SAPI.SpVoice")
                with self._lock:
                    if self._current_utterance_id != utterance_id:
                        pythoncom.CoUninitialize()
                        return
                    self._active_speaker = sp

                # Configure rate & volume
                sp.Rate = sapi_rate
                sp.Volume = sapi_volume

                # Configure voice if requested
                if self.voice:
                    for v in sp.GetVoices():
                        if self.voice.lower() in v.GetDescription().lower():
                            sp.Voice = v
                            break

                # Speak asynchronously inside worker thread using SAPI flag SVSFlagsAsync = 1
                sp.Speak(text, 1)

                # Monitor completion and cancellation
                while not self._cancel_event.is_set() and self._current_utterance_id == utterance_id:
                    if sp.WaitUntilDone(50):
                        break

                with self._lock:
                    self._active_speaker = None
                pythoncom.CoUninitialize()
            else:
                # Fallback to PowerShell speech synthesis if pywin32 is unavailable
                ps_cmd = (
                    f"Add-Type -AssemblyName System.Speech; "
                    f"$syn = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    f"$syn.Volume = {sapi_volume}; "
                    f"$syn.Speak([Console]::In.ReadToEnd())"
                )
                proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                proc.communicate(input=text)

            with self._lock:
                if self._current_utterance_id != utterance_id:
                    return

                self._is_speaking = False
                self.set_status(DeviceStatus.ONLINE)

            duration = time.time() - start_time
            logger.info("Speech playback finished in %.2fs: '%s'", duration, text[:40])
            self._publish_event(
                EventType.SPEECH_OUTPUT_FINISHED,
                {"text": text, "duration": duration},
            )
        except Exception as exc:
            logger.error("Error during speech synthesis: %s", exc)
            with self._lock:
                if self._current_utterance_id == utterance_id:
                    self._is_speaking = False
                    self.set_status(DeviceStatus.ONLINE)
            self._publish_event(
                EventType.SPEECH_OUTPUT_ERROR,
                {"text": text, "error": str(exc)},
            )

    def speak(self, text: str, block: bool = False) -> bool:
        """Synthesize and play speech.

        Args:
            text: Text to speak.
            block: If True, blocks synchronously until speech completes.
                   If False (default), returns immediately while audio plays.

        Returns:
            True if speech output started, False if text was empty or error occurred.
        """
        if not text or not text.strip():
            logger.warning("Empty or blank text passed to speak(); skipping.")
            return False

        with self._lock:
            # If already speaking, interrupt current speech before starting new one
            if self._is_speaking:
                logger.info("Interrupting previous speech playback for new utterance.")
                self.stop()

            self._current_utterance_id += 1
            utterance_id = self._current_utterance_id
            self._cancel_event.clear()
            self._is_speaking = True
            self.set_status(DeviceStatus.BUSY)

        # Publish started event immediately so state machine transitions to SPEAKING
        self._publish_event(
            EventType.SPEECH_OUTPUT_STARTED,
            {
                "text": text,
                "voice": self.voice,
                "rate": self.speech_rate,
                "volume": self.volume,
            },
        )

        if block:
            self._speak_worker(text, utterance_id)
            return True
        else:
            self._playback_thread = threading.Thread(
                target=self._speak_worker,
                args=(text, utterance_id),
                daemon=True,
                name="WindowsTTSPlayback",
            )
            self._playback_thread.start()
            return True

    def stop(self) -> None:
        """Immediately interrupt and stop any active speech playback."""
        with self._lock:
            if not self._is_speaking and self._active_speaker is None:
                return

            self._current_utterance_id += 1
            self._cancel_event.set()
            if self._active_speaker is not None:
                try:
                    # SVSFPurgeBeforeSpeak = 2 immediately purges active speech buffer
                    self._active_speaker.Speak("", 2)
                except Exception as exc:
                    logger.debug("Error while purging SAPI speech buffer: %s", exc)
                self._active_speaker = None

            self._is_speaking = False
            self.set_status(DeviceStatus.ONLINE)

        self._publish_event(
            EventType.SPEECH_OUTPUT_CANCELLED,
            {"text": "", "reason": "User interrupted"},
        )

        if self._playback_thread and self._playback_thread.is_alive():
            if threading.current_thread() != self._playback_thread:
                self._playback_thread.join(timeout=0.3)


class MockTTS(BaseTextToSpeech):
    """Deterministic mock text-to-speech provider for unit testing."""

    def __init__(
        self,
        id: str = "mock_tts",
        name: str = "Mock Text-To-Speech",
        voice: str | None = None,
        speech_rate: int = 175,
        volume: float = 1.0,
        event_bus: EventBus | None = None,
        simulated_duration: float = 0.0,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            voice=voice,
            speech_rate=speech_rate,
            volume=volume,
            event_bus=event_bus,
        )
        self.spoken_texts: list[str] = []
        self.speak_calls: list[str] = []
        self.stop_calls: int = 0
        self.simulated_error: str | None = None
        self.simulated_duration = simulated_duration
        self._cancel_event = threading.Event()
        self._playback_thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def connect(self) -> bool:
        self.set_status(DeviceStatus.ONLINE)
        return True

    def disconnect(self) -> None:
        self.stop()
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        return self.simulated_error is None

    def _worker(self, text: str, utterance_id: int) -> None:
        if self.simulated_duration > 0:
            elapsed = 0.0
            step = 0.02
            while elapsed < self.simulated_duration:
                if self._cancel_event.is_set() or self._current_utterance_id != utterance_id:
                    break
                time.sleep(min(step, self.simulated_duration - elapsed))
                elapsed += step

        with self._lock:
            if self._current_utterance_id != utterance_id:
                return

            cancelled = self._cancel_event.is_set()
            self._is_speaking = False
            self.set_status(DeviceStatus.ONLINE)

        if cancelled:
            self._publish_event(
                EventType.SPEECH_OUTPUT_CANCELLED,
                {"text": text, "reason": "Interrupted"},
            )
        else:
            self._publish_event(
                EventType.SPEECH_OUTPUT_FINISHED,
                {"text": text, "duration": self.simulated_duration},
            )

    def speak(self, text: str, block: bool = False) -> bool:
        if not text or not text.strip():
            return False

        with self._lock:
            if self._is_speaking:
                self.stop()

            self._current_utterance_id += 1
            utterance_id = self._current_utterance_id
            self.speak_calls.append(text)
            self._cancel_event.clear()

            if self.simulated_error:
                self._publish_event(
                    EventType.SPEECH_OUTPUT_ERROR,
                    {"text": text, "error": self.simulated_error},
                )
                return False

            self.spoken_texts.append(text)
            self._is_speaking = True
            self.set_status(DeviceStatus.BUSY)

        self._publish_event(
            EventType.SPEECH_OUTPUT_STARTED,
            {
                "text": text,
                "voice": self.voice,
                "rate": self.speech_rate,
                "volume": self.volume,
            },
        )

        if block or self.simulated_duration == 0.0:
            self._worker(text, utterance_id)
            return True
        else:
            self._playback_thread = threading.Thread(
                target=self._worker, args=(text, utterance_id), daemon=True, name="MockTTSWorker"
            )
            self._playback_thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self.stop_calls += 1
            if not self._is_speaking:
                return

            self._current_utterance_id += 1
            self._cancel_event.set()
            self._is_speaking = False
            self.set_status(DeviceStatus.ONLINE)

        self._publish_event(
            EventType.SPEECH_OUTPUT_CANCELLED,
            {"text": self.spoken_texts[-1] if self.spoken_texts else "", "reason": "Stopped"},
        )

        if self._playback_thread and self._playback_thread.is_alive():
            if threading.current_thread() != self._playback_thread:
                self._playback_thread.join(timeout=0.2)


class RaspberryPiTTS(BaseTextToSpeech):
    """Hardware abstraction stub for future Raspberry Pi speaker / audio deployment.

    Designed to interface with:
    - ALSA hardware playback devices (`hw:0,0`, `plughw:1,0`)
    - On-device local neural TTS engines (Piper / Mimic3 / eSpeak-ng)
    - I2S MAX98357A or USB DAC speaker amplifiers
    """

    def __init__(
        self,
        id: str = "rpi_tts",
        name: str = "Raspberry Pi TTS",
        alsa_device: str = "default",
        engine: str = "piper",
        voice: str | None = None,
        speech_rate: int = 175,
        volume: float = 1.0,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            voice=voice,
            speech_rate=speech_rate,
            volume=volume,
            event_bus=event_bus,
        )
        self.alsa_device = alsa_device
        self.engine = engine
        self._cancel_event = threading.Event()
        self._playback_thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def connect(self) -> bool:
        """Verify ALSA audio output device accessibility."""
        logger.info(
            "Connecting to Raspberry Pi audio output (ALSA device: %s, Engine: %s)",
            self.alsa_device,
            self.engine,
        )
        self.set_status(DeviceStatus.ONLINE)
        return True

    def disconnect(self) -> None:
        self.stop()
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        return self.status != DeviceStatus.ERROR

    def _worker(self, text: str, utterance_id: int) -> None:
        start_time = time.time()
        try:
            # Future hardware execution: subprocess.run(["piper", ...]) or aplay
            logger.info("Raspberry Pi TTS synthesizing on %s: '%s'", self.alsa_device, text[:40])
            time.sleep(0.05)  # Simulation stub

            with self._lock:
                if self._current_utterance_id != utterance_id:
                    return

                cancelled = self._cancel_event.is_set()
                self._is_speaking = False
                self.set_status(DeviceStatus.ONLINE)

            if cancelled:
                self._publish_event(
                    EventType.SPEECH_OUTPUT_CANCELLED,
                    {"text": text, "reason": "Interrupted"},
                )
            else:
                self._publish_event(
                    EventType.SPEECH_OUTPUT_FINISHED,
                    {"text": text, "duration": time.time() - start_time},
                )
        except Exception as exc:
            with self._lock:
                if self._current_utterance_id == utterance_id:
                    self._is_speaking = False
                    self.set_status(DeviceStatus.ERROR)
            self._publish_event(
                EventType.SPEECH_OUTPUT_ERROR,
                {"text": text, "error": str(exc)},
            )

    def speak(self, text: str, block: bool = False) -> bool:
        if not text or not text.strip():
            return False

        with self._lock:
            if self._is_speaking:
                self.stop()

            self._current_utterance_id += 1
            utterance_id = self._current_utterance_id
            self._cancel_event.clear()
            self._is_speaking = True
            self.set_status(DeviceStatus.BUSY)

        self._publish_event(
            EventType.SPEECH_OUTPUT_STARTED,
            {
                "text": text,
                "voice": self.voice,
                "rate": self.speech_rate,
                "volume": self.volume,
                "device": self.alsa_device,
            },
        )

        if block:
            self._worker(text, utterance_id)
            return True
        else:
            self._playback_thread = threading.Thread(
                target=self._worker, args=(text, utterance_id), daemon=True, name="RaspberryPiTTSWorker"
            )
            self._playback_thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            if not self._is_speaking:
                return
            self._current_utterance_id += 1
            self._cancel_event.set()
            self._is_speaking = False
            self.set_status(DeviceStatus.ONLINE)

        self._publish_event(
            EventType.SPEECH_OUTPUT_CANCELLED,
            {"text": "", "reason": "Stopped"},
        )

        if self._playback_thread and self._playback_thread.is_alive():
            if threading.current_thread() != self._playback_thread:
                self._playback_thread.join(timeout=0.2)


class PiperTTS(BaseTextToSpeech):
    """Local neural Text-To-Speech implementation using Piper.

    Features:
    - High-quality, low-latency on-device neural synthesis.
    - Selected default voice: `en_GB-semaine-medium`.
    - Zero external cloud dependencies or API keys.
    - Asynchronous playback in a worker thread (non-blocking).
    - Plays synthesized audio through PC speakers (sounddevice).
    - Immediate thread-safe cancellation / barge-in support.
    - Configurable model path, config path, speech rate, and volume.
    - Dispatches typed domain events (STARTED, FINISHED, ERROR, CANCELLED).
    """

    DEFAULT_VOICE_NAME = "en_GB-semaine-medium"
    DEFAULT_MODEL_FILE = "models/piper/en_GB-semaine-medium.onnx"
    DEFAULT_CONFIG_FILE = "models/piper/en_GB-semaine-medium.onnx.json"

    def __init__(
        self,
        id: str = "piper_tts",
        name: str = "Piper Neural TTS",
        model_path: str | None = None,
        config_path: str | None = None,
        voice: str | None = None,
        speech_rate: int = 175,
        volume: float = 1.0,
        output_device: str | int | None = None,
        sample_rate: int = 22050,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            voice=voice or self.DEFAULT_VOICE_NAME,
            speech_rate=speech_rate,
            volume=volume,
            event_bus=event_bus,
        )
        self.model_path = model_path
        self.config_path = config_path
        self.output_device = output_device
        self.sample_rate = sample_rate

        self._piper_voice: Any = None
        self._playback_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._lock = threading.RLock()
        self._is_initialized = False

        self._initialize_piper()

    def _resolve_paths(self) -> tuple[str, str]:
        """Resolve model and config paths with sensible project defaults."""
        m_path = self.model_path or self.DEFAULT_MODEL_FILE
        c_path = self.config_path or (
            self.DEFAULT_CONFIG_FILE if not self.model_path else f"{self.model_path}.json"
        )
        return m_path, c_path

    def _initialize_piper(self) -> None:
        """Initialize the Piper voice engine with local model weights."""
        if not HAS_PIPER:
            logger.warning("piper-tts package is not available. PiperTTS will remain uninitialized.")
            self.set_status(DeviceStatus.ERROR)
            return

        m_path, c_path = self._resolve_paths()

        if not os.path.exists(m_path):
            logger.warning(
                "Piper model file not found at '%s'. PiperTTS will be offline until model is supplied.",
                m_path,
            )
            self.set_status(DeviceStatus.OFFLINE)
            return

        try:
            logger.info("Loading Piper voice model from '%s'", m_path)
            self._piper_voice = PiperVoice.load(
                m_path,
                config_path=c_path if os.path.exists(c_path) else None,
            )
            if hasattr(self._piper_voice, "config") and hasattr(self._piper_voice.config, "sample_rate"):
                self.sample_rate = self._piper_voice.config.sample_rate
            self._is_initialized = True
            self.set_status(DeviceStatus.ONLINE)
            logger.info(
                "PiperTTS initialized successfully (voice: %s, sample_rate: %d)",
                self.voice,
                self.sample_rate,
            )
        except Exception as exc:
            logger.error("Failed to initialize Piper voice model: %s", exc)
            self._is_initialized = False
            self.set_status(DeviceStatus.ERROR)
            self._publish_event(
                EventType.SPEECH_OUTPUT_ERROR,
                {"text": "", "error": f"Piper init failure: {exc}"},
            )

    def connect(self) -> bool:
        if not self._is_initialized:
            self._initialize_piper()
        return self.status != DeviceStatus.ERROR

    def disconnect(self) -> None:
        self.stop()
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        return self._is_initialized and self.status != DeviceStatus.ERROR

    def _worker(self, text: str, utterance_id: int) -> None:
        start_time = time.time()
        try:
            if not self._is_initialized or self._piper_voice is None:
                raise RuntimeError("Piper voice is not initialized or model file missing")

            # Length scale inversely proportional to speech rate (175 wpm is 1.0)
            length_scale = 175.0 / max(50.0, float(self.speech_rate))
            syn_config = None
            if SynthesisConfig is not None:
                syn_config = SynthesisConfig(length_scale=length_scale)

            # Synthesize text into audio chunks
            chunks = list(self._piper_voice.synthesize(text, syn_config=syn_config))
            if not chunks:
                logger.warning("Piper produced no audio chunks for text: '%s'", text)
                self._handle_completion(text, utterance_id, start_time)
                return

            audio_arrays = []
            for c in chunks:
                if hasattr(c, "audio_float_array") and c.audio_float_array is not None:
                    audio_arrays.append(c.audio_float_array)
                elif hasattr(c, "_audio_int16_array") and c._audio_int16_array is not None:
                    audio_arrays.append(c._audio_int16_array.astype(np.float32) / 32768.0)

            if not audio_arrays or np is None:
                self._handle_completion(text, utterance_id, start_time)
                return

            full_audio = np.concatenate(audio_arrays)
            if self.volume != 1.0:
                full_audio = full_audio * self.volume

            if self._cancel_event.is_set():
                self._handle_completion(text, utterance_id, start_time)
                return

            # Audio playback via sounddevice if available
            if HAS_SOUNDDEVICE and sd is not None:
                device = self.output_device if self.output_device != "default" else None
                try:
                    sd.play(full_audio, samplerate=self.sample_rate, device=device)
                    duration = len(full_audio) / float(self.sample_rate)
                    elapsed = 0.0
                    while elapsed < duration and not self._cancel_event.is_set():
                        step = min(0.05, duration - elapsed)
                        time.sleep(step)
                        elapsed += step

                    if self._cancel_event.is_set():
                        sd.stop()
                except Exception as play_err:
                    logger.warning("sounddevice playback exception (%s); falling back to timed simulation", play_err)
                    duration = len(full_audio) / float(self.sample_rate)
                    time.sleep(min(0.05, duration))
            else:
                duration = len(full_audio) / float(self.sample_rate)
                time.sleep(min(0.05, duration))

            self._handle_completion(text, utterance_id, start_time)

        except Exception as exc:
            logger.error("Piper synthesis error: %s", exc)
            with self._lock:
                if self._current_utterance_id == utterance_id:
                    self._is_speaking = False
                    self.set_status(DeviceStatus.ERROR)
            self._publish_event(
                EventType.SPEECH_OUTPUT_ERROR,
                {"text": text, "error": str(exc)},
            )

    def _handle_completion(self, text: str, utterance_id: int, start_time: float) -> None:
        with self._lock:
            if self._current_utterance_id != utterance_id:
                return

            cancelled = self._cancel_event.is_set()
            self._is_speaking = False
            self.set_status(DeviceStatus.ONLINE)

        if cancelled:
            logger.info("Speech output cancelled for utterance: '%s'", text[:30])
            self._publish_event(
                EventType.SPEECH_OUTPUT_CANCELLED,
                {"text": text, "reason": "Interrupted"},
            )
        else:
            duration = time.time() - start_time
            logger.info("Speech output finished (%0.2fs): '%s'", duration, text[:30])
            self._publish_event(
                EventType.SPEECH_OUTPUT_FINISHED,
                {"text": text, "duration": duration},
            )

    def speak(self, text: str, block: bool = False) -> bool:
        if not text or not text.strip():
            logger.debug("Empty or blank text passed to speak(); skipping.")
            return False

        clean_text = text.strip()

        with self._lock:
            if self._is_speaking:
                self.stop()

            self._current_utterance_id += 1
            utterance_id = self._current_utterance_id
            self._cancel_event.clear()
            self._is_speaking = True
            self.set_status(DeviceStatus.BUSY)

        self._publish_event(
            EventType.SPEECH_OUTPUT_STARTED,
            {
                "text": clean_text,
                "voice": self.voice,
                "rate": self.speech_rate,
                "volume": self.volume,
            },
        )

        if block:
            self._worker(clean_text, utterance_id)
            return True
        else:
            self._playback_thread = threading.Thread(
                target=self._worker,
                args=(clean_text, utterance_id),
                name="PiperTTSWorker",
                daemon=True,
            )
            self._playback_thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            if not self._is_speaking:
                return
            self._current_utterance_id += 1
            self._cancel_event.set()
            self._is_speaking = False
            self.set_status(DeviceStatus.ONLINE)

        if HAS_SOUNDDEVICE and sd is not None:
            try:
                sd.stop()
            except Exception as e:
                logger.debug("sounddevice.stop() exception: %s", e)

        self._publish_event(
            EventType.SPEECH_OUTPUT_CANCELLED,
            {"text": "", "reason": "Stopped"},
        )

        if self._playback_thread and self._playback_thread.is_alive():
            if threading.current_thread() != self._playback_thread:
                self._playback_thread.join(timeout=0.2)

