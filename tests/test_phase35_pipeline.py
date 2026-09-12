"""Comprehensive unit and integration test suite for Phase 3.5.

Validates:
1. WakeWordDetector contracts, detection, no detection, repeated detection suppression,
   threshold handling, detector failure, and model initialization failure handling.
2. WhisperSTT contracts, valid audio, empty audio, silence, transcription results,
   transcription failure, and malformed audio handling.
3. Pipeline end-to-end transitions: IDLE -> wake word -> LISTENING -> speech capture
   -> Whisper -> SPEECH_RECEIVED -> THINKING.
4. Transcription failure -> ERROR transition.
5. Speech timeout without voice -> return to IDLE.
6. Verification that AgentInputInterface receives the text and LLM is NOT called.
7. Swappability of microphone backends preserving pipeline and state machine contracts.
"""

from pathlib import Path
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.config import AudioConfig, Config
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.voice import (
    AgentInputInterface,
    BaseMicrophone,
    BaseSpeechToText,
    BaseWakeWordDetector,
    STTResult,
    WakeWordResult,
)
from bimo.voice import (
    DefaultAgentInput,
    EnergyVAD,
    MockMicrophone,
    MockSpeechToText,
    MockWakeWordDetector,
    OpenWakeWordDetector,
    PCMicrophone,
    RaspberryPiMicrophone,
    VoicePipelineStage,
    VoiceService,
    WhisperSTT,
    create_microphone,
    create_stt_provider,
    create_voice_service,
    create_wake_word_detector,
)


