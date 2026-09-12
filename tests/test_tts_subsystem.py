"""Comprehensive unit tests for Phase 4 Text-To-Speech (TTS) subsystem.

Validates:
1. BaseTextToSpeech interface contracts and DeviceCapability.AUDIO_OUTPUT.
2. Normal speech synthesis and playback completion.
3. Empty input rejection (empty string and blank whitespace).
4. Long input handling (multi-sentence paragraphs).
5. Cancellation and interruption (tts.stop()).
6. Playback failure and error event propagation.
7. End-to-end state transitions: IDLE -> SPEAKING -> IDLE and SPEAKING -> LISTENING (barge-in).
8. Multiple speech requests (interruption and sequential execution).
9. Raspberry Pi TTS stub swappability without state-machine changes.
10. Factory creation based on configuration.
"""

from pathlib import Path
import sys
import time
import unittest

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.config import AudioConfig, Config
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.devices import BaseDevice, DeviceCapability, DeviceStatus
from unittest.mock import MagicMock, patch

from bimo.interfaces.voice import BaseTextToSpeech
from bimo.voice import (
    MockTTS,
    PiperTTS,
    RaspberryPiTTS,
    WindowsTTS,
    create_tts_provider,
)


class TestBaseTextToSpeechContract(unittest.TestCase):
    """Test suite for BaseTextToSpeech interface contracts and properties."""

    def test_mock_tts_implements_interface_contracts(self) -> None:
        tts = MockTTS(speech_rate=180, volume=0.8)
        self.assertIsInstance(tts, BaseTextToSpeech)
        self.assertIsInstance(tts, BaseDevice)
        self.assertTrue(tts.has_capability(DeviceCapability.AUDIO_OUTPUT))
        self.assertEqual(tts.speech_rate, 180)
        self.assertEqual(tts.volume, 0.8)
        self.assertFalse(tts.is_speaking)

        self.assertTrue(tts.connect())
        self.assertEqual(tts.status, DeviceStatus.ONLINE)
        self.assertTrue(tts.health_check())

        tts.disconnect()
        self.assertEqual(tts.status, DeviceStatus.OFFLINE)

    def test_volume_clamping(self) -> None:
        tts = MockTTS(volume=1.5)
        self.assertEqual(tts.volume, 1.0)
        tts.volume = -0.5
        self.assertEqual(tts.volume, 0.0)
        tts.volume = 0.65
        self.assertEqual(tts.volume, 0.65)

    def test_speech_rate_property(self) -> None:
        tts = MockTTS(speech_rate=150)
        self.assertEqual(tts.speech_rate, 150)
        tts.speech_rate = 220
        self.assertEqual(tts.speech_rate, 220)

    def test_voice_property(self) -> None:
        tts = MockTTS(voice="Microsoft David")
        self.assertEqual(tts.voice, "Microsoft David")
        tts.voice = "Microsoft Zira"
        self.assertEqual(tts.voice, "Microsoft Zira")


