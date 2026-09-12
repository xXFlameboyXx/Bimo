"""Microphone implementations for Bimo.

Provides:
- BaseMicrophone: Abstract hardware-independent microphone interface.
- PCMicrophone: Live audio capture using the development PC's microphone (Windows/Linux/Mac).
- MockMicrophone: Controllable synthetic audio source for automated testing.
- RaspberryPiMicrophone: ALSA / I2S microphone interface stub for future Pi hardware integration.
"""

from __future__ import annotations

import logging
import math
import queue
import struct
import time
from typing import Any

from bimo.interfaces.devices import DeviceStatus
from bimo.interfaces.voice import BaseMicrophone

logger = logging.getLogger(__name__)

# Try importing sounddevice for live PC microphone capture
try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False


class PCMicrophone(BaseMicrophone):
    """Live microphone audio capture using PC sound input devices.

    Captures 16-bit mono PCM audio chunks using sounddevice into an internal
    thread-safe queue.
    """

    def __init__(
        self,
        id: str = "pc_microphone",
        name: str = "PC Development Microphone",
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
        device_index: int | str | None = None,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            sample_rate=sample_rate,
            channels=channels,
            chunk_size=chunk_size,
        )
        self.device_index = device_index
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
        self._stream: Any = None

    def connect(self) -> bool:
        """Verify that audio hardware is accessible."""
        if not HAS_SOUNDDEVICE:
            logger.error("sounddevice library is not installed")
            self.set_status(DeviceStatus.ERROR)
            return False

        try:
            device_info = sd.query_devices(self.device_index, "input")
            logger.info(
                "Connected to PC microphone: %s (Max input channels: %s)",
                device_info.get("name", "Unknown"),
                device_info.get("max_input_channels", 0),
            )
            self.set_status(DeviceStatus.ONLINE)
            return True
        except Exception as e:
            logger.error("Failed to connect to PC microphone: %s", e)
            self.set_status(DeviceStatus.ERROR)
            return False

    def disconnect(self) -> None:
        """Stop stream and disconnect audio hardware."""
        self.stop_stream()
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        """Verify device responsiveness."""
        if not HAS_SOUNDDEVICE:
            return False
        try:
            sd.query_devices(self.device_index, "input")
            return True
        except Exception:
            return False

    def _audio_callback(
        self, indata: Any, frames: int, time_info: Any, status: Any
    ) -> None:
        """Callback invoked by audio backend on each newly captured buffer."""
        if status:
            logger.warning("Microphone stream status: %s", status)

        # Convert to 16-bit signed integer PCM bytes
        try:
            raw_bytes = indata.tobytes()
            # Drop oldest chunks if queue is full to prevent memory explosion
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
            self._queue.put_nowait(raw_bytes)
        except Exception as e:
            logger.error("Error in audio callback: %s", e)

    def start_stream(self) -> None:
        """Start capturing audio stream from PC microphone."""
        if self._is_recording:
            return

        if not HAS_SOUNDDEVICE:
            raise RuntimeError("sounddevice is required for PCMicrophone audio capture")

        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=self.chunk_size,
                device=self.device_index,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._is_recording = True
            self.set_status(DeviceStatus.BUSY)
            logger.info(
                "PCMicrophone stream started (%d Hz, %d ch, chunk=%d)",
                self.sample_rate,
                self.channels,
                self.chunk_size,
            )
        except Exception as e:
            self.set_status(DeviceStatus.ERROR)
            logger.error("Failed to start PC microphone stream: %s", e)
            raise

    def stop_stream(self) -> None:
        """Stop capturing audio stream and flush buffers."""
        if not self._is_recording:
            return

        self._is_recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.debug("Error stopping audio stream: %s", e)
            self._stream = None

        self.set_status(DeviceStatus.ONLINE)
        logger.info("PCMicrophone stream stopped.")

    def read_chunk(self, timeout: float | None = None) -> bytes | None:
        """Read a single raw 16-bit PCM chunk from the audio stream buffer."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self) -> None:
        """Discard any accumulated buffered audio chunks to sync with real time."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