class TestWakeWordDetector(unittest.TestCase):
    """Unit tests for WakeWordDetector interface and implementations."""

    def setUp(self) -> None:
        self.event_bus = EventBus()

    def test_mock_detection_and_events(self) -> None:
        detector = MockWakeWordDetector(
            model_name="hey_jarvis",
            threshold=0.5,
            cooldown_seconds=1.0,
            event_bus=self.event_bus,
            should_detect=False,
        )
        self.assertIsInstance(detector, BaseWakeWordDetector)
        self.assertEqual(detector.provider_name, "MockWakeWord")
        self.assertEqual(detector.model_name, "hey_jarvis")
        self.assertEqual(detector.threshold, 0.5)

        events: list[Event] = []
        self.event_bus.subscribe(EventType.WAKE_WORD_DETECTED, events.append)

        # 1. No detection
        chunk = b"\x00\x10" * 512
        res1 = detector.process_audio(chunk)
        self.assertFalse(res1.detected)
        self.assertEqual(len(events), 0)

        # 2. Queue detection
        detector.set_next_detection(True, score=0.92)
        res2 = detector.process_audio(chunk)
        self.assertTrue(res2.detected)
        self.assertEqual(res2.model_name, "hey_jarvis")
        self.assertAlmostEqual(res2.score, 0.92)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data["model"], "hey_jarvis")

    def test_no_detection_on_silence_or_empty(self) -> None:
        detector = MockWakeWordDetector(should_detect=True)
        # Empty chunk returns detected=False
        res_empty = detector.process_audio(b"")
        self.assertFalse(res_empty.detected)

    def test_repeated_detection_suppression_cooldown(self) -> None:
        detector = MockWakeWordDetector(
            model_name="hey_jarvis",
            threshold=0.5,
            cooldown_seconds=1.0,
            event_bus=self.event_bus,
            should_detect=True,
            fixed_score=0.95,
        )
        chunk = b"\x00\x20" * 512

        # First detection succeeds
        res1 = detector.process_audio(chunk)
        self.assertTrue(res1.detected)

        # Immediate second detection is suppressed by debounce cooldown
        res2 = detector.process_audio(chunk)
        self.assertFalse(res2.detected)
        self.assertEqual(res2.score, 0.95)

        # Wait for cooldown to expire
        time.sleep(1.05)
        res3 = detector.process_audio(chunk)
        self.assertTrue(res3.detected)

    def test_threshold_handling(self) -> None:
        detector = MockWakeWordDetector(
            threshold=0.8,
            event_bus=self.event_bus,
        )
        detector.set_next_detection(True, score=0.65)
        chunk = b"\x00\x15" * 512
        res = detector.process_audio(chunk)
        # Below threshold 0.8 -> not detected
        self.assertFalse(res.detected)
        self.assertAlmostEqual(res.score, 0.65)

    def test_detector_failure_and_events(self) -> None:
        detector = MockWakeWordDetector(event_bus=self.event_bus)
        errors: list[Event] = []
        self.event_bus.subscribe(EventType.WAKE_WORD_ERROR, errors.append)

        detector.simulate_error("Audio DSP buffer corruption")
        res = detector.process_audio(b"\x00\x10" * 512)
        self.assertFalse(res.detected)
        self.assertEqual(res.error, "Audio DSP buffer corruption")
        self.assertEqual(len(errors), 1)
        self.assertIn("Audio DSP buffer corruption", errors[0].data["error"])

    def test_openwakeword_initialization_failure_handling(self) -> None:
        # Test nonexistent model path handling
        detector = OpenWakeWordDetector(
            model_path="/nonexistent/model/path.onnx",
            event_bus=self.event_bus,
        )
        self.assertFalse(detector.is_active)
        res = detector.process_audio(b"\x00\x10" * 512)
        self.assertFalse(res.detected)
        self.assertIsNotNone(res.error)

    def test_openwakeword_real_detector_live_instance(self) -> None:
        detector = OpenWakeWordDetector(model_name="hey_jarvis", event_bus=self.event_bus)
        self.assertTrue(detector.is_active)
        self.assertEqual(detector.provider_name, "openwakeword")
        # Feeding 1280 samples of silence should yield detected=False with 0 error
        chunk = b"\x00\x00" * 1280
        res = detector.process_audio(chunk)
        self.assertFalse(res.detected)
        self.assertIsNone(res.error)

    def test_wake_word_result_interoperability(self) -> None:
        # Test model / model_name and confidence / score cross-population
        r1 = WakeWordResult(detected=True, model="alexa", confidence=0.89)
        self.assertEqual(r1.model, "alexa")
        self.assertEqual(r1.model_name, "alexa")
        self.assertAlmostEqual(r1.confidence, 0.89)
        self.assertAlmostEqual(r1.score, 0.89)

        r2 = WakeWordResult(detected=True, model_name="hey_jarvis", score=0.94)
        self.assertEqual(r2.model, "hey_jarvis")
        self.assertEqual(r2.model_name, "hey_jarvis")
        self.assertAlmostEqual(r2.confidence, 0.94)
        self.assertAlmostEqual(r2.score, 0.94)

    def test_multi_wake_word_mock_evaluation(self) -> None:
        detector = MockWakeWordDetector(
            model_names=["hey_jarvis", "alexa", "hey_mycroft"],
            event_bus=self.event_bus,
        )
        self.assertEqual(detector.model_names, ("hey_jarvis", "alexa", "hey_mycroft"))

        events: list[Event] = []
        self.event_bus.subscribe(EventType.WAKE_WORD_DETECTED, events.append)

        chunk = b"\x00\x10" * 512
        # Detect 'alexa'
        detector.set_next_detection(True, score=0.91, model="alexa")
        res1 = detector.process_audio(chunk)
        self.assertTrue(res1.detected)
        self.assertEqual(res1.model, "alexa")
        self.assertEqual(res1.model_name, "alexa")
        self.assertEqual(events[-1].data["model"], "alexa")

        # Wait for cooldown
        time.sleep(1.05)

        # Detect 'hey_mycroft'
        detector.set_next_detection(True, score=0.88, model="hey_mycroft")
        res2 = detector.process_audio(chunk)
        self.assertTrue(res2.detected)
        self.assertEqual(res2.model, "hey_mycroft")
        self.assertEqual(events[-1].data["model"], "hey_mycroft")

    def test_multi_model_deterministic_tie_breaking(self) -> None:
        detector = OpenWakeWordDetector(
            model_names=["hey_jarvis", "alexa"],
            event_bus=self.event_bus,
        )
        # Mock internal model predict
        mock_model = MagicMock()
        mock_model.predict.return_value = {
            "hey_jarvis_v0.1": 0.92,
            "alexa_v0.1": 0.92,
        }
        detector._model = mock_model
        detector._is_active = True

        chunk = b"\x00\x10" * 512
        # Equal score: first configured model ('hey_jarvis') wins deterministically
        res = detector.process_audio(chunk)
        self.assertTrue(res.detected)
        self.assertEqual(res.model, "hey_jarvis")

        # Now make alexa higher
        time.sleep(1.55)
        mock_model.predict.return_value = {
            "hey_jarvis_v0.1": 0.65,
            "alexa_v0.1": 0.88,
        }
        res2 = detector.process_audio(chunk)
        self.assertTrue(res2.detected)
        self.assertEqual(res2.model, "alexa")



