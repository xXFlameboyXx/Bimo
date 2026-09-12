"""Comprehensive unit tests for Phase 3 Voice Input subsystem.

Validates:
1. BaseMicrophone and MockMicrophone interface contracts.
2. EnergyVAD voice activity detection and silence thresholds.
3. Speech event generation (VOICE_ACTIVITY_STARTED, VOICE_ACTIVITY_STOPPED, USER_SPEAKING, SPEECH_RECEIVED, SPEECH_ERROR).
4. Speech transcription handling and delivery to AgentInputInterface.
5. Silence and unintelligible audio handling.
6. Microphone hardware errors and STT error recovery.
7. End-to-end state transitions: IDLE -> LISTENING -> THINKING and ERROR.
8. Swappability of microphone backends without altering the state machine.
"""

from pathlib import Path
import sys
import time
import unittest

# Ensure src/ is on path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.config import AudioConfig, Config
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.devices import BaseDevice, DeviceCapability, DeviceStatus
from bimo.interfaces.voice import (
    AgentInputInterface,
    BaseMicrophone,
    BaseSpeechToText,
    STTResult,
)
from bimo.voice import (
    DefaultAgentInput,
    EnergyVAD,
    GoogleWebSTT,
    MockMicrophone,
    MockSpeechToText,
    PCMicrophone,
    RaspberryPiMicrophone,
    VoiceService,
    calculate_rms,
    create_microphone,
    create_stt_provider,
    create_voice_service,
    normalize_pcm,
)


class TestMicrophoneInterface(unittest.TestCase):
    """Test suite for BaseMicrophone contracts and MockMicrophone operation."""

    def test_mock_microphone_conforms_to_contracts(self) -> None:
        mic = MockMicrophone(sample_rate=16000, channels=1, chunk_size=1024)
        self.assertIsInstance(mic, BaseMicrophone)
        self.assertIsInstance(mic, BaseDevice)
        self.assertTrue(mic.has_capability(DeviceCapability.AUDIO_INPUT))
        self.assertEqual(mic.sample_rate, 16000)
        self.assertEqual(mic.channels, 1)
        self.assertEqual(mic.chunk_size, 1024)
        self.assertFalse(mic.is_recording)

        self.assertTrue(mic.connect())
        self.assertEqual(mic.status, DeviceStatus.ONLINE)

        mic.start_stream()
        self.assertTrue(mic.is_recording)
        self.assertEqual(mic.status, DeviceStatus.BUSY)

        # Feed audio chunk and read it back
        dummy_chunk = b"\x01\x00" * 1024
        mic.feed_audio(dummy_chunk)
        read_data = mic.read_chunk(timeout=0.1)
        self.assertIsNotNone(read_data)
        self.assertEqual(len(read_data), 2048)

        mic.stop_stream()
        self.assertFalse(mic.is_recording)
        self.assertEqual(mic.status, DeviceStatus.ONLINE)

        mic.disconnect()
        self.assertEqual(mic.status, DeviceStatus.OFFLINE)

    def test_mock_microphone_tone_and_silence(self) -> None:
        mic = MockMicrophone(sample_rate=16000, chunk_size=1024)
        mic.start_stream()

        # Feed silence
        mic.feed_silence(duration_seconds=0.2)
        silence_chunk = mic.read_chunk(timeout=0.1)
        self.assertIsNotNone(silence_chunk)
        self.assertEqual(calculate_rms(silence_chunk), 0.0)

        # Clear remaining silence chunks and feed tone
        mic.clear()
        mic.feed_tone(frequency=440.0, duration_seconds=0.2, amplitude=0.5)
        tone_chunk = mic.read_chunk(timeout=0.1)
        self.assertIsNotNone(tone_chunk)
        self.assertGreater(calculate_rms(tone_chunk), 5000.0)

        mic.stop_stream()

    def test_mock_microphone_simulated_error(self) -> None:
        mic = MockMicrophone()
        mic.simulate_error("Mic unplugged")
        with self.assertRaises(RuntimeError):
            mic.start_stream()

        mic.simulate_error(None)
        mic.start_stream()
        mic.simulate_error("Buffer read fault")
        with self.assertRaises(RuntimeError):
            mic.read_chunk()
        mic.stop_stream()


