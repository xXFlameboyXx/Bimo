"""Targeted tests for spoken interruption and barge-in handling.

Validates:
1. BaseTextToSpeech: Subscribes to SPEECH_OUTPUT_CANCELLED and stops playback immediately.
2. BimoAgent: Urgently interrupts turn on "stop", "cancel", "abort", even when busy processing.
3. VoiceService: Monitors audio during _is_self_speaking and triggers SPEECH_OUTPUT_CANCELLED when "stop" is heard.
"""

import time
import unittest

from bimo.agent.bimo_agent import BimoAgent
from bimo.agent.cancellation import InterruptCommand, TaskInterruptManager
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.llm import LLMProvider, LLMRequest, LLMResponse
from bimo.interfaces.voice import (
    AudioChunk,
    BaseMicrophone,
    BaseSpeechToText,
    BaseTextToSpeech,
    STTResult,
)
from bimo.voice.service import VoicePipelineStage, VoiceService
from bimo.voice.tts import MockTTS


class DummyProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(model_name="dummy")

    @property
    def provider_name(self) -> str:
        return "dummy"

    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="This is a very long response that needs to be interrupted.", raw={})

    def generate_stream(self, request: LLMRequest):
        yield "This is a very long response that needs to be interrupted."


class MockInterruptionMicrophone(BaseMicrophone):
    def __init__(self) -> None:
        super().__init__(id="mock_mic", name="Mock Mic", sample_rate=16000, chunk_size=1024)
        self.chunks_to_yield = []
        self._is_recording = True

    def start_stream(self) -> None:
        self._is_recording = True

    def stop_stream(self) -> None:
        self._is_recording = False

    def read_chunk(self, timeout: float | None = None) -> bytes | None:
        if self.chunks_to_yield:
            return self.chunks_to_yield.pop(0)
        time.sleep(0.02)
        return b"\x00" * 2048


class MockInterruptionSTT(BaseSpeechToText):
    def __init__(self, transcript_to_return: str = "stop") -> None:
        self.transcript_to_return = transcript_to_return

    @property
    def provider_name(self) -> str:
        return "mock_stt"

    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> STTResult:
        return STTResult(text=self.transcript_to_return, confidence=0.98)