class TestMockTTSSpeech(unittest.TestCase):
    """Test suite covering MockTTS speech operations and edge cases."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.tts = MockTTS(event_bus=self.event_bus)
        self.events: list[Event] = []
        self.event_bus.subscribe(None, lambda e: self.events.append(e))

    def test_normal_speech(self) -> None:
        """Verify normal speech output emits STARTED and FINISHED events."""
        success = self.tts.speak("Hello, I am Bimo.", block=True)
        self.assertTrue(success)
        self.assertIn("Hello, I am Bimo.", self.tts.spoken_texts)
        self.assertFalse(self.tts.is_speaking)

        event_types = [e.type for e in self.events]
        self.assertIn(EventType.SPEECH_OUTPUT_STARTED, event_types)
        self.assertIn(EventType.SPEECH_OUTPUT_FINISHED, event_types)

        started_ev = next(e for e in self.events if e.type == EventType.SPEECH_OUTPUT_STARTED)
        self.assertEqual(started_ev.data["text"], "Hello, I am Bimo.")

    def test_empty_input(self) -> None:
        """Verify empty and whitespace-only strings are rejected without events."""
        self.assertFalse(self.tts.speak(""))
        self.assertFalse(self.tts.speak("   "))
        self.assertFalse(self.tts.speak("\t\n"))
        self.assertEqual(len(self.tts.spoken_texts), 0)
        self.assertEqual(len(self.events), 0)

    def test_long_input(self) -> None:
        """Verify long multi-sentence input synthesizes cleanly."""
        long_text = (
            "Bimo is an interactive desk robot companion. It features an LCD face, "
            "voice input with dual-threshold voice activity detection, text-to-speech output, "
            "and a decoupled event-driven architecture designed to operate smoothly without lag. "
        ) * 4
        self.assertGreater(len(long_text), 800)
        success = self.tts.speak(long_text, block=True)
        self.assertTrue(success)
        self.assertIn(long_text, self.tts.spoken_texts)

    def test_cancellation(self) -> None:
        """Verify active speech can be interrupted via stop() emitting CANCELLED."""
        tts_async = MockTTS(event_bus=self.event_bus, simulated_duration=0.5)
        success = tts_async.speak("A sentence that will be interrupted.", block=False)
        self.assertTrue(success)
        self.assertTrue(tts_async.is_speaking)

        time.sleep(0.05)
        tts_async.stop()

        self.assertFalse(tts_async.is_speaking)
        self.assertEqual(tts_async.stop_calls, 1)

        event_types = [e.type for e in self.events]
        self.assertIn(EventType.SPEECH_OUTPUT_STARTED, event_types)
        self.assertIn(EventType.SPEECH_OUTPUT_CANCELLED, event_types)

    def test_playback_completion(self) -> None:
        """Verify asynchronous playback runs to completion with accurate duration."""
        tts_async = MockTTS(event_bus=self.event_bus, simulated_duration=0.1)
        tts_async.speak("Short speech utterance.", block=False)

        # Wait for completion
        time.sleep(0.2)
        self.assertFalse(tts_async.is_speaking)

        finished_ev = next((e for e in self.events if e.type == EventType.SPEECH_OUTPUT_FINISHED), None)
        self.assertIsNotNone(finished_ev)
        self.assertEqual(finished_ev.data["text"], "Short speech utterance.")
        self.assertAlmostEqual(finished_ev.data["duration"], 0.1, places=1)

    def test_playback_failure(self) -> None:
        """Verify simulated playback failure dispatches SPEECH_OUTPUT_ERROR."""
        self.tts.simulated_error = "I2S speaker communication failure"
        success = self.tts.speak("Testing failure", block=True)
        self.assertFalse(success)

        event_types = [e.type for e in self.events]
        self.assertIn(EventType.SPEECH_OUTPUT_ERROR, event_types)
        err_ev = next(e for e in self.events if e.type == EventType.SPEECH_OUTPUT_ERROR)
        self.assertEqual(err_ev.data["error"], "I2S speaker communication failure")

    def test_multiple_speech_requests(self) -> None:
        """Verify multiple speech requests interrupt previous speech cleanly without deadlock."""
        tts_async = MockTTS(event_bus=self.event_bus, simulated_duration=0.5)

        # First speech
        tts_async.speak("First utterance", block=False)
        self.assertTrue(tts_async.is_speaking)
        time.sleep(0.05)

        # Second speech should interrupt the first
        tts_async.speak("Second utterance", block=False)
        self.assertTrue(tts_async.is_speaking)

        for _ in range(20):
            if not tts_async.is_speaking:
                break
            time.sleep(0.05)
        self.assertFalse(tts_async.is_speaking)

        self.assertEqual(self.tts.speak_calls or tts_async.speak_calls, ["First utterance", "Second utterance"])
        event_types = [e.type for e in self.events]
        self.assertIn(EventType.SPEECH_OUTPUT_CANCELLED, event_types)
        self.assertIn(EventType.SPEECH_OUTPUT_FINISHED, event_types)


class TestTTSStateMachineIntegration(unittest.TestCase):
    """Integration test suite for TTS and RobotStateMachine."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=self.event_bus,
            auto_subscribe_events=True,
        )
        self.tts = MockTTS(event_bus=self.event_bus, simulated_duration=0.1)

    def test_idle_speaking_idle_cycle(self) -> None:
        """Verify complete state cycle: IDLE -> SPEAKING -> IDLE."""
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)

        # Start speaking (async)
        self.tts.speak("Hello Bimo", block=False)
        self.assertEqual(self.state_machine.current_state, RobotState.SPEAKING)

        # Wait for speech to complete
        time.sleep(0.2)
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)

    def test_cancellation_returns_to_idle(self) -> None:
        """Verify cancelling active speech returns state machine to IDLE."""
        self.tts.simulated_duration = 1.0
        self.tts.speak("Long utterance to cancel", block=False)
        self.assertEqual(self.state_machine.current_state, RobotState.SPEAKING)

        self.tts.stop()
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)

    def test_error_transitions_to_error_state(self) -> None:
        """Verify TTS error event transitions state machine to ERROR."""
        self.tts.simulated_error = "Audio DAC disconnected"
        self.tts.speak("Faulty audio", block=True)
        self.assertEqual(self.state_machine.current_state, RobotState.ERROR)

    def test_barge_in_interruption_transitions_to_listening(self) -> None:
        """Verify user speaking during robot TTS transitions SPEAKING -> LISTENING."""
        self.tts.simulated_duration = 1.0
        self.tts.speak("Robot is talking...", block=False)
        self.assertEqual(self.state_machine.current_state, RobotState.SPEAKING)

        # User interrupts / barge-in
        self.event_bus.publish(Event(type=EventType.VOICE_ACTIVITY_STARTED, source="mic"))
        self.assertEqual(self.state_machine.current_state, RobotState.LISTENING)