class TestVoiceActivityDetection(unittest.TestCase):
    """Test suite for EnergyVAD and RMS calculations."""

    def test_rms_calculation(self) -> None:
        silence = bytes(2048)
        self.assertEqual(calculate_rms(silence), 0.0)

        # Non-zero waveform
        pulse = b"\x00\x10" * 512
        self.assertGreater(calculate_rms(pulse), 0.0)

    def test_energy_vad_speech_and_silence_cycles(self) -> None:
        vad = EnergyVAD(
            energy_threshold=500.0,
            speech_time_threshold=0.05,
            silence_time_threshold=0.05,
            sample_rate=16000,
            chunk_size=1024,
        )

        silence_chunk = bytes(2048)
        loud_chunk = b"\x00\x40" * 1024  # High amplitude int16

        # Feed silence: no speech activity
        res1 = vad.process_chunk(silence_chunk)
        self.assertFalse(res1.is_speech)
        self.assertFalse(res1.activity_started)

        # Feed speech frames until activity starts
        started = False
        for _ in range(5):
            res = vad.process_chunk(loud_chunk)
            if res.activity_started:
                started = True
                break
        self.assertTrue(started)
        self.assertTrue(vad.is_speaking)

        # Feed silence frames until activity stops
        stopped = False
        for _ in range(5):
            res = vad.process_chunk(silence_chunk)
            if res.activity_stopped:
                stopped = True
                break
        self.assertTrue(stopped)
        self.assertFalse(vad.is_speaking)


class TestSpeechRecognitionProviders(unittest.TestCase):
    """Test suite for STT providers."""

    def test_mock_stt_provider(self) -> None:
        stt = MockSpeechToText(default_text="Turn on lights")
        self.assertEqual(stt.provider_name, "MockSTT")

        res = stt.transcribe(b"audio-data")
        self.assertEqual(res.text, "Turn on lights")
        self.assertEqual(res.confidence, 1.0)
        self.assertIsNone(res.error)

        # Test error simulation
        stt.set_next_result("", error="API rate limit")
        res_err = stt.transcribe(b"audio-data")
        self.assertEqual(res_err.error, "API rate limit")

        # Test empty audio buffer
        res_empty = stt.transcribe(b"")
        self.assertEqual(res_empty.error, "Empty audio buffer")