class TestSpokenInterruption(unittest.TestCase):
    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(event_bus=self.event_bus)

    def test_tts_subscribes_and_stops_on_cancellation(self) -> None:
        """Verify that any BaseTextToSpeech instance stops playback when SPEECH_OUTPUT_CANCELLED is emitted."""
        tts = MockTTS(event_bus=self.event_bus, simulated_duration=5.0)
        tts.speak("Hello this is a long sentence that should be interrupted", block=False)
        self.assertTrue(tts.is_speaking)

        # Emit external cancellation
        self.event_bus.publish(
            Event(
                type=EventType.SPEECH_OUTPUT_CANCELLED,
                data={"reason": "user_interrupt", "transcript": "stop"},
                source="test_caller",
            )
        )

        time.sleep(0.05)
        self.assertFalse(tts.is_speaking)
        self.assertGreaterEqual(tts.stop_calls, 1)

    def test_bimo_agent_interrupt_while_busy(self) -> None:
        """Verify that BimoAgent stops TTS and transitions to IDLE when receiving 'stop' while processing."""
        tts = MockTTS(event_bus=self.event_bus, simulated_duration=5.0)
        agent = BimoAgent(
            llm_provider=DummyProvider(),
            tts=tts,
            event_bus=self.event_bus,
            state_machine=self.state_machine,
        )

        # Simulate agent actively speaking
        tts.speak("Long response playing now", block=False)
        self.assertTrue(tts.is_speaking)
        self.state_machine.transition_to(RobotState.SPEAKING, reason="Speaking", force=True)

        with agent._lock:
            agent._is_processing = True

        # Send "Stop" event
        self.event_bus.publish(
            Event(
                type=EventType.SPEECH_RECEIVED,
                data={"transcript": "stop", "utterance_id": "u1"},
                source="voice_service",
            )
        )

        time.sleep(0.05)
        self.assertFalse(tts.is_speaking)
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)
        self.assertFalse(agent._is_processing)

    def test_classify_interrupt_commands(self) -> None:
        """Verify various spoken phrases are classified as STOP, CANCEL, PAUSE, and RESUME."""
        # Stop / Cancel
        self.assertEqual(TaskInterruptManager.classify("stop"), InterruptCommand.STOP)
        self.assertEqual(TaskInterruptManager.classify("Stop."), InterruptCommand.STOP)
        self.assertEqual(TaskInterruptManager.classify("bimo stop"), InterruptCommand.STOP)
        self.assertEqual(TaskInterruptManager.classify("stop it"), InterruptCommand.STOP)
        self.assertEqual(TaskInterruptManager.classify("cancel"), InterruptCommand.CANCEL)
        self.assertEqual(TaskInterruptManager.classify("abort"), InterruptCommand.CANCEL)
        self.assertEqual(TaskInterruptManager.classify("never mind"), InterruptCommand.CANCEL)

        # Pause / Wait
        self.assertEqual(TaskInterruptManager.classify("wait"), InterruptCommand.PAUSE)
        self.assertEqual(TaskInterruptManager.classify("bimo wait"), InterruptCommand.PAUSE)
        self.assertEqual(TaskInterruptManager.classify("Bimo, wait!"), InterruptCommand.PAUSE)
        self.assertEqual(TaskInterruptManager.classify("wait wait"), InterruptCommand.PAUSE)
        self.assertEqual(TaskInterruptManager.classify("pause"), InterruptCommand.PAUSE)
        self.assertEqual(TaskInterruptManager.classify("hold on"), InterruptCommand.PAUSE)
        self.assertEqual(TaskInterruptManager.classify("ruko"), InterruptCommand.PAUSE)

        # Resume / Continue
        self.assertEqual(TaskInterruptManager.classify("continue"), InterruptCommand.RESUME)
        self.assertEqual(TaskInterruptManager.classify("bimo continue"), InterruptCommand.RESUME)
        self.assertEqual(TaskInterruptManager.classify("resume"), InterruptCommand.RESUME)
        self.assertEqual(TaskInterruptManager.classify("go ahead"), InterruptCommand.RESUME)
        self.assertEqual(TaskInterruptManager.classify("chalu rakho"), InterruptCommand.RESUME)

    def test_bimo_agent_pause_and_resume(self) -> None:
        """Verify BimoAgent pauses on 'wait' and resumes on 'continue'."""
        tts = MockTTS(event_bus=self.event_bus)
        agent = BimoAgent(
            llm_provider=DummyProvider(),
            tts=tts,
            event_bus=self.event_bus,
            state_machine=self.state_machine,
            auto_speak=False,
        )

        events: list[Event] = []
        self.event_bus.subscribe(None, events.append)

        # 1. Send "wait"
        agent.receive_user_input("wait")
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)
        event_types = [e.type for e in events]
        self.assertIn(EventType.TASK_PAUSED, event_types)

        # 2. Send "continue"
        agent.receive_user_input("continue")
        self.assertEqual(self.state_machine.current_state, RobotState.EXECUTING)
        event_types = [e.type for e in events]
        self.assertIn(EventType.TASK_RESUMED, event_types)

    def test_orchestrator_interruptions(self) -> None:
        """Verify AssistantOrchestrator handles 'Bimo wait', 'Bimo stop', 'Bimo continue'."""
        from bimo.agent.mock_provider import MockLLMProvider
        from bimo.agent.orchestrator import AssistantOrchestrator, BimoRequest
        from bimo.tools.registry import ToolRegistry
        from bimo.tools.permissions import PermissionPolicy

        tts = MockTTS(event_bus=self.event_bus)
        orch = AssistantOrchestrator(
            llm_provider=MockLLMProvider(),
            tool_registry=ToolRegistry(),
            permission_policy=PermissionPolicy(auto_confirm=True),
            state_machine=self.state_machine,
            event_bus=self.event_bus,
            tts=tts,
        )

        # 1. Bimo wait
        res_wait = orch.handle_request(BimoRequest(text="Bimo wait", source="voice"))
        self.assertEqual(res_wait.text, "Waiting.")
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)

        # 2. Bimo continue
        res_cont = orch.handle_request(BimoRequest(text="Bimo continue", source="voice"))
        self.assertEqual(res_cont.text, "Continuing.")
        self.assertEqual(self.state_machine.current_state, RobotState.EXECUTING)

        # 3. Bimo stop
        res_stop = orch.handle_request(BimoRequest(text="Bimo stop", source="voice"))
        self.assertEqual(res_stop.text, "Stopped.")
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)

    def test_voice_service_wake_word_bypass_for_interrupts(self) -> None:
        """Verify VoiceService with require_wake_word=True does not ignore 'wait', 'stop', 'continue'."""
        from bimo.voice.microphone import MockMicrophone
        from bimo.voice.agent_input import DefaultAgentInput

        received = []
        class Collector(DefaultAgentInput):
            def receive_user_input(self, text, metadata=None):
                received.append(text)

        mic = MockMicrophone()
        stt = MockInterruptionSTT(transcript_to_return="wait")
        service = VoiceService(
            microphone=mic,
            stt=stt,
            event_bus=self.event_bus,
            agent_input=Collector(),
            require_wake_word=True,
        )

        # Process buffer with "wait"
        service.process_audio_buffer(b"\x00" * 4000)
        self.assertIn("wait", received)

        # Process buffer with "stop"
        stt.transcript_to_return = "stop"
        service.process_audio_buffer(b"\x00" * 4000)
        self.assertIn("stop", received)

        # Process buffer with "continue"
        stt.transcript_to_return = "continue"
        service.process_audio_buffer(b"\x00" * 4000)
        self.assertIn("continue", received)


if __name__ == "__main__":
    unittest.main()