class TestWhisperSTT(unittest.TestCase):
    """Unit tests for WhisperSTT provider."""

    def test_whisper_contract_and_properties(self) -> None:
        stt = WhisperSTT(model_size="tiny.en", device="cpu", compute_type="int8")
        self.assertIsInstance(stt, BaseSpeechToText)
        self.assertIn("tiny.en", stt.provider_name)
        self.assertEqual(stt.model_size, "tiny.en")
        self.assertEqual(stt.device, "cpu")
        self.assertEqual(stt.compute_type, "int8")

    def test_whisper_empty_audio(self) -> None:
        stt = WhisperSTT(model_size="tiny.en")
        res = stt.transcribe(b"")
        self.assertEqual(res.text, "")
        self.assertEqual(res.error, "Empty audio buffer")

    def test_whisper_silence(self) -> None:
        stt = WhisperSTT(model_size="tiny.en")
        # 1 second of pure zeros (silence)
        silence = bytes(32000)
        res = stt.transcribe(silence)
        self.assertEqual(res.text, "")
        self.assertIsNone(res.error)

    def test_whisper_malformed_audio_odd_bytes(self) -> None:
        stt = WhisperSTT(model_size="tiny.en")
        # Odd number of bytes (1023 bytes)
        odd_audio = bytes(1023)
        res = stt.transcribe(odd_audio)
        self.assertEqual(res.text, "")
        self.assertIsNone(res.error)

    def test_whisper_transcription_result_mocked(self) -> None:
        stt = WhisperSTT(model_size="tiny.en")

        # Mock internal model transcribe output
        mock_segment = MagicMock()
        mock_segment.text = "What time is it?"
        mock_segment.avg_logprob = -0.15

        mock_info = MagicMock()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], mock_info)
        stt._model = mock_model
        stt._is_initialized = True

        audio = b"\x00\x25" * 16000
        res = stt.transcribe(audio, sample_rate=16000)
        self.assertEqual(res.text, "What time is it?")
        self.assertGreater(res.confidence, 0.8)
        self.assertIsNone(res.error)

    def test_whisper_transcription_failure(self) -> None:
        stt = WhisperSTT(model_size="tiny.en")
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("Inference kernel out of memory")
        stt._model = mock_model
        stt._is_initialized = True

        audio = b"\x00\x25" * 16000
        res = stt.transcribe(audio)
        self.assertEqual(res.text, "")
        self.assertIn("Inference kernel out of memory", str(res.error))

    def test_whisper_real_inference_execution(self) -> None:
        stt = WhisperSTT(model_size="tiny.en", device="cpu", compute_type="int8")
        # Transcribe 1.0 second of valid PCM audio without crashing
        audio = bytes(32000)
        res = stt.transcribe(audio, sample_rate=16000)
        self.assertEqual(res.text, "")
        self.assertIsNone(res.error)


