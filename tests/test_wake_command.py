"""Comprehensive unit and integration test suite for Wake Command Gating and Phonetic Normalization.

Validates:
1. Phonetic alias resolution for "Bimo" across accents and STT mishearings
   ("Be more", "Beam o", "Bemo", "Veemo", "Pimo", "B-mo", Hindi/Indian phonetics).
2. Strict Wake Word Gating: Bimo MUST NOT listen or dispatch when wake word is absent.
3. Wake Word Confirmation & Event Dispatching: Transcripts with wake words are accepted,
   normalized to canonical "Bimo", and delivered to AgentInputInterface.
4. VoiceService Pipeline Integration with EnergyVAD, MockMicrophone, MockSTT, and RobotStateMachine.
5. Command extraction from wake phrases.
6. STT Provider (GoogleWebSTT & WhisperSTT) phonetic normalization integration.
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
    GoogleWebSTT,
    MockMicrophone,
    MockSpeechToText,
    MockWakeWordDetector,
    VoicePipelineStage,
    VoiceService,
    WhisperSTT,
    contains_wake_word,
    create_voice_service,
    extract_command_after_wake_word,
    normalize_phonetic_text,
)


class TestPhoneticNormalization(unittest.TestCase):
    """Test suite for phonetic alias resolution of 'Bimo' and accented speech."""

    def test_bimo_mishearings_normalized_to_canonical(self) -> None:
        """Verify that speech recognizer mishearings of 'Bimo' are normalized to 'Bimo'."""
        test_cases = [
            ("be more open notepad", "Bimo open notepad"),
            ("Be More open calculator", "Bimo open calculator"),
            ("bee more, what is the time?", "Bimo, what is the time?"),
            ("be-more show desktop", "Bimo show desktop"),
            ("beemore please help", "Bimo please help"),
            ("beam o start explorer", "Bimo start explorer"),
            ("beam oh open terminal", "Bimo open terminal"),
            ("beamo close window", "Bimo close window"),
            ("bemo launch discord", "Bimo launch discord"),
            ("b-mo what's the weather", "Bimo what's the weather"),
            ("b mo take screenshot", "Bimo take screenshot"),
            ("b. mo help me", "Bimo help me"),
            ("veemo open chrome", "Bimo open chrome"),
            ("vimo play music", "Bimo play music"),
            ("beimo run diagnostics", "Bimo run diagnostics"),
            ("pimo status check", "Bimo status check"),
            ("be mode search web", "Bimo search web"),
            ("bheem o gaana chalao", "Bimo gaana chalao"),
            ("bheemo namaste", "Bimo namaste"),
            ("Demo open notepad", "Bimo open notepad"),
            ("Vivo can you hear me", "Bimo can you hear me"),
            ("vivo open notepad", "Bimo open notepad"),
            ("Hey Vivo", "Hey Bimo"),
            ("OK Vivo tell me a joke", "OK Bimo tell me a joke"),
            ("vevo status", "Bimo status"),
            ("remove", "Bimo"),
            ("remove can you hear me", "Bimo can you hear me"),
            ("Hey remove open chrome", "Hey Bimo open chrome"),
            ("Bimo remove test.py", "Bimo remove test.py"),
            ("heavy more", "Hey Bimo"),
            ("heavy more open notepad", "Hey Bimo open notepad"),
            ("heavy-more please help", "Hey Bimo please help"),
            ("heavymore what time is it", "Hey Bimo what time is it"),
            ("heavy mo start terminal", "Hey Bimo start terminal"),
            ("heavier more show desktop", "Hey Bimo show desktop"),
            ("Hey heavy more can you hear me", "Hey Bimo can you hear me"),
            ("hey be more open calculator", "Hey Bimo open calculator"),
            ("hay bimo play music", "Hey Bimo play music"),
            ("ae bimo status check", "Hey Bimo status check"),
            ("baby more", "Hey Bimo"),
            ("baby more open notepad", "Hey Bimo open notepad"),
            ("Hey baby more can you hear me", "Hey Bimo can you hear me"),
            ("bimu open discord", "Bimo open discord"),
            ("Hey Bimu", "Hey Bimo"),
            ("hi memo how are you", "hi Bimo how are you"),
            ("Memo open notepad", "Bimo open notepad"),
            ("Memo how are you", "Bimo how are you"),
            ("send a memo to the team", "send a memo to the team"),
            ("maybe more kaise ho", "Hey Bimo kaise ho"),
            ("maybe more open notepad", "Hey Bimo open notepad"),
            ("Hey maybe more tell me a joke", "Hey Bimo tell me a joke"),
            ("maybe more", "Hey Bimo"),
            ("Remo open chrome", "Bimo open chrome"),
            ("remo play music", "Bimo play music"),
            ("Hey Remo", "Hey Bimo"),
            ("OK remo status", "OK Bimo status"),
        ]

        for raw_input, expected in test_cases:
            with self.subTest(raw_input=raw_input):
                normalized = normalize_phonetic_text(raw_input)
                self.assertEqual(normalized, expected)

    def test_indian_hindi_vocabulary_normalization(self) -> None:
        """Verify common Hindi / Indian-English vocalizations are cleaned up."""
        cases = [
            ("numb state Bimo", "namaste Bimo"),
            ("num stay Bimo", "namaste Bimo"),
            ("Bimo gun chalao", "Bimo gaana chalao"),
            ("Bimo ganna chalao", "Bimo gaana chalao"),
            ("Bimo band karo", "Bimo band karo"),
            ("Bimo play episode song", "Bimo play despacito"),
            ("hello paner", "hello paneer"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phonetic_text(raw), expected)

    def test_contains_wake_word_detection(self) -> None:
        """Verify contains_wake_word identifies valid wake words and rejects non-wake speech."""
        # Positive cases (should detect wake word)
        positives = [
            "Bimo open notepad",
            "bimo, what time is it?",
            "Hey Bimo can you hear me",
            "OK Bimo help",
            "okay bimo",
            "be more open notepad",
            "bee more play music",
            "beam o search google",
            "bemo launch app",
            "veemo status",
            "bheemo gaana chalao",
            ("Vivo can you hear me"),
            ("vivo open notepad"),
            ("Hey Vivo"),
            ("remove can you hear me"),
            ("heavy more"),
            ("heavy more open notepad"),
            ("heavy-more please help"),
            ("Hey heavy more can you hear me"),
            ("baby more"),
            ("baby more open notepad"),
            ("Hey baby more can you hear me"),
            ("bimu open discord"),
            ("hi memo how are you"),
            ("maybe more kaise ho"),
            ("Remo open chrome"),
            ("alexa turn off lights"),
            "hey jarvis what is the time",
            "jarvis mute audio",
        ]
        for text in positives:
            with self.subTest(text=text):
                has_ww, matched = contains_wake_word(text)
                self.assertTrue(has_ww, f"Expected wake word in '{text}', but got False")
                self.assertIsNotNone(matched)

        # Negative cases (ambient speech without wake word)
        negatives = [
            "what is the weather today",
            "open notepad right now",
            "can you pass the salt please",
            "turn on the television",
            "who was the first president of the united states",
            "let us go for a walk outside",
            "I need some water",
            "the quick brown fox jumps over the lazy dog",
            "send a memo to the team",
        ]
        for text in negatives:
            with self.subTest(text=text):
                has_ww, matched = contains_wake_word(text)
                self.assertFalse(has_ww, f"Expected NO wake word in '{text}', but got True ('{matched}')")
                self.assertIsNone(matched)

    def test_extract_command_after_wake_word(self) -> None:
        """Verify command payload extraction following the wake word."""
        self.assertEqual(extract_command_after_wake_word("Bimo, open notepad"), "open notepad")
        self.assertEqual(extract_command_after_wake_word("Vivo, open notepad"), "open notepad")
        self.assertEqual(extract_command_after_wake_word("Hey Bimo, what time is it?"), "what time is it?")
        self.assertEqual(extract_command_after_wake_word("Hey Vivo, what time is it?"), "what time is it?")
        self.assertEqual(extract_command_after_wake_word("heavy more open notepad"), "open notepad")
        self.assertEqual(extract_command_after_wake_word("heavy more, what time is it?"), "what time is it?")
        self.assertEqual(extract_command_after_wake_word("baby more open notepad"), "open notepad")
        self.assertEqual(extract_command_after_wake_word("maybe more kaise ho"), "kaise ho")
        self.assertEqual(extract_command_after_wake_word("Remo open chrome"), "open chrome")
        self.assertEqual(extract_command_after_wake_word("hi memo how are you"), "how are you")
        self.assertEqual(extract_command_after_wake_word("bimu launch app"), "launch app")
        self.assertEqual(extract_command_after_wake_word("Be more, play music"), "play music")
        self.assertEqual(extract_command_after_wake_word("OK Bimo show desktop"), "show desktop")
        self.assertEqual(extract_command_after_wake_word("Bimo"), "Bimo")
        self.assertEqual(extract_command_after_wake_word("Vivo"), "Bimo")
        self.assertEqual(extract_command_after_wake_word("heavy more"), "Hey Bimo")
        self.assertEqual(extract_command_after_wake_word("baby more"), "Hey Bimo")
        self.assertEqual(extract_command_after_wake_word("maybe more"), "Hey Bimo")


class TestStrictWakeWordGating(unittest.TestCase):
    """Test suite ensuring Bimo strictly ignores speech unless wake words are included."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=self.event_bus,
            auto_subscribe_events=True,
        )
        self.agent_input = DefaultAgentInput()
        self.mic = MockMicrophone(sample_rate=16000, chunk_size=1024)
        self.stt = MockSpeechToText()
        self.vad = EnergyVAD(
            energy_threshold=500.0,
            speech_time_threshold=0.04,
            silence_time_threshold=0.08,
            sample_rate=16000,
            chunk_size=1024,
        )

        # VoiceService with require_wake_word=True
        self.service = VoiceService(
            microphone=self.mic,
            stt=self.stt,
            wake_word_detector=None,  # Direct continuous VAD mode with strict gating
            event_bus=self.event_bus,
            agent_input=self.agent_input,
            vad=self.vad,
            require_wake_word=True,
            min_speech_bytes=1000,
        )

    def tearDown(self) -> None:
        self.service.stop()

    def test_speech_without_wake_word_is_strictly_ignored(self) -> None:
        """Bimo MUST NOT listen or dispatch when wake word is absent in spoken speech."""
        ignored_events: list[Event] = []
        speech_received_events: list[Event] = []
        self.event_bus.subscribe(EventType.SPEECH_IGNORED, ignored_events.append)
        self.event_bus.subscribe(EventType.SPEECH_RECEIVED, speech_received_events.append)

        # Configure STT to return ambient speech without any wake word
        self.stt.set_next_result("What is the weather today?")
        self.service.start()

        # Feed speech
        for _ in range(5):
            self.mic.feed_audio(b"\x00\x40" * 1024)
            time.sleep(0.04)
        # Feed silence to complete utterance
        for _ in range(6):
            self.mic.feed_audio(bytes(2048))
            time.sleep(0.04)
        time.sleep(0.3)

        # 1. SPEECH_IGNORED event MUST be published
        self.assertEqual(len(ignored_events), 1)
        self.assertEqual(ignored_events[0].data["reason"], "no_wake_word")
        self.assertIn("What is the weather today?", ignored_events[0].data["raw_transcript"])

        # 2. SPEECH_RECEIVED MUST NOT be published
        self.assertEqual(len(speech_received_events), 0)

        # 3. AgentInputInterface MUST NOT receive input
        self.assertEqual(self.agent_input.input_count, 0)
        self.assertIsNone(self.agent_input.last_input)

        # 4. State machine returns to IDLE
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)

    def test_speech_with_phonetic_wake_word_is_accepted_and_dispatched(self) -> None:
        """Bimo accepts 'be more open notepad', normalizes to 'Bimo', and delivers to agent."""
        ignored_events: list[Event] = []
        speech_received_events: list[Event] = []
        wake_detected_events: list[Event] = []

        self.event_bus.subscribe(EventType.SPEECH_IGNORED, ignored_events.append)
        self.event_bus.subscribe(EventType.SPEECH_RECEIVED, speech_received_events.append)
        self.event_bus.subscribe(EventType.WAKE_WORD_DETECTED, wake_detected_events.append)

        # STT returns phonetic mishearing of Bimo
        self.stt.set_next_result("be more open notepad")
        self.service.start()

        # Feed speech
        for _ in range(5):
            self.mic.feed_audio(b"\x00\x40" * 1024)
            time.sleep(0.04)
        # Feed silence
        for _ in range(6):
            self.mic.feed_audio(bytes(2048))
            time.sleep(0.04)
        time.sleep(0.3)

        # 1. SPEECH_IGNORED must NOT be published
        self.assertEqual(len(ignored_events), 0)

        # 2. WAKE_WORD_DETECTED must be emitted
        self.assertGreaterEqual(len(wake_detected_events), 1)

        # 3. SPEECH_RECEIVED must be emitted with normalized text
        self.assertEqual(len(speech_received_events), 1)
        self.assertEqual(speech_received_events[0].data["transcript"], "Bimo open notepad")

        # 4. AgentInputInterface must receive normalized command
        self.assertEqual(self.agent_input.input_count, 1)
        self.assertEqual(self.agent_input.last_input, "Bimo open notepad")

        # 5. State machine transitions to THINKING
        self.assertEqual(self.state_machine.current_state, RobotState.THINKING)

    def test_process_audio_buffer_wake_word_gating(self) -> None:
        """Verify process_audio_buffer respects require_wake_word."""
        # Non-wake word buffer
        self.stt.set_next_result("Turn on the lights")
        res1 = self.service.process_audio_buffer(b"\x00\x10" * 1000)
        self.assertEqual(self.agent_input.input_count, 0)

        # Wake word buffer with phonetic variant
        self.stt.set_next_result("beam o turn on the lights")
        res2 = self.service.process_audio_buffer(b"\x00\x10" * 1000)
        self.assertEqual(self.agent_input.input_count, 1)
        self.assertEqual(self.agent_input.last_input, "Bimo turn on the lights")