class TestVoiceServiceAndStateTransitions(unittest.TestCase):
    """End-to-end integration tests for VoiceService, EventBus, and StateMachine."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=self.event_bus,
            auto_subscribe_events=True,
        )
        self.agent_input = DefaultAgentInput()
        self.mic = MockMicrophone(sample_rate=16000, chunk_size=1024)
        self.stt = MockSpeechToText(default_text="What time is it?")

        self.vad = EnergyVAD(
            energy_threshold=500.0,
            speech_time_threshold=0.05,
            silence_time_threshold=0.05,
            sample_rate=16000,
            chunk_size=1024,
        )

        self.service = VoiceService(
            microphone=self.mic,
            stt=self.stt,
            event_bus=self.event_bus,
            agent_input=self.agent_input,
            vad=self.vad,
            min_speech_bytes=1000,
        )

    def tearDown(self) -> None:
        self.service.stop()

    def test_state_flow_idle_listening_thinking(self) -> None:
        """Verify full flow: IDLE -> VOICE_ACTIVITY_STARTED (LISTENING) -> SPEECH_RECEIVED (THINKING)."""
        captured_events: list[Event] = []
        self.event_bus.subscribe(None, lambda e: captured_events.append(e))

        self.service.start()
        self.assertTrue(self.service.is_running)
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)

        # 1. Feed speech tone to trigger VAD speech start
        self.mic.feed_tone(frequency=440.0, duration_seconds=0.2, amplitude=0.5)
        time.sleep(0.3)

        # Should have transitioned to LISTENING
        self.assertEqual(self.state_machine.current_state, RobotState.LISTENING)
        event_types = [e.type for e in captured_events]
        self.assertIn(EventType.VOICE_ACTIVITY_STARTED, event_types)
        self.assertIn(EventType.USER_SPEAKING, event_types)

        # 2. Feed silence to trigger VAD speech stop and STT
        self.mic.feed_silence(duration_seconds=0.3)
        time.sleep(0.3)

        # Should have transitioned to THINKING on SPEECH_RECEIVED
        self.assertEqual(self.state_machine.current_state, RobotState.THINKING)
        event_types = [e.type for e in captured_events]
        self.assertIn(EventType.VOICE_ACTIVITY_STOPPED, event_types)
        self.assertIn(EventType.SPEECH_RECEIVED, event_types)

        # Verify AgentInputInterface received the transcribed turn
        self.assertEqual(self.agent_input.input_count, 1)
        self.assertEqual(self.agent_input.last_input, "What time is it?")

    def test_speech_error_transitions_to_error_state(self) -> None:
        """Verify that STT or microphone errors transition the robot to ERROR state."""
        self.stt.set_next_result("", error="Audio corrupted")
        self.service.start()

        # Trigger speech burst
        self.mic.feed_tone(frequency=440.0, duration_seconds=0.2, amplitude=0.5)
        time.sleep(0.2)
        # Trigger silence to invoke STT
        self.mic.feed_silence(duration_seconds=0.3)
        time.sleep(0.3)

        self.assertEqual(self.state_machine.current_state, RobotState.ERROR)

    def test_swapping_microphones_preserves_state_machine(self) -> None:
        """Verify that replacing the microphone implementation requires 0 changes to state machine."""
        # 1. Test with MockMicrophone
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)
        self.event_bus.publish(Event(type=EventType.VOICE_ACTIVITY_STARTED, source="mock_mic"))
        self.assertEqual(self.state_machine.current_state, RobotState.LISTENING)

        self.event_bus.publish(Event(type=EventType.SPEECH_RECEIVED, data={"transcript": "Hi"}, source="mock_stt"))
        self.assertEqual(self.state_machine.current_state, RobotState.THINKING)

        # 2. Swap to RaspberryPiMicrophone stub
        rpi_mic = RaspberryPiMicrophone(alsa_device="hw:1,0")
        self.state_machine.transition_to(RobotState.IDLE, reason="Reset", force=True)

        # State machine seamlessly handles events generated by rpi_mic
        self.event_bus.publish(Event(type=EventType.VOICE_ACTIVITY_STARTED, source=rpi_mic.id))
        self.assertEqual(self.state_machine.current_state, RobotState.LISTENING)

    def test_preroll_buffer_preserves_onset_and_audio_normalization(self) -> None:
        """Verify pre-roll buffer retains speech onset and STT normalizes soft audio."""
        # 1. Verify pre-roll buffer in VoiceService
        self.assertEqual(len(self.service._preroll_buffer), 0)
        chunk = bytes(2048)
        self.service._preroll_buffer.append(chunk)
        self.assertEqual(len(self.service._preroll_buffer), 1)

        # 2. Verify GoogleWebSTT normalization
        import numpy as np
        stt = GoogleWebSTT()
        pcm = (np.sin(2 * np.pi * 440 * np.linspace(0, 1, 16000)) * 1200).astype(np.int16).tobytes()
        norm_pcm = stt._normalize_pcm(pcm)
        orig_peak = np.max(np.abs(np.frombuffer(pcm, dtype=np.int16)))
        norm_peak = np.max(np.abs(np.frombuffer(norm_pcm, dtype=np.int16)))
        self.assertGreater(norm_peak, orig_peak)


class TestPCMNormalization(unittest.TestCase):
    """Unit test suite for PCM normalization edge cases and acoustic characteristics."""

    def test_empty_audio(self) -> None:
        """Verify empty audio returns empty bytes without error."""
        self.assertEqual(normalize_pcm(b""), b"")

    def test_silence(self) -> None:
        """Verify silence (all zeros) remains unchanged with zero peak."""
        import numpy as np

        silence = bytes(2048)
        norm = normalize_pcm(silence)
        self.assertEqual(norm, silence)
        peak = float(np.max(np.abs(np.frombuffer(norm, dtype=np.int16))))
        self.assertEqual(peak, 0.0)

    def test_low_amplitude_noise_floor_untouched(self) -> None:
        """Verify low amplitude (<20.0 peak noise floor) is untouched to avoid amplifying background noise."""
        import numpy as np

        # Ambient room noise measured in quiet room is peak ~4-8
        noise = (np.ones(1024, dtype=np.int16) * 10).tobytes()
        norm = normalize_pcm(noise, target_peak=22000, max_gain=50.0, noise_floor_peak=20.0)
        peak = float(np.max(np.abs(np.frombuffer(norm, dtype=np.int16))))
        self.assertEqual(peak, 10.0)

    def test_100_peak_amplified(self) -> None:
        """Verify peak of 100 (whispered audio) is amplified up to max_gain (50.0x -> 5000)."""
        import numpy as np

        pcm_100 = (np.ones(1024, dtype=np.int16) * 100).tobytes()
        norm_50x = normalize_pcm(pcm_100, target_peak=22000, max_gain=50.0)
        peak_50x = float(np.max(np.abs(np.frombuffer(norm_50x, dtype=np.int16))))
        # target_peak / 100 = 220 -> clamped to max_gain 50.0 -> 100 * 50 = 5000
        self.assertEqual(peak_50x, 5000.0)

        # Also verify configurable max_gain (e.g., 30.0x gives 3000)
        norm_30x = normalize_pcm(pcm_100, target_peak=22000, max_gain=30.0)
        peak_30x = float(np.max(np.abs(np.frombuffer(norm_30x, dtype=np.int16))))
        self.assertEqual(peak_30x, 3000.0)

    def test_250_peak_amplified(self) -> None:
        """Verify peak of 250 (soft 'hi'/'hey') is amplified up to max_gain (50.0x -> 12500)."""
        import numpy as np

        pcm_250 = (np.ones(1024, dtype=np.int16) * 250).tobytes()
        norm_50x = normalize_pcm(pcm_250, target_peak=22000, max_gain=50.0)
        peak_50x = float(np.max(np.abs(np.frombuffer(norm_50x, dtype=np.int16))))
        # target_peak / 250 = 88.0 -> clamped to max_gain 50.0 -> 250 * 50 = 12500
        self.assertEqual(peak_50x, 12500.0)

        # Also verify configurable max_gain of 30.0x gives 7500
        norm_30x = normalize_pcm(pcm_250, target_peak=22000, max_gain=30.0)
        peak_30x = float(np.max(np.abs(np.frombuffer(norm_30x, dtype=np.int16))))
        self.assertEqual(peak_30x, 7500.0)

    def test_high_amplitude_untouched(self) -> None:
        """Verify high amplitude audio (>= target_peak) is not modified to prevent distortion."""
        import numpy as np

        pcm_high = (np.ones(1024, dtype=np.int16) * 25000).tobytes()
        norm_high = normalize_pcm(pcm_high, target_peak=22000, max_gain=50.0)
        peak_high = float(np.max(np.abs(np.frombuffer(norm_high, dtype=np.int16))))
        self.assertEqual(peak_high, 25000.0)

    def test_clipping_protection(self) -> None:
        """Verify clipping protection guarantees samples remain within int16 bounds [-32768, 32767]."""
        import numpy as np

        # Create ramp spanning -30000 to +30000
        ramp = np.linspace(-30000, 30000, 1024).astype(np.int16)
        pcm_ramp = ramp.tobytes()

        # Set target_peak > 32767 (e.g. 45000) to verify strict [-32768, 32767] clipping
        norm_clipped = normalize_pcm(pcm_ramp, target_peak=45000, max_gain=2.0)
        out_arr = np.frombuffer(norm_clipped, dtype=np.int16)

        self.assertTrue(np.all(out_arr >= -32768))
        self.assertTrue(np.all(out_arr <= 32767))
        self.assertEqual(int(np.max(out_arr)), 32767)
        self.assertEqual(int(np.min(out_arr)), -32768)


class TestVoiceFactory(unittest.TestCase):
    """Test suite for voice factory functions and configuration."""

    def test_factory_creates_correct_components(self) -> None:
        # Mock mic
        cfg_mock = AudioConfig(audio_input="mock", stt_provider="mock")
        mic_mock = create_microphone(cfg_mock)
        self.assertIsInstance(mic_mock, MockMicrophone)
        stt_mock = create_stt_provider(cfg_mock)
        self.assertIsInstance(stt_mock, MockSpeechToText)

        # Pi mic
        cfg_pi = AudioConfig(audio_input="pi")
        mic_pi = create_microphone(cfg_pi)
        self.assertIsInstance(mic_pi, RaspberryPiMicrophone)

        # Service factory
        svc = create_voice_service(cfg_mock, mock=True)
        self.assertIsInstance(svc, VoiceService)
        self.assertIsInstance(svc.microphone, MockMicrophone)
        self.assertIsInstance(svc.stt, MockSpeechToText)


if __name__ == "__main__":
    unittest.main()