class TestPhase35VoicePipeline(unittest.TestCase):
    """Integration test suite for the complete Voice Pipeline and State Machine."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=self.event_bus,
            auto_subscribe_events=True,
        )
        self.agent_input = DefaultAgentInput()
        self.mic = MockMicrophone(sample_rate=16000, chunk_size=1024)
        self.wake_word = MockWakeWordDetector(
            model_name="hey_jarvis",
            threshold=0.5,
            event_bus=self.event_bus,
        )
        self.stt = MockSpeechToText(default_text="What time is it?")
        self.vad = EnergyVAD(
            energy_threshold=100.0,
            speech_time_threshold=0.04,
            silence_time_threshold=0.1,
            sample_rate=16000,
            chunk_size=1024,
        )
        self.service = VoiceService(
            microphone=self.mic,
            stt=self.stt,
            wake_word_detector=self.wake_word,
            event_bus=self.event_bus,
            agent_input=self.agent_input,
            vad=self.vad,
            speech_timeout=1.0,
            max_speech_duration=2.0,
            min_speech_bytes=1024,
        )

    def tearDown(self) -> None:
        self.service.stop()

    def test_idle_to_wake_word_to_listening(self) -> None:
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)
        self.assertEqual(self.service.stage, VoicePipelineStage.WAITING_FOR_WAKE_WORD)

        events_received: list[EventType] = []
        self.event_bus.subscribe(None, lambda e: events_received.append(e.type))

        self.service.start()

        # Trigger wake word
        self.wake_word.set_next_detection(True, score=0.94)
        self.mic.feed_audio(b"\x00\x10" * 1024)
        time.sleep(0.2)

        self.assertEqual(self.state_machine.current_state, RobotState.LISTENING)
        self.assertEqual(self.service.stage, VoicePipelineStage.CAPTURING_SPEECH)
        self.assertIn(EventType.WAKE_WORD_DETECTED, events_received)
        self.assertIn(EventType.LISTENING_STARTED, events_received)
        self.assertIn(EventType.SPEECH_CAPTURE_STARTED, events_received)

    def test_listening_to_speech_capture_to_whisper_to_speech_received(self) -> None:
        events: list[Event] = []
        self.event_bus.subscribe(None, events.append)

        self.service.start()

        # 1. Wake word triggers LISTENING
        self.wake_word.set_next_detection(True, score=0.91)
        self.mic.feed_audio(b"\x00\x10" * 1024)
        time.sleep(0.15)
        self.assertEqual(self.state_machine.current_state, RobotState.LISTENING)

        # 2. User speech triggers capture and VAD
        for _ in range(5):
            self.mic.feed_audio(b"\x00\x40" * 1024)
            time.sleep(0.04)

        # 3. Silence triggers end of speech and transcription
        for _ in range(6):
            self.mic.feed_audio(bytes(2048))
            time.sleep(0.04)

        time.sleep(0.25)

        event_types = [e.type for e in events]
        self.assertIn(EventType.SPEECH_CAPTURE_STOPPED, event_types)
        self.assertIn(EventType.SPEECH_TRANSCRIPTION_STARTED, event_types)
        self.assertIn(EventType.SPEECH_TRANSCRIPTION_COMPLETED, event_types)
        self.assertIn(EventType.SPEECH_RECEIVED, event_types)

        # Robot state transitions to THINKING upon SPEECH_RECEIVED
        self.assertEqual(self.state_machine.current_state, RobotState.THINKING)

    def test_transcription_failure_transitions_to_error_state(self) -> None:
        self.service.start()

        # Set STT to fail
        self.stt.set_next_result("", error="Whisper decode corrupted")

        # 1. Wake word
        self.wake_word.set_next_detection(True, score=0.90)
        self.mic.feed_audio(b"\x00\x10" * 1024)
        time.sleep(0.15)

        # 2. Speech
        for _ in range(5):
            self.mic.feed_audio(b"\x00\x40" * 1024)
            time.sleep(0.04)

        # 3. Silence
        for _ in range(6):
            self.mic.feed_audio(bytes(2048))
            time.sleep(0.04)

        time.sleep(0.25)

        # Robot state transitions to ERROR
        self.assertEqual(self.state_machine.current_state, RobotState.ERROR)

    def test_speech_timeout_returns_to_idle(self) -> None:
        self.service.start()

        # Wake word detected
        self.wake_word.set_next_detection(True, score=0.90)
        self.mic.feed_audio(b"\x00\x10" * 1024)
        time.sleep(0.15)
        self.assertEqual(self.state_machine.current_state, RobotState.LISTENING)

        # User remains silent past speech_timeout (1.0s)
        for _ in range(12):
            self.mic.feed_audio(bytes(2048))
            time.sleep(0.1)

        time.sleep(0.2)
        # Returns to IDLE
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)
        self.assertEqual(self.service.stage, VoicePipelineStage.WAITING_FOR_WAKE_WORD)

    def test_llm_is_not_called(self) -> None:
        delivered_texts: list[str] = []

        class MockAgent(DefaultAgentInput):
            def receive_user_input(self, text: str, metadata: dict | None = None) -> None:
                delivered_texts.append(text)

        self.service.agent_input = MockAgent()
        self.service.start()

        self.wake_word.set_next_detection(True, score=0.95)
        self.mic.feed_audio(b"\x00\x10" * 1024)
        time.sleep(0.15)

        for _ in range(5):
            self.mic.feed_audio(b"\x00\x40" * 1024)
            time.sleep(0.04)

        for _ in range(6):
            self.mic.feed_audio(bytes(2048))
            time.sleep(0.04)

        time.sleep(0.25)

        self.assertEqual(len(delivered_texts), 1)
        self.assertEqual(delivered_texts[0], "What time is it?")
        # Verify state is THINKING and stopped before LLM call
        self.assertEqual(self.state_machine.current_state, RobotState.THINKING)

    def test_swapping_microphone_preserves_pipeline(self) -> None:
        # Swapping between PC, Mock, and RaspberryPi microphones
        pc_mic = PCMicrophone(sample_rate=16000, chunk_size=1024)
        pi_mic = RaspberryPiMicrophone(sample_rate=16000, chunk_size=1024)

        self.assertIsInstance(pc_mic, BaseMicrophone)
        self.assertIsInstance(pi_mic, BaseMicrophone)

        # Zero changes needed to state machine or wake word pipeline
        service_pi = VoiceService(
            microphone=pi_mic,
            stt=self.stt,
            wake_word_detector=self.wake_word,
            event_bus=self.event_bus,
        )
        self.assertEqual(service_pi.microphone.id, "rpi_microphone")