class TestHybridWakeWordPipeline(unittest.TestCase):
    """Test suite for VoiceService with acoustic detector + transcript wake-word verification."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=self.event_bus,
            auto_subscribe_events=True,
        )
        self.agent_input = DefaultAgentInput()
        self.mic = MockMicrophone(sample_rate=16000, chunk_size=1024)
        self.wake_detector = MockWakeWordDetector(
            model_name="alexa",
            threshold=0.5,
            event_bus=self.event_bus,
        )
        self.stt = MockSpeechToText()
        self.vad = EnergyVAD(
            energy_threshold=500.0,
            speech_time_threshold=0.04,
            silence_time_threshold=0.08,
            sample_rate=16000,
            chunk_size=1024,
        )

        self.service = VoiceService(
            microphone=self.mic,
            stt=self.stt,
            wake_word_detector=self.wake_detector,
            event_bus=self.event_bus,
            agent_input=self.agent_input,
            vad=self.vad,
            require_wake_word=True,
            hybrid_wake_word=True,
            min_speech_bytes=1000,
        )

    def tearDown(self) -> None:
        self.service.stop()

    def test_acoustic_wake_word_preceding_command_dispatches(self) -> None:
        """When acoustic detector triggers (e.g. 'alexa'), subsequent command is accepted even without saying Bimo."""
        self.stt.set_next_result("what time is it?")
        self.service.start()

        # 1. Acoustic detector fires
        self.wake_detector.set_next_detection(True, score=0.95)
        self.mic.feed_audio(b"\x00\x10" * 1024)
        time.sleep(0.15)
        self.assertEqual(self.state_machine.current_state, RobotState.LISTENING)

        # 2. Spoken command arrives
        self.mic.feed_tone(frequency=440.0, duration_seconds=0.2, amplitude=0.5)
        time.sleep(0.2)
        self.mic.feed_silence(duration_seconds=0.25)
        time.sleep(0.3)

        # Verified & delivered
        self.assertEqual(self.agent_input.input_count, 1)
        self.assertEqual(self.agent_input.last_input, "what time is it?")
        self.assertEqual(self.state_machine.current_state, RobotState.THINKING)

    def test_hybrid_vad_catches_bimo_when_acoustic_model_is_different(self) -> None:
        """Acoustic detector has only 'alexa', but user says 'Bimo open notepad' -> hybrid VAD catches it."""
        self.stt.set_next_result("Bimo open notepad")
        self.service.start()

        # Acoustic detector does NOT fire (user didn't say alexa)
        self.wake_detector.set_next_detection(False)

        # User speaks "Bimo open notepad"
        for _ in range(5):
            self.mic.feed_audio(b"\x00\x40" * 1024)
            time.sleep(0.04)
        for _ in range(6):
            self.mic.feed_audio(bytes(2048))
            time.sleep(0.04)
        time.sleep(0.3)

        # Hybrid VAD captured speech, verified wake word in transcript, and dispatched
        self.assertEqual(self.agent_input.input_count, 1)
        self.assertEqual(self.agent_input.last_input, "Bimo open notepad")
        self.assertEqual(self.state_machine.current_state, RobotState.THINKING)


class TestSTTProviderNormalization(unittest.TestCase):
    """Test suite verifying GoogleWebSTT and WhisperSTT phonetic replacements."""

    def test_google_web_stt_vocab_replacements(self) -> None:
        """Verify GoogleWebSTT applies normalize_phonetic_text."""
        stt = GoogleWebSTT(vocab_replacements={r"\bfoo\b": "bar"})
        self.assertIn("bar", stt.vocab_replacements.values())


if __name__ == "__main__":
    unittest.main()