class TestRaspberryPiTTSStub(unittest.TestCase):
    """Test suite for RaspberryPiTTS hardware abstraction stub."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=self.event_bus,
            auto_subscribe_events=True,
        )
        self.rpi_tts = RaspberryPiTTS(
            alsa_device="hw:0,0",
            engine="piper",
            event_bus=self.event_bus,
        )

    def test_rpi_tts_properties_and_contracts(self) -> None:
        self.assertEqual(self.rpi_tts.alsa_device, "hw:0,0")
        self.assertEqual(self.rpi_tts.engine, "piper")
        self.assertTrue(self.rpi_tts.connect())
        self.assertEqual(self.rpi_tts.status, DeviceStatus.ONLINE)
        self.assertTrue(self.rpi_tts.health_check())

    def test_rpi_tts_swappability_without_state_machine_changes(self) -> None:
        """Verify swapping to RaspberryPiTTS drives state machine identically with 0 changes."""
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)

        # Speak via Pi stub
        success = self.rpi_tts.speak("Pi audio test", block=True)
        self.assertTrue(success)

        # Finished returns to IDLE
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)


class TestWindowsTTS(unittest.TestCase):
    """Unit tests for Windows SAPI TTS implementation."""

    def test_sapi_rate_and_volume_conversion(self) -> None:
        tts = WindowsTTS()
        # 175 WPM -> SAPI 0
        self.assertEqual(tts._convert_rate_to_sapi(175), 0)
        # 100 WPM -> SAPI -6
        self.assertEqual(tts._convert_rate_to_sapi(100), -6)
        # 300 WPM -> SAPI 10
        self.assertEqual(tts._convert_rate_to_sapi(300), 10)

        # Volume conversion
        self.assertEqual(tts._convert_volume_to_sapi(1.0), 100)
        self.assertEqual(tts._convert_volume_to_sapi(0.0), 0)
        self.assertEqual(tts._convert_volume_to_sapi(0.5), 50)

    def test_empty_string_rejected(self) -> None:
        tts = WindowsTTS()
        self.assertFalse(tts.speak(""))
        self.assertFalse(tts.speak("   "))

    def test_health_check_and_available_voices(self) -> None:
        tts = WindowsTTS()
        tts.connect()
        self.assertTrue(tts.health_check())
        voices = tts.get_available_voices()
        self.assertIsInstance(voices, list)
        self.assertGreater(len(voices), 0)


class TestTTSFactory(unittest.TestCase):
    """Test suite for create_tts_provider factory function."""

    def test_factory_creates_correct_tts_implementations(self) -> None:
        # Mock TTS
        cfg_mock = AudioConfig(tts_provider="mock")
        tts_mock = create_tts_provider(cfg_mock)
        self.assertIsInstance(tts_mock, MockTTS)

        # Windows TTS
        cfg_win = AudioConfig(tts_provider="windows")
        tts_win = create_tts_provider(cfg_win)
        self.assertIsInstance(tts_win, WindowsTTS)

        # Raspberry Pi TTS
        cfg_pi = AudioConfig(tts_provider="pi")
        tts_pi = create_tts_provider(cfg_pi)
        self.assertIsInstance(tts_pi, RaspberryPiTTS)

        # Piper TTS (default local neural TTS)
        cfg_piper = AudioConfig(tts_provider="piper")
        tts_piper = create_tts_provider(cfg_piper)
        self.assertIsInstance(tts_piper, PiperTTS)


class TestPiperTTS(unittest.TestCase):
    """Test suite for PiperTTS neural speech synthesis implementation."""

    def setUp(self) -> None:
        self.event_bus = EventBus()

    def test_piper_properties_and_contract(self) -> None:
        tts = PiperTTS(
            voice="en_GB-semaine-medium",
            speech_rate=175,
            volume=0.9,
            event_bus=self.event_bus,
        )
        self.assertIsInstance(tts, BaseTextToSpeech)
        self.assertIsInstance(tts, BaseDevice)
        self.assertTrue(tts.has_capability(DeviceCapability.AUDIO_OUTPUT))
        self.assertEqual(tts.voice, "en_GB-semaine-medium")
        self.assertEqual(tts.speech_rate, 175)
        self.assertAlmostEqual(tts.volume, 0.9)
        self.assertFalse(tts.is_speaking)

    def test_piper_empty_or_whitespace_string_rejected(self) -> None:
        tts = PiperTTS(event_bus=self.event_bus)
        self.assertFalse(tts.speak(""))
        self.assertFalse(tts.speak("    "))
        self.assertFalse(tts.is_speaking)

    @patch("bimo.voice.tts.sd")
    def test_piper_mocked_synthesis_and_events(self, mock_sd: MagicMock) -> None:
        tts = PiperTTS(event_bus=self.event_bus)

        # Mock piper voice
        mock_voice = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.audio_float_array = [0.0] * 1000
        mock_chunk._audio_int16_array = None
        mock_voice.synthesize.return_value = [mock_chunk]
        tts._piper_voice = mock_voice
        tts._is_initialized = True

        started_events: list[Event] = []
        finished_events: list[Event] = []
        self.event_bus.subscribe(EventType.SPEECH_OUTPUT_STARTED, started_events.append)
        self.event_bus.subscribe(EventType.SPEECH_OUTPUT_FINISHED, finished_events.append)

        success = tts.speak("Testing Piper speech synthesis", block=True)
        self.assertTrue(success)
        self.assertEqual(len(started_events), 1)
        self.assertEqual(started_events[0].data["text"], "Testing Piper speech synthesis")
        self.assertEqual(len(finished_events), 1)
        self.assertEqual(finished_events[0].data["text"], "Testing Piper speech synthesis")

    @patch("bimo.voice.tts.sd")
    def test_piper_mocked_cancellation(self, mock_sd: MagicMock) -> None:
        tts = PiperTTS(event_bus=self.event_bus)

        cancelled_events: list[Event] = []
        self.event_bus.subscribe(EventType.SPEECH_OUTPUT_CANCELLED, cancelled_events.append)

        # Simulate speaking active
        tts._is_speaking = True
        tts.stop()
        self.assertFalse(tts.is_speaking)
        self.assertEqual(len(cancelled_events), 1)
        mock_sd.stop.assert_called_once()

    def test_piper_mocked_synthesis_error(self) -> None:
        tts = PiperTTS(event_bus=self.event_bus)

        mock_voice = MagicMock()
        mock_voice.synthesize.side_effect = RuntimeError("Piper ONNX inference failed")
        tts._piper_voice = mock_voice
        tts._is_initialized = True

        error_events: list[Event] = []
        self.event_bus.subscribe(EventType.SPEECH_OUTPUT_ERROR, error_events.append)

        tts.speak("Failing utterance", block=True)
        self.assertEqual(len(error_events), 1)
        self.assertIn("Piper ONNX inference failed", error_events[0].data["error"])

    @patch("bimo.voice.tts.sd")
    def test_piper_live_inference_synthesis(self, mock_sd: MagicMock) -> None:
        """Verify real Piper voice synthesizes audio from the downloaded model."""
        tts = PiperTTS(
            model_path="models/piper/en_GB-semaine-medium.onnx",
            config_path="models/piper/en_GB-semaine-medium.onnx.json",
            event_bus=self.event_bus,
        )
        if not tts._is_initialized:
            self.skipTest("Piper model weights not available on disk")

        started_events: list[Event] = []
        finished_events: list[Event] = []
        self.event_bus.subscribe(EventType.SPEECH_OUTPUT_STARTED, started_events.append)
        self.event_bus.subscribe(EventType.SPEECH_OUTPUT_FINISHED, finished_events.append)

        success = tts.speak("Hello Bimo", block=True)
        self.assertTrue(success)
        self.assertEqual(len(started_events), 1)
        self.assertEqual(len(finished_events), 1)
        self.assertGreater(finished_events[0].data["duration"], 0.0)

    @patch("bimo.voice.tts.sd")
    def test_piper_swappability_without_state_machine_changes(self, mock_sd: MagicMock) -> None:
        """Verify PiperTTS drives state machine through IDLE -> SPEAKING -> IDLE with 0 state machine edits."""
        sm = RobotStateMachine(event_bus=self.event_bus)
        self.assertEqual(sm.current_state, RobotState.IDLE)

        tts = PiperTTS(event_bus=self.event_bus)
        mock_voice = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.audio_float_array = [0.0] * 500
        mock_chunk._audio_int16_array = None
        mock_voice.synthesize.return_value = [mock_chunk]
        tts._piper_voice = mock_voice
        tts._is_initialized = True

        # Non-blocking speak
        tts.speak("State machine test utterance", block=False)
        time.sleep(0.02)
        # Event SPEECH_OUTPUT_STARTED transitions IDLE -> SPEAKING
        self.assertEqual(sm.current_state, RobotState.SPEAKING)

        # Wait for worker completion
        time.sleep(0.1)
        # Event SPEECH_OUTPUT_FINISHED transitions SPEAKING -> IDLE
        self.assertEqual(sm.current_state, RobotState.IDLE)

    @patch("bimo.voice.tts.sd")
    def test_barge_in_interruption_piper_transitions_to_listening(self, mock_sd: MagicMock) -> None:
        """Verify user speaking during Piper TTS speech transitions SPEAKING -> LISTENING."""
        sm = RobotStateMachine(event_bus=self.event_bus)

        # Force state to SPEAKING
        sm.transition_to(RobotState.SPEAKING, reason="Robot speaking")
        self.assertEqual(sm.current_state, RobotState.SPEAKING)

        # Emit wake word detected while robot is speaking
        self.event_bus.publish(
            Event(
                type=EventType.WAKE_WORD_DETECTED,
                data={"model": "hey_jarvis", "score": 0.95},
                source="test",
            )
        )
        self.assertEqual(sm.current_state, RobotState.LISTENING)


if __name__ == "__main__":
    unittest.main()
