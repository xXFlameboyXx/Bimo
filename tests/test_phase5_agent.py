"""Comprehensive Phase 5 test suite for Bimo Agent, Context, and OmniRoute LLM provider."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from bimo.agent import (
    BimoAgent,
    ConversationContext,
    DEFAULT_BIMO_SYSTEM_PROMPT,
    MockLLMProvider,
    OmniRouteLLM,
    create_bimo_agent,
    create_llm_provider,
)
from bimo.core.config import Config, LLMConfig
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    TokenUsage,
    ToolCall,
)
from bimo.voice import MockTTS


class TestLLMConfiguration(unittest.TestCase):
    """Test configuration parsing and environment overrides for Phase 5 LLM settings."""

    def test_default_llm_config(self) -> None:
        cfg = LLMConfig()
        self.assertEqual(cfg.provider, "omniroute")
        self.assertEqual(cfg.model, "gemini-3.8-flash")
        self.assertEqual(cfg.base_url, "http://localhost:20128")
        self.assertEqual(cfg.timeout_seconds, 30.0)
        self.assertEqual(cfg.temperature, 0.7)
        self.assertEqual(cfg.max_history, 20)

    def test_from_env_with_omniroute_vars(self) -> None:
        env = {
            "LLM_PROVIDER": "omniroute",
            "OMNIROUTE_BASE_URL": "http://192.168.1.100:20128",
            "LLM_MODEL": "gemini-2.5-flash",
            "LLM_API_KEY": "test-key-123",
            "LLM_TIMEOUT": "45.5",
            "LLM_MAX_HISTORY": "15",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.from_env()
            self.assertEqual(cfg.llm.provider, "omniroute")
            self.assertEqual(cfg.llm.base_url, "http://192.168.1.100:20128")
            self.assertEqual(cfg.llm.model, "gemini-2.5-flash")
            self.assertEqual(cfg.llm.api_key, "test-key-123")
            self.assertEqual(cfg.llm.timeout_seconds, 45.5)
            self.assertEqual(cfg.llm.max_history, 15)


class TestConversationContext(unittest.TestCase):
    """Test conversation history, bounding, ordering, and reset behaviors."""

    def setUp(self) -> None:
        self.context = ConversationContext(max_history=4, system_prompt="Test System Prompt")

    def test_initial_state_contains_system_prompt(self) -> None:
        messages = self.context.get_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, Role.SYSTEM)
        self.assertEqual(messages[0].content, "Test System Prompt")
        self.assertEqual(self.context.message_count, 0)

    def test_turn_ordering_preserved(self) -> None:
        self.context.add_user_message("User 1")
        self.context.add_assistant_message("Assistant 1")
        self.context.add_user_message("User 2")
        self.context.add_assistant_message("Assistant 2")

        messages = self.context.get_messages()
        self.assertEqual(len(messages), 5)  # 1 system + 4 history
        self.assertEqual(messages[0].role, Role.SYSTEM)
        self.assertEqual(messages[1].content, "User 1")
        self.assertEqual(messages[2].content, "Assistant 1")
        self.assertEqual(messages[3].content, "User 2")
        self.assertEqual(messages[4].content, "Assistant 2")
        self.assertEqual(self.context.message_count, 4)

    def test_bounded_history_prunes_oldest_non_system_turns(self) -> None:
        # Context max_history is 4
        self.context.add_user_message("User 1")
        self.context.add_assistant_message("Assistant 1")
        self.context.add_user_message("User 2")
        self.context.add_assistant_message("Assistant 2")
        # Add 5th and 6th messages: oldest (User 1, Assistant 1) should be pruned
        self.context.add_user_message("User 3")
        self.context.add_assistant_message("Assistant 3")

        messages = self.context.get_messages()
        self.assertEqual(len(messages), 5)  # 1 system + 4 history
        self.assertEqual(messages[0].role, Role.SYSTEM)
        self.assertEqual(messages[1].content, "User 2")
        self.assertEqual(messages[2].content, "Assistant 2")
        self.assertEqual(messages[3].content, "User 3")
        self.assertEqual(messages[4].content, "Assistant 3")
        self.assertEqual(self.context.message_count, 4)

    def test_clear_resets_turns_and_keeps_system_prompt(self) -> None:
        self.context.add_user_message("Hello")
        self.context.add_assistant_message("Hi")
        self.assertEqual(self.context.message_count, 2)

        self.context.clear()
        self.assertEqual(self.context.message_count, 0)
        messages = self.context.get_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, Role.SYSTEM)
        self.assertEqual(messages[0].content, "Test System Prompt")

    def test_reset_with_new_system_prompt(self) -> None:
        self.context.add_user_message("Hello")
        self.context.reset(system_prompt="New System Prompt")
        self.assertEqual(self.context.message_count, 0)
        messages = self.context.get_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].content, "New System Prompt")

    def test_last_messages_properties(self) -> None:
        self.assertIsNone(self.context.last_user_message)
        self.assertIsNone(self.context.last_assistant_message)

        self.context.add_user_message("Query A")
        self.assertEqual(self.context.last_user_message, "Query A")

        self.context.add_assistant_message("Response A")
        self.assertEqual(self.context.last_assistant_message, "Response A")


class TestOmniRouteProvider(unittest.TestCase):
    """Test suite for OmniRouteLLM HTTP client handling."""

    def setUp(self) -> None:
        self.provider = OmniRouteLLM(
            model_name="gemini-3.8-flash",
            api_key="secret-api-key",
            base_url="http://localhost:20128",
            timeout_seconds=5.0,
        )

    def test_endpoint_url_resolution(self) -> None:
        p1 = OmniRouteLLM("m", base_url="http://localhost:20128")
        self.assertEqual(p1.endpoint_url, "http://localhost:20128/v1/chat/completions")

        p2 = OmniRouteLLM("m", base_url="http://localhost:20128/v1")
        self.assertEqual(p2.endpoint_url, "http://localhost:20128/v1/chat/completions")

        p3 = OmniRouteLLM("m", base_url="http://localhost:20128/v1/chat/completions")
        self.assertEqual(p3.endpoint_url, "http://localhost:20128/v1/chat/completions")

    @patch("urllib.request.urlopen")
    def test_successful_model_response(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "model": "gemini-3.8-flash",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello paneer! I am Bimo."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        req = LLMRequest(messages=[Message(role=Role.USER, content="Hello")])
        resp = self.provider.generate(req)

        self.assertFalse(resp.is_error)
        self.assertEqual(resp.content, "Hello paneer! I am Bimo.")
        self.assertEqual(resp.finish_reason, "stop")
        self.assertIsNotNone(resp.usage)
        assert resp.usage is not None
        self.assertEqual(resp.usage.total_tokens, 20)

    @patch("urllib.request.urlopen")
    def test_provider_timeout_handling(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = TimeoutError("Connection timed out")

        req = LLMRequest(messages=[Message(role=Role.USER, content="Hello")])
        resp = self.provider.generate(req)

        self.assertTrue(resp.is_error)
        self.assertEqual(resp.content, "")
        self.assertIn("timed out", resp.error or "")

    @patch("urllib.request.urlopen")
    def test_connection_refused_handling(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        req = LLMRequest(messages=[Message(role=Role.USER, content="Hello")])
        resp = self.provider.generate(req)

        self.assertTrue(resp.is_error)
        self.assertEqual(resp.content, "")
        self.assertIn("Connection refused", resp.error or "")

    @patch("urllib.request.urlopen")
    def test_http_error_handling(self, mock_urlopen: MagicMock) -> None:
        error_body = io.BytesIO(json.dumps({"error": "Rate limit exceeded"}).encode("utf-8"))
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://localhost:20128",
            code=429,
            msg="Too Many Requests",
            hdrs={},  # type: ignore
            fp=error_body,
        )

        req = LLMRequest(messages=[Message(role=Role.USER, content="Hello")])
        resp = self.provider.generate(req)

        self.assertTrue(resp.is_error)
        self.assertEqual(resp.content, "")
        self.assertIn("429", resp.error or "")
        self.assertIn("Rate limit exceeded", resp.error or "")

    @patch("urllib.request.urlopen")
    def test_malformed_json_response(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html>502 Bad Gateway</html>"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        req = LLMRequest(messages=[Message(role=Role.USER, content="Hello")])
        resp = self.provider.generate(req)

        self.assertTrue(resp.is_error)
        self.assertIn("Malformed JSON", resp.error or "")

    @patch("urllib.request.urlopen")
    def test_empty_choices_response(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"choices": []}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        req = LLMRequest(messages=[Message(role=Role.USER, content="Hello")])
        resp = self.provider.generate(req)

        self.assertTrue(resp.is_error)
        self.assertIn("Empty choices", resp.error or "")

    @patch("urllib.request.urlopen")
    def test_tool_call_parsed_safely_without_execution(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "model": "gemini-3.8-flash",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "I would like to turn off the lights.",
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "turn_off_lights",
                                    "arguments": json.dumps({"room": "living_room"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        req = LLMRequest(messages=[Message(role=Role.USER, content="Turn off the lights")])
        resp = self.provider.generate(req)

        self.assertFalse(resp.is_error)
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(len(resp.tool_calls), 1)
        self.assertEqual(resp.tool_calls[0].name, "turn_off_lights")
        self.assertEqual(resp.tool_calls[0].arguments, {"room": "living_room"})


class TestMockLLMProvider(unittest.TestCase):
    """Test suite for MockLLMProvider."""

    def test_default_and_queued_responses(self) -> None:
        provider = MockLLMProvider(default_response="Default Response")
        req = LLMRequest(messages=[Message(role=Role.USER, content="Hello")])

        # Default response
        r1 = provider.generate(req)
        self.assertEqual(r1.content, "Default Response")

        # Enqueued response
        provider.enqueue_response("Custom Queued Response")
        r2 = provider.generate(req)
        self.assertEqual(r2.content, "Custom Queued Response")

        # Back to default
        r3 = provider.generate(req)
        self.assertEqual(r3.content, "Default Response")

    def test_simulated_error(self) -> None:
        provider = MockLLMProvider()
        provider.set_error("Simulated API Error")
        req = LLMRequest(messages=[Message(role=Role.USER, content="Hello")])
        resp = provider.generate(req)
        self.assertTrue(resp.is_error)
        self.assertEqual(resp.error, "Simulated API Error")


class TestBimoAgent(unittest.TestCase):
    """Test suite for BimoAgent reasoning, state transitions, and event flow."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=self.event_bus,
            auto_subscribe_events=True,
        )
        self.tts = MockTTS(event_bus=self.event_bus, simulated_duration=0.1)
        self.llm_provider = MockLLMProvider(default_response="I am Bimo, ready to help.")
        self.context = ConversationContext(max_history=6, system_prompt=DEFAULT_BIMO_SYSTEM_PROMPT)

        self.agent = BimoAgent(
            llm_provider=self.llm_provider,
            tts=self.tts,
            context=self.context,
            event_bus=self.event_bus,
            state_machine=self.state_machine,
            auto_speak=True,
        )

    def test_full_pipeline_user_speech_to_tts_output(self) -> None:
        events: list[Event] = []
        self.event_bus.subscribe(None, events.append)

        # Deliver user text to agent
        self.agent.receive_user_input("What is your name?")

        # Verify LLM call occurred
        self.assertEqual(len(self.llm_provider.generate_calls), 1)
        sent_messages = self.llm_provider.generate_calls[0].messages
        self.assertEqual(sent_messages[0].role, Role.SYSTEM)
        self.assertIn("Bimo", sent_messages[0].content)
        self.assertEqual(sent_messages[1].role, Role.USER)
        self.assertEqual(sent_messages[1].content, "What is your name?")

        # Verify conversation context updated
        self.assertEqual(self.context.message_count, 2)
        self.assertEqual(self.context.last_user_message, "What is your name?")
        self.assertEqual(self.context.last_assistant_message, "I am Bimo, ready to help.")

        # Verify domain events
        event_types = [e.type for e in events]
        self.assertIn(EventType.AI_STARTED, event_types)
        self.assertIn(EventType.AI_FINISHED, event_types)
        self.assertIn(EventType.SPEECH_OUTPUT_STARTED, event_types)

        # Verify TTS received output
        self.assertIn("I am Bimo, ready to help.", self.tts.spoken_texts)

    def test_event_driven_trigger_from_speech_received(self) -> None:
        """Verify that EventType.SPEECH_RECEIVED triggers BimoAgent automatically."""
        self.event_bus.publish(
            Event(
                type=EventType.SPEECH_RECEIVED,
                data={"transcript": "How are you?"},
                source="test",
            )
        )

        self.assertEqual(self.context.last_user_message, "How are you?")
        self.assertIn("I am Bimo, ready to help.", self.tts.spoken_texts)

    def test_llm_error_dispatches_ai_error_and_transitions_to_error(self) -> None:
        self.llm_provider.set_error("OmniRoute server connection refused")
        events: list[Event] = []
        self.event_bus.subscribe(None, events.append)

        self.agent.receive_user_input("Tell me a story.")

        event_types = [e.type for e in events]
        self.assertIn(EventType.AI_STARTED, event_types)
        self.assertIn(EventType.AI_ERROR, event_types)

        # State machine should transition to ERROR
        self.assertEqual(self.state_machine.current_state, RobotState.ERROR)

        # Fallback message spoken
        self.assertIn(self.agent.fallback_error_message, self.tts.spoken_texts)

    def test_no_real_tool_execution_safety_boundary(self) -> None:
        """Verify that tool calls returned by the model are NOT executed in Phase 5."""
        tool_call = ToolCall(
            id="tool_1",
            name="execute_shell_command",
            arguments={"command": "rm -rf /"},
        )
        self.llm_provider.enqueue_response(
            content="",
            tool_calls=[tool_call],
        )

        resp = self.agent.process_turn("Run a command")

        # Tool calls safely preserved in LLMResponse
        self.assertEqual(len(resp.tool_calls), 1)
        self.assertEqual(resp.tool_calls[0].name, "execute_shell_command")

        # Agent provides honest explanation that tools are not available
        self.assertIn(self.agent.TOOL_UNAVAILABLE_FALLBACK, self.tts.spoken_texts)

        # No subprocess or OS command was executed
        self.assertNotIn("rm -rf", self.tts.spoken_texts[-1])

    def test_factory_methods(self) -> None:
        provider = create_llm_provider(mock=True)
        self.assertIsInstance(provider, MockLLMProvider)

        agent = create_bimo_agent(mock=True)
        self.assertIsInstance(agent, BimoAgent)
        self.assertIsInstance(agent.llm_provider, MockLLMProvider)


if __name__ == "__main__":
    unittest.main()