class MockMicrophone(BaseMicrophone):
    """Deterministic, mock microphone implementation for automated testing.

    Allows tests to feed synthetic audio chunks, silence, tones, or simulate
    hardware errors without requiring physical audio hardware.
    """

    def __init__(
        self,
        id: str = "mock_microphone",
        name: str = "Mock Audio Microphone",
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            sample_rate=sample_rate,
            channels=channels,
            chunk_size=chunk_size,
        )
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._simulated_error: str | None = None

    def connect(self) -> bool:
        self.set_status(DeviceStatus.ONLINE)
        return True

    def disconnect(self) -> None:
        self.stop_stream()
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        return self._simulated_error is None

    def start_stream(self) -> None:
        if self._simulated_error:
            self.set_status(DeviceStatus.ERROR)
            raise RuntimeError(self._simulated_error)
        self._is_recording = True
        self.set_status(DeviceStatus.BUSY)

    def stop_stream(self) -> None:
        self._is_recording = False
        self.set_status(DeviceStatus.ONLINE)

    def read_chunk(self, timeout: float | None = None) -> bytes | None:
        if self._simulated_error:
            raise RuntimeError(self._simulated_error)
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self) -> None:
        """Discard any accumulated buffered chunks."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def simulate_error(self, error_message: str | None = "Hardware I/O error") -> None:
        """Configure mock microphone to simulate a hardware fault."""
        self._simulated_error = error_message
        if error_message:
            self.set_status(DeviceStatus.ERROR)

    def feed_audio(self, pcm_bytes: bytes) -> None:
        """Feed raw PCM bytes into the microphone buffer in chunk_size packets."""
        bytes_per_chunk = self.chunk_size * 2  # 16-bit = 2 bytes/sample
        for i in range(0, len(pcm_bytes), bytes_per_chunk):
            chunk = pcm_bytes[i : i + bytes_per_chunk]
            if len(chunk) < bytes_per_chunk:
                chunk = chunk + bytes(bytes_per_chunk - len(chunk))
            self._queue.put(chunk)

    def feed_silence(self, duration_seconds: float = 1.0) -> None:
        """Inject pure zero-amplitude silence into the stream buffer."""
        total_samples = int(self.sample_rate * duration_seconds)
        total_bytes = total_samples * 2
        silence = bytes(total_bytes)
        self.feed_audio(silence)

    def feed_tone(
        self, frequency: float = 440.0, duration_seconds: float = 1.0, amplitude: float = 0.5
    ) -> None:
        """Inject a synthetic sine-wave tone into the stream buffer."""
        total_samples = int(self.sample_rate * duration_seconds)
        raw_samples = []
        max_int16 = 32767
        for i in range(total_samples):
            t = float(i) / self.sample_rate
            val = int(amplitude * max_int16 * math.sin(2.0 * math.pi * frequency * t))
            raw_samples.append(max(-32768, min(32767, val)))

        pcm_bytes = struct.pack(f"<{len(raw_samples)}h", *raw_samples)
        self.feed_audio(pcm_bytes)

    def clear(self) -> None:
        """Clear all queued audio buffers."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


class RaspberryPiMicrophone(BaseMicrophone):
    """Microphone implementation for Raspberry Pi physical hardware.

    Designed for future I2S (e.g., INMP441, SPH0645) or USB microphone devices.
    Currently acts as an interface contract and stub on development machines.
    """

    def __init__(
        self,
        id: str = "rpi_microphone",
        name: str = "Raspberry Pi I2S/USB Microphone",
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
        alsa_device: str = "default",
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            sample_rate=sample_rate,
            channels=channels,
            chunk_size=chunk_size,
        )
        self.alsa_device = alsa_device
        self._queue: queue.Queue[bytes] = queue.Queue()

    def connect(self) -> bool:
        """Establish connection with Raspberry Pi ALSA/I2S hardware."""
        logger.info(
            "RaspberryPiMicrophone stub connecting to ALSA device: %s", self.alsa_device
        )
        self.set_status(DeviceStatus.ONLINE)
        return True

    def disconnect(self) -> None:
        self.stop_stream()
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        return self.status != DeviceStatus.ERROR

    def start_stream(self) -> None:
        logger.info("Starting RaspberryPiMicrophone stream on device: %s", self.alsa_device)
        self._is_recording = True
        self.set_status(DeviceStatus.BUSY)

    def stop_stream(self) -> None:
        logger.info("Stopping RaspberryPiMicrophone stream")
        self._is_recording = False
        self.set_status(DeviceStatus.ONLINE)

    def read_chunk(self, timeout: float | None = None) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
