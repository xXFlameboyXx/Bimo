"""Comprehensive test suite for Phase 6: Secure Tool System.

Verifies:
1. ToolRegistry: registration, duplicate protection, unregister, deterministic listing, unknown tool rejection.
2. Argument Validation: required parameters, strict parameter checking, type mismatches, enum validation.
3. Permission System: SAFE, CONFIRM (with/without confirmation), HIGH_RISK, tamper-resistance.
4. Tool Execution: success, exception handling, timeout protection, serialization & output bounding.
5. Agent Tool Loop: single tool call, multiple tool calls, multi-turn tool reasoning loop, max iterations limit.
6. Initial Robot Tools: robot.speak, robot.set_face, robot.get_status.
7. Security: rejection of arbitrary tool names, shell command prevention, eval/exec avoidance.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bimo.agent.bimo_agent import BimoAgent
from bimo.agent.context import ConversationContext
from bimo.agent.mock_provider import MockLLMProvider
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.llm import LLMRequest, LLMResponse, Message, Role, ToolCall
from bimo.tools.base import BaseTool, ToolParameter, ToolResult
from bimo.tools.mock_tools import (
    MockConfirmTool,
    MockEchoTool,
    MockFailingTool,
    MockHighRiskTool,
    MockMultiTypeTool,
    MockTimeoutTool,
)
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry
from bimo.tools.robot_tools import (
    RobotGetStatusTool,
    RobotSetFaceTool,
    RobotSpeakTool,
)
from bimo.voice import MockTTS


class TestToolRegistry(unittest.TestCase):
    """Test suite for ToolRegistry registration, lookups, and discovery."""

    def setUp(self) -> None:
        self.registry = ToolRegistry()

    def test_register_and_get_tool(self) -> None:
        tool = MockEchoTool("test.echo")
        self.registry.register(tool)
        self.assertTrue(self.registry.has("test.echo"))
        self.assertEqual(self.registry.get("test.echo"), tool)

    def test_duplicate_registration_without_overwrite_raises_value_error(self) -> None:
        tool1 = MockEchoTool("test.echo")
        tool2 = MockEchoTool("test.echo")
        self.registry.register(tool1)
        with self.assertRaises(ValueError) as ctx:
            self.registry.register(tool2, overwrite=False)
        self.assertIn("already registered", str(ctx.exception))

    def test_duplicate_registration_with_overwrite_replaces_tool(self) -> None:
        tool1 = MockEchoTool("test.echo")
        tool2 = MockEchoTool("test.echo")
        self.registry.register(tool1)
        self.registry.register(tool2, overwrite=True)
        self.assertEqual(self.registry.get("test.echo"), tool2)

    def test_invalid_tool_registration_rejected(self) -> None:
        # Non-BaseTool instance
        with self.assertRaises(TypeError):
            self.registry.register("not_a_tool")  # type: ignore

        # Invalid name pattern
        invalid_tool = MockEchoTool("invalid tool name with spaces!")
        with self.assertRaises(ValueError):
            self.registry.register(invalid_tool)

    def test_unregister_tool(self) -> None:
        tool = MockEchoTool("test.echo")
        self.registry.register(tool)
        self.assertTrue(self.registry.unregister("test.echo"))
        self.assertFalse(self.registry.has("test.echo"))
        self.assertFalse(self.registry.unregister("test.echo"))

    def test_deterministic_alphabetical_listing(self) -> None:
        self.registry.register(MockEchoTool("zeta.tool"))
        self.registry.register(MockEchoTool("alpha.tool"))
        self.registry.register(MockEchoTool("beta.tool"))

        names = self.registry.list_names()
        self.assertEqual(names, ["alpha.tool", "beta.tool", "zeta.tool"])

        schemas = self.registry.get_schemas()
        schema_names = [s["function"]["name"] for s in schemas]
        self.assertEqual(schema_names, ["alpha.tool", "beta.tool", "zeta.tool"])

    def test_clear_registry(self) -> None:
        self.registry.register(MockEchoTool("t1"))
        self.registry.register(MockEchoTool("t2"))
        self.registry.clear()
        self.assertEqual(len(self.registry.list_tools()), 0)

    def test_execute_unknown_tool_returns_structured_error(self) -> None:
        res = self.registry.execute("completely.unknown.tool")
        self.assertFalse(res.success)
        self.assertIn("Unknown tool", str(res.error))
        self.assertEqual(res.tool_name, "completely.unknown.tool")


class TestArgumentValidation(unittest.TestCase):
    """Test suite for strict argument and type validation in BaseTool."""

    def setUp(self) -> None:
        self.tool = MockMultiTypeTool()

    def test_valid_arguments_pass(self) -> None:
        valid_args = {
            "str_val": "hello",
            "int_val": 42,
            "num_val": 3.14,
            "bool_val": True,
            "arr_val": [1, 2, 3],
            "obj_val": {"key": "val"},
            "choice": "beta",
        }
        ok, err = self.tool.validate_arguments(valid_args)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_missing_required_argument(self) -> None:
        ok, err = self.tool.validate_arguments({"str_val": "hello"})
        self.assertFalse(ok)
        self.assertIn("Missing required parameter 'int_val'", str(err))

    def test_wrong_type_string_instead_of_int(self) -> None:
        ok, err = self.tool.validate_arguments({"str_val": "hello", "int_val": "not_an_int"})
        self.assertFalse(ok)
        self.assertIn("must be an integer", str(err))

    def test_boolean_rejected_for_integer(self) -> None:
        # In Python bool is a subclass of int; our validator explicitly rejects bool for int
        ok, err = self.tool.validate_arguments({"str_val": "hello", "int_val": True})
        self.assertFalse(ok)
        self.assertIn("must be an integer", str(err))

    def test_unexpected_parameter_rejected_under_strict_mode(self) -> None:
        ok, err = self.tool.validate_arguments({
            "str_val": "hello",
            "int_val": 42,
            "unregistered_param": "malicious_payload",
        })
        self.assertFalse(ok)
        self.assertIn("Unexpected argument 'unregistered_param'", str(err))

    def test_enum_validation(self) -> None:
        ok, err = self.tool.validate_arguments({
            "str_val": "hello",
            "int_val": 10,
            "choice": "invalid_choice",
        })
        self.assertFalse(ok)
        self.assertIn("is not in allowed choices", str(err))


class TestPermissionPolicy(unittest.TestCase):
    """Test suite for SAFE, CONFIRM, and HIGH_RISK permission policies."""

    def setUp(self) -> None:
        self.policy = PermissionPolicy(allow_high_risk=False)
        self.safe_tool = MockEchoTool("safe.tool", permission=ToolPermission.SAFE)
        self.confirm_tool = MockConfirmTool("confirm.tool")
        self.high_risk_tool = MockHighRiskTool("high_risk.tool")

    def test_safe_tool_allowed_by_default(self) -> None:
        allowed, err = self.policy.check_permission(self.safe_tool)
        self.assertTrue(allowed)
        self.assertIsNone(err)

    def test_confirm_tool_denied_without_confirmation(self) -> None:
        allowed, err = self.policy.check_permission(self.confirm_tool, context={})
        self.assertFalse(allowed)
        self.assertIn("requires explicit user confirmation", str(err))

    def test_confirm_tool_allowed_with_confirmation(self) -> None:
        allowed, err = self.policy.check_permission(self.confirm_tool, context={"confirmed": True})
        self.assertTrue(allowed)
        self.assertIsNone(err)

    def test_high_risk_tool_prohibited_when_policy_disables_it(self) -> None:
        allowed, err = self.policy.check_permission(
            self.high_risk_tool, context={"elevated_confirmed": True}
        )
        self.assertFalse(allowed)
        self.assertIn("prohibited by current policy", str(err))

    def test_high_risk_tool_requires_elevated_confirmation_when_policy_permits(self) -> None:
        permissive_policy = PermissionPolicy(allow_high_risk=True)
        # Without confirmation: denied
        allowed, err = permissive_policy.check_permission(self.high_risk_tool, context={})
        self.assertFalse(allowed)
        self.assertIn("requires elevated confirmation", str(err))

        # With elevated confirmation: allowed
        allowed, err = permissive_policy.check_permission(
            self.high_risk_tool, context={"elevated_confirmed": True}
        )
        self.assertTrue(allowed)
        self.assertIsNone(err)


class TestToolExecution(unittest.TestCase):
    """Test suite for execution exception handling, timeout protection, and output truncation."""

    def setUp(self) -> None:
        self.registry = ToolRegistry()

    def test_successful_execution(self) -> None:
        self.registry.register(MockEchoTool("echo"))
        res = self.registry.execute("echo", message="Hello Robot")
        self.assertTrue(res.success)
        self.assertEqual(res.output, "Echo: Hello Robot")
        self.assertEqual(res.tool_name, "echo")

    def test_execution_catches_unhandled_exception(self) -> None:
        self.registry.register(MockFailingTool())
        res = self.registry.execute("mock.failing")
        self.assertFalse(res.success)
        self.assertIn("Hardware communication error", str(res.error))
        self.assertEqual(res.tool_name, "mock.failing")

    def test_execution_timeout_protection(self) -> None:
        self.registry.register(MockTimeoutTool(sleep_seconds=0.3, timeout=0.05))
        res = self.registry.execute("mock.timeout")
        self.assertFalse(res.success)
        self.assertIn("timed out after 0.1s", str(res.error))

    def test_oversized_tool_output_truncation(self) -> None:
        oversized = "A" * 5000
        res = ToolResult(success=True, output=oversized, tool_name="big.tool")
        serialized = res.to_json(max_length=500)
        self.assertLessEqual(len(serialized), 550)
        self.assertIn("[output truncated]", serialized)


class TestInitialRobotTools(unittest.TestCase):
    """Test suite for Phase 6 initial tools: robot.speak, robot.set_face, robot.get_status."""

    def setUp(self) -> None:
        self.tts = MockTTS()
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(event_bus=self.event_bus)

    def test_robot_speak_tool_success(self) -> None:
        tool = RobotSpeakTool(tts=self.tts)
        self.assertEqual(tool.permission, ToolPermission.SAFE)

        res = tool.execute(text="Hello world!")
        self.assertTrue(res.success)
        self.assertIn("Hello world!", self.tts.spoken_texts)

    def test_robot_speak_tool_validation(self) -> None:
        tool = RobotSpeakTool(tts=self.tts)
        # Empty text rejected
        res = tool.execute(text="   ")
        self.assertFalse(res.success)

        # Missing text rejected by argument validator
        ok, err = tool.validate_arguments({})
        self.assertFalse(ok)
        self.assertIn("Missing required parameter 'text'", str(err))

        # Unexpected extra argument rejected
        ok, err = tool.validate_arguments({"text": "Hi", "unknown": 123})
        self.assertFalse(ok)
        self.assertIn("Unexpected argument", str(err))

    def test_robot_set_face_tool_success(self) -> None:
        tool = RobotSetFaceTool(state_machine=self.state_machine, event_bus=self.event_bus)
        self.assertEqual(tool.permission, ToolPermission.SAFE)

        res = tool.execute(face="happy")
        self.assertTrue(res.success)
        self.assertEqual(self.state_machine.current_state, RobotState.SUCCESS)

        res2 = tool.execute(face="listening")
        self.assertTrue(res2.success)
        self.assertEqual(self.state_machine.current_state, RobotState.LISTENING)

    def test_robot_set_face_tool_rejects_invalid_face(self) -> None:
        tool = RobotSetFaceTool(state_machine=self.state_machine)
        ok, err = tool.validate_arguments({"face": "non_existent_face"})
        self.assertFalse(ok)
        self.assertIn("not in allowed choices", str(err))

    def test_robot_get_status_tool(self) -> None:
        tool = RobotGetStatusTool(state_machine=self.state_machine, tts=self.tts)
        self.assertEqual(tool.permission, ToolPermission.SAFE)

        res = tool.execute()
        self.assertTrue(res.success)
        self.assertIsInstance(res.output, dict)
        self.assertEqual(res.output["robot_name"], "Bimo")
        self.assertEqual(res.output["current_state"], "IDLE")
        self.assertIn("subsystems", res.output)

        # Reject unexpected arguments
        ok, err = tool.validate_arguments({"secret_key": "attempt"})
        self.assertFalse(ok)
        self.assertIn("Unexpected argument", str(err))


class TestAgentToolLoop(unittest.TestCase):
    """Test suite for tool call execution within BimoAgent's reasoning loop."""

    def setUp(self) -> None:
        self.llm_provider = MockLLMProvider()
        self.tts = MockTTS()
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(event_bus=self.event_bus)
        self.context = ConversationContext()
        self.registry = ToolRegistry()
        self.registry.register(MockEchoTool("test.echo"))
        self.registry.register(RobotSpeakTool(tts=self.tts))
        self.registry.register(RobotSetFaceTool(state_machine=self.state_machine))
        self.registry.register(RobotGetStatusTool(state_machine=self.state_machine, tts=self.tts))

        self.agent = BimoAgent(
            llm_provider=self.llm_provider,
            tts=self.tts,
            context=self.context,
            event_bus=self.event_bus,
            state_machine=self.state_machine,
            tool_registry=self.registry,
            tools_enabled=True,
            max_tool_iterations=5,
        )

    def test_normal_no_tool_response(self) -> None:
        self.llm_provider.enqueue_response("I am doing great, thank you!")
        events: list[Event] = []
        self.event_bus.subscribe(None, events.append)

        resp = self.agent.process_turn("How are you?")
        self.assertEqual(resp.content, "I am doing great, thank you!")
        self.assertIn("I am doing great, thank you!", self.tts.spoken_texts)
        self.assertNotIn(EventType.TOOL_EXECUTION_STARTED, [e.type for e in events])

    def test_single_tool_call_flow(self) -> None:
        """Verify: User -> LLM tool call -> execute tool -> LLM receives result -> final response."""
        tool_call = ToolCall(id="call_1", name="test.echo", arguments={"message": "Ping Bimo"})
        # 1st LLM call: requests tool
        self.llm_provider.enqueue_response(content="", tool_calls=[tool_call])
        # 2nd LLM call: returns final answer after seeing tool result
        self.llm_provider.enqueue_response(content="I received the echo: Ping Bimo.")

        events: list[Event] = []
        self.event_bus.subscribe(None, events.append)

        resp = self.agent.process_turn("Echo ping")
        self.assertEqual(resp.content, "I received the echo: Ping Bimo.")

        # Verify event sequence
        event_types = [e.type for e in events]
        self.assertIn(EventType.AI_STARTED, event_types)
        self.assertIn(EventType.TOOL_CALL_REQUESTED, event_types)
        self.assertIn(EventType.TOOL_EXECUTION_STARTED, event_types)
        self.assertIn(EventType.TOOL_EXECUTION_COMPLETED, event_types)
        self.assertIn(EventType.AI_FINISHED, event_types)

        # Spoken text delivered to TTS
        self.assertIn("I received the echo: Ping Bimo.", self.tts.spoken_texts)

    def test_multiple_tool_calls_in_single_turn(self) -> None:
        tc1 = ToolCall(id="c1", name="robot.set_face", arguments={"face": "happy"})
        tc2 = ToolCall(id="c2", name="test.echo", arguments={"message": "smile"})

        self.llm_provider.enqueue_response(content="", tool_calls=[tc1, tc2])
        self.llm_provider.enqueue_response(content="Face set and echoed successfully.")

        resp = self.agent.process_turn("Set face to happy and echo smile")
        self.assertEqual(resp.content, "Face set and echoed successfully.")
        self.assertIn(self.state_machine.current_state, (RobotState.SPEAKING, RobotState.IDLE))
        self.assertIn("Face set and echoed successfully.", self.tts.spoken_texts)

    def test_repeated_tool_iterations(self) -> None:
        """Verify multi-iteration loop: Tool 1 -> Tool 2 -> Final response."""
        tc1 = ToolCall(id="c1", name="test.echo", arguments={"message": "step 1"})
        tc2 = ToolCall(id="c2", name="test.echo", arguments={"message": "step 2"})

        self.llm_provider.enqueue_response(content="", tool_calls=[tc1])
        self.llm_provider.enqueue_response(content="", tool_calls=[tc2])
        self.llm_provider.enqueue_response(content="Completed both steps.")

        resp = self.agent.process_turn("Execute steps")
        self.assertEqual(resp.content, "Completed both steps.")
        self.assertEqual(self.llm_provider.call_count, 3)

    def test_max_tool_iterations_limit_breaks_infinite_loop(self) -> None:
        """Verify that agent terminates loop when model requests tools indefinitely."""
        infinite_tc = ToolCall(id="loop_tc", name="test.echo", arguments={"message": "loop"})
        # Enqueue more tool calls than max_tool_iterations (5)
        for _ in range(8):
            self.llm_provider.enqueue_response(content="", tool_calls=[infinite_tc])

        resp = self.agent.process_turn("Loop forever")
        self.assertEqual(self.llm_provider.call_count, 5)
        self.assertIn("maximum tool execution limit", resp.content)

    def test_permission_denied_in_agent_loop(self) -> None:
        """Verify that an unauthorized tool returns structured error and emits TOOL_EXECUTION_DENIED."""
        self.registry.register(MockConfirmTool("unconfirmed.action"))
        tc = ToolCall(id="tc_deny", name="unconfirmed.action", arguments={"action_id": "delete_all"})

        self.llm_provider.enqueue_response(content="", tool_calls=[tc])
        self.llm_provider.enqueue_response(content="I cannot perform that action without confirmation.")

        events: list[Event] = []
        self.event_bus.subscribe(None, events.append)

        resp = self.agent.process_turn("Perform dangerous action", metadata={"confirmed": False})
        self.assertEqual(resp.content, "I cannot perform that action without confirmation.")

        event_types = [e.type for e in events]
        self.assertIn(EventType.TOOL_EXECUTION_DENIED, event_types)
        self.assertIn(EventType.TOOL_EXECUTION_FAILED, event_types)


class TestSecurityAndIsolation(unittest.TestCase):
    """Test suite ensuring strict security, sandbox isolation, and prevention of arbitrary code execution."""

    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.registry.register(MockEchoTool("safe.tool"))

    def test_arbitrary_tool_name_never_dynamically_imports_or_executes(self) -> None:
        malicious_names = [
            "os.system",
            "subprocess.Popen",
            "builtins.eval",
            "builtins.exec",
            "shutil.rmtree",
            "__import__",
        ]
        for name in malicious_names:
            res = self.registry.execute(name)
            self.assertFalse(res.success)
            self.assertIn("Unknown tool", str(res.error))

    def test_schema_only_exposes_explicitly_registered_tools(self) -> None:
        schemas = self.registry.get_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "safe.tool")

        # Unregistered categories must never appear in schemas
        schema_text = str(schemas)
        self.assertNotIn("shell", schema_text)
        self.assertNotIn("computer", schema_text)
        self.assertNotIn("lights", schema_text)
        self.assertNotIn("filesystem", schema_text)


if __name__ == "__main__":
    unittest.main()
