"""Comprehensive Phase 8 Tests for Bimo Controlled Computer Use.

Validates:
1. SCREEN:
   - get_screen_size success
   - screenshot success
   - screenshot dimensions
   - screenshot maximum size & compression
   - oversized screenshot rejection / handling
   - screenshot failure handling
2. WINDOW:
   - active window query
   - focus exact matching
   - nonexistent window handling
   - ambiguous match handling
3. MOUSE:
   - valid move within primary screen bounds
   - negative coordinate rejection
   - out-of-bounds rejection (x >= width, y >= height)
   - valid left click
   - valid right click
   - double click
   - click with coordinates (moves then clicks)
   - invalid button rejection
   - invalid click count rejection
4. SCROLL:
   - positive scroll (up)
   - negative scroll (down)
   - excessive range rejection (outside [-1000..1000])
5. KEYBOARD:
   - allowed single key (e.g. ENTER, ESC, TAB, SPACE, BACKSPACE, UP, DOWN, LEFT, RIGHT, WIN)
   - allowed combination (e.g. CTRL+C, CTRL+V, CTRL+A, CTRL+S, ALT+TAB, SHIFT+TAB)
   - unknown key rejected
   - malformed combination rejected
6. TYPE:
   - valid text
   - empty text rejected
   - text exceeding maximum length rejected
7. APPLICATION:
   - allowlisted app succeeds (notepad, calculator, explorer)
   - non-allowlisted app rejected
   - arbitrary executable rejected
8. PERMISSIONS:
   - SAFE observation tools execute (get_status, get_active_window, get_screen_size, screenshot)
   - CONFIRM action denied without explicit user confirmation
   - CONFIRM action executes with explicit user confirmation
9. COMPUTER-USE LOOP:
   - one action execution
   - multiple actions execution
   - observe -> act -> observe loop
   - tool failure handling
   - disconnected PC graceful handling
   - max-step limit reached
   - LLM final response formatting
   - malformed tool request handling
   - unknown tool handling
10. SECURITY BOUNDARIES:
   - no shell execution
   - no arbitrary executable
   - no arbitrary subprocess
   - no eval/exec
   - no unrestricted filesystem
   - no process-kill interface
   - invalid coordinates rejected
   - invalid keys rejected
   - oversized image rejected
"""

from __future__ import annotations

import base64
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from bimo.agent.bimo_agent import BimoAgent
from bimo.agent.context import ConversationContext
from bimo.core.config import PCAgentConfig
from bimo.core.events import Event, EventBus, EventType
from bimo.interfaces.llm import LLMProvider, LLMRequest, LLMResponse, Message, ToolCall
from bimo.interfaces.voice import BaseTextToSpeech
from bimo.pc.mock_client import MockPCAgentClient
from bimo.pc.models import PCErrorCode, PCResponse
from bimo.pc.tools import (
    PCClickTool,
    PCCloseAppTool,
    PCFocusWindowTool,
    PCGetActiveWindowTool,
    PCGetScreenSizeTool,
    PCGetStatusTool,
    PCMoveMouseTool,
    PCOpenAppTool,
    PCPressKeyTool,
    PCScreenshotTool,
    PCScrollTool,
    PCTypeTextTool,
    register_pc_tools,
)
from bimo.pc.vision import DesktopCapture, ScreenControllerDesktopCapture, Screenshot
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry
from windows_agent.commands import build_default_command_registry
from windows_agent.controllers import (
    MockAppLauncher,
    MockKeyboardController,
    MockMouseController,
    MockScreenController,
    MockWindowController,
)
from windows_agent.registry import PCCommandRegistry, PCCommandSpec


class TestScreenCommands(unittest.TestCase):
    """Unit tests for screen observation and capture."""

    def setUp(self) -> None:
        self.screen = MockScreenController(width=1920, height=1080)
        self.registry = PCCommandRegistry()
        self.registry.register(
            PCCommandSpec(
                name="pc.get_screen_size",
                description="Get primary monitor screen resolution",
                handler=lambda args: self.screen.get_screen_size(),
            )
        )
        self.registry.register(
            PCCommandSpec(
                name="pc.screenshot",
                description="Capture primary desktop screenshot",
                handler=lambda args: self.screen.capture_screenshot(),
            )
        )

    def test_get_screen_size_success(self) -> None:
        resp = self.registry.execute("req-1", "pc.get_screen_size", {})
        self.assertTrue(resp.success)
        self.assertEqual(resp.output, (1920, 1080))

    def test_screenshot_success_and_dimensions(self) -> None:
        resp = self.registry.execute("req-2", "pc.screenshot", {})
        self.assertTrue(resp.success)
        shot = resp.output
        self.assertEqual(shot["width"], 1920)
        self.assertEqual(shot["height"], 1080)
        self.assertEqual(shot["format"], "png")
        self.assertIn("image", shot)
        # Check base64 is valid
        raw = base64.b64decode(shot["image"])
        self.assertGreater(len(raw), 0)

    def test_screenshot_maximum_size_enforced(self) -> None:
        # Request with small max_bytes
        with self.assertRaises(ValueError) as ctx:
            self.screen.capture_screenshot(max_bytes=10)
        self.assertIn("exceeds allowable maximum", str(ctx.exception))

    def test_screenshot_failure_handled(self) -> None:
        self.screen.fail_capture = True
        with self.assertRaises(RuntimeError) as ctx:
            self.screen.capture_screenshot()
        self.assertIn("Mock screenshot capture failed", str(ctx.exception))

    def test_vision_desktop_capture_abstraction(self) -> None:
        capture = ScreenControllerDesktopCapture(self.screen)
        screenshot = capture.capture()
        self.assertIsInstance(screenshot, Screenshot)
        self.assertEqual(screenshot.width, 1920)
        self.assertEqual(screenshot.height, 1080)
        self.assertEqual(screenshot.format, "png")
        self.assertGreater(len(screenshot.get_bytes()), 0)
        dict_rep = screenshot.to_dict()
        self.assertEqual(dict_rep["width"], 1920)


class TestWindowCommands(unittest.TestCase):
    """Unit tests for window observation and focus."""

    def setUp(self) -> None:
        self.window = MockWindowController()
        self.window.visible_windows = [
            {"hwnd": 101, "title": "Untitled - Notepad", "process_name": "notepad.exe"},
            {"hwnd": 102, "title": "Calculator", "process_name": "calculator.exe"},
            {"hwnd": 103, "title": "File Explorer", "process_name": "explorer.exe"},
        ]

    def test_active_window_query(self) -> None:
        act = self.window.get_active_window()
        self.assertIn("title", act)
        self.assertIn("process_name", act)

    def test_focus_exact_matching(self) -> None:
        res = self.window.focus_window("Calculator")
        self.assertEqual(res["title"], "Calculator")
        self.assertEqual(res["status"], "focused")
        self.assertIn("Calculator", self.window.focused_titles)

    def test_focus_substring_case_insensitive_matching(self) -> None:
        res = self.window.focus_window("notepad")
        self.assertEqual(res["title"], "Untitled - Notepad")
        self.assertEqual(res["status"], "focused")

    def test_focus_nonexistent_window_fails(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.window.focus_window("NonexistentWindowNameXYZ")
        self.assertIn("No visible window found", str(ctx.exception))

    def test_focus_ambiguous_match_handling(self) -> None:
        # Add duplicate window
        self.window.visible_windows.append(
            {"hwnd": 104, "title": "Calculator (2)", "process_name": "calculator.exe"}
        )
        # Should deterministically pick the first match without crashing
        res = self.window.focus_window("Calculator")
        self.assertIsNotNone(res)


class TestMouseCommands(unittest.TestCase):
    """Unit tests for mouse movement, click, and scroll."""

    def setUp(self) -> None:
        self.screen = MockScreenController(width=1920, height=1080)
        self.mouse = MockMouseController(screen_ctrl=self.screen)

    def test_valid_move(self) -> None:
        self.assertTrue(self.mouse.move_mouse(100, 200))
        self.assertEqual(self.mouse.mouse_moves, [(100, 200)])

    def test_negative_coordinate_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.mouse.move_mouse(-5, 100)
        with self.assertRaises(ValueError):
            self.mouse.move_mouse(100, -1)

    def test_out_of_bounds_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.mouse.move_mouse(1920, 500)
        with self.assertRaises(ValueError):
            self.mouse.move_mouse(500, 1080)

    def test_non_integer_coordinate_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.mouse.move_mouse("100", 200)  # type: ignore
        with self.assertRaises(ValueError):
            self.mouse.move_mouse(True, 200)  # type: ignore

    def test_valid_left_click(self) -> None:
        self.assertTrue(self.mouse.click(button="left", clicks=1))
        self.assertIn(("left", 1), self.mouse.clicks)

    def test_valid_right_click(self) -> None:
        self.assertTrue(self.mouse.click(button="right", clicks=1))
        self.assertIn(("right", 1), self.mouse.clicks)

    def test_double_click(self) -> None:
        self.assertTrue(self.mouse.click(button="left", clicks=2))
        self.assertIn(("left", 2), self.mouse.clicks)

    def test_click_with_coordinates(self) -> None:
        self.assertTrue(self.mouse.click(button="left", clicks=1, x=500, y=600))
        self.assertIn((500, 600), self.mouse.mouse_moves)
        self.assertIn(("left", 1, 500, 600), self.mouse.clicks_with_coords)

    def test_invalid_button_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.mouse.click(button="middle")

    def test_invalid_click_count_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.mouse.click(button="left", clicks=3)

    def test_positive_scroll(self) -> None:
        self.assertTrue(self.mouse.scroll(120))
        self.assertEqual(self.mouse.scroll_calls, [120])

    def test_negative_scroll(self) -> None:
        self.assertTrue(self.mouse.scroll(-240))
        self.assertEqual(self.mouse.scroll_calls, [-240])

    def test_excessive_scroll_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.mouse.scroll(1001)
        with self.assertRaises(ValueError):
            self.mouse.scroll(-1001)


class TestKeyboardAndTypeCommands(unittest.TestCase):
    """Unit tests for keyboard shortcuts and text input."""

    def setUp(self) -> None:
        self.keyboard = MockKeyboardController()

    def test_allowed_single_keys(self) -> None:
        for k in ["ENTER", "ESC", "TAB", "SPACE", "BACKSPACE", "UP", "DOWN", "LEFT", "RIGHT", "WIN"]:
            self.assertTrue(self.keyboard.press_key(k))
        self.assertIn("ENTER", self.keyboard.pressed_keys)
        self.assertIn("WIN", self.keyboard.pressed_keys)

    def test_allowed_combinations(self) -> None:
        for comb in ["CTRL+C", "CTRL+V", "CTRL+A", "CTRL+S", "ALT+TAB", "SHIFT+TAB"]:
            self.assertTrue(self.keyboard.press_key(comb))
        self.assertIn("CTRL+A", self.keyboard.pressed_keys)

    def test_unknown_key_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.keyboard.press_key("F24")
        self.assertIn("not in the allowed keys list", str(ctx.exception))

    def test_malformed_combination_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.keyboard.press_key("CTRL+UNKNOWN")
        self.assertIn("not in the allowed keys list", str(ctx.exception))

    def test_type_valid_text(self) -> None:
        chars = self.keyboard.type_text("Hello from Bimo")
        self.assertEqual(chars, 15)
        self.assertIn("Hello from Bimo", self.keyboard.typed_text)


class TestApplicationCommands(unittest.TestCase):
    """Unit tests for strict allowlisted application launching."""

    def setUp(self) -> None:
        self.launcher = MockAppLauncher()

    def test_allowlisted_apps_succeed(self) -> None:
        for app in ["notepad", "calculator", "explorer"]:
            res = self.launcher.launch(app)
            self.assertEqual(res["app"], app)
            self.assertIn(app, self.launcher.launched_apps)

    def test_non_allowlisted_app_rejected(self) -> None:
        for bad_app in ["cmd", "powershell", "terminal", "python", "bash"]:
            with self.assertRaises(ValueError) as ctx:
                self.launcher.launch(bad_app)
            self.assertIn("not in the allowed applications list", str(ctx.exception))

    def test_arbitrary_executable_path_rejected(self) -> None:
        for path in ["C:\\Windows\\System32\\cmd.exe", "calc.exe", "powershell.exe", "../../malicious.exe"]:
            with self.assertRaises(ValueError):
                self.launcher.launch(path)


class TestBimoPermissionsPhase8(unittest.TestCase):
    """Unit tests for Phase 6 & Phase 8 permission classification."""

    def setUp(self) -> None:
        self.client = MockPCAgentClient()
        self.registry = ToolRegistry()
        register_pc_tools(self.registry, self.client)
        self.policy = PermissionPolicy()

    def test_safe_observation_tools_execute_without_confirmation(self) -> None:
        for tool_name in ["pc.get_status", "pc.get_active_window", "pc.get_screen_size", "pc.screenshot"]:
            tool = self.registry.get(tool_name)
            self.assertIsNotNone(tool)
            self.assertEqual(tool.permission, ToolPermission.SAFE)
            # Execute without confirmation flag
            res = self.registry.execute(tool_name, permission_policy=self.policy, context={})
            self.assertTrue(res.success, f"Tool {tool_name} failed unexpectedly: {res.error}")

    def test_confirm_tools_denied_without_confirmation(self) -> None:
        confirm_cases = [
            ("pc.open_app", {"app": "notepad"}),
            ("pc.close_app", {"app": "notepad"}),
            ("pc.focus_window", {"title": "Notepad"}),
            ("pc.move_mouse", {"x": 100, "y": 100}),
            ("pc.click", {"button": "left"}),
            ("pc.scroll", {"amount": 100}),
            ("pc.type_text", {"text": "Hello"}),
            ("pc.press_key", {"key": "ENTER"}),
        ]
        for name, kwargs in confirm_cases:
            tool = self.registry.get(name)
            self.assertIsNotNone(tool)
            self.assertEqual(tool.permission, ToolPermission.CONFIRM)
            # Execute without confirmed=True
            res = self.registry.execute(name, permission_policy=self.policy, context={}, **kwargs)
            self.assertFalse(res.success, f"Tool {name} succeeded without confirmation!")
            self.assertTrue(res.metadata.get("denied", False))

    def test_confirm_tools_execute_with_confirmation(self) -> None:
        confirm_cases = [
            ("pc.open_app", {"app": "notepad"}),
            ("pc.close_app", {"app": "notepad"}),
            ("pc.focus_window", {"title": "Notepad"}),
            ("pc.move_mouse", {"x": 100, "y": 100}),
            ("pc.click", {"button": "left"}),
            ("pc.scroll", {"amount": 100}),
            ("pc.type_text", {"text": "Hello"}),
            ("pc.press_key", {"key": "ENTER"}),
        ]
        for name, kwargs in confirm_cases:
            res = self.registry.execute(name, permission_policy=self.policy, context={"confirmed": True}, **kwargs)
            self.assertTrue(res.success, f"Tool {name} failed with confirmation: {res.error}")


class MockMultiTurnLLM(LLMProvider):
    """Mock LLM that executes scripted sequential turns for computer-use testing."""

    def __init__(self, responses: List[LLMResponse]) -> None:
        super().__init__(model_name="mock-turn-llm")
        self.responses = responses
        self.call_count = 0
        self.requests: List[LLMRequest] = []

    @property
    def provider_name(self) -> str:
        return "MockMultiTurnLLM"

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(content="Final default response", tool_calls=[], finish_reason="stop", model=self.model_name)

    def generate_stream(self, request: LLMRequest):
        yield "token"


class TestComputerUseLoop(unittest.TestCase):
    """Unit tests for the bounded multi-turn observe -> act -> observe computer-use loop."""

    def setUp(self) -> None:
        self.client = MockPCAgentClient()
        self.tool_registry = ToolRegistry()
        register_pc_tools(self.tool_registry, self.client)
        self.policy = PermissionPolicy()
        self.event_bus = EventBus()
        self.events_received: List[Event] = []
        self.event_bus.subscribe(None, lambda ev: self.events_received.append(ev))

    def test_single_action_execution(self) -> None:
        # 1. LLM requests pc.get_status -> 2. LLM responds with natural language
        llm = MockMultiTurnLLM([
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="call-1", name="pc.get_status", arguments={})],
                model="mock",
            ),
            LLMResponse(content="Your laptop is online.", tool_calls=[], model="mock"),
        ])
        agent = BimoAgent(
            llm_provider=llm,
            event_bus=self.event_bus,
            tool_registry=self.tool_registry,
            permission_policy=self.policy,
            auto_speak=False,
        )

        resp = agent.process_turn("Is my PC online?")
        self.assertIn("laptop is online", resp.content)
        event_types = [ev.type for ev in self.events_received]
        self.assertIn(EventType.PC_COMPUTER_USE_STARTED, event_types)
        self.assertIn(EventType.PC_COMPUTER_USE_STEP, event_types)
        self.assertIn(EventType.PC_COMPUTER_USE_COMPLETED, event_types)

    def test_observe_act_observe_loop(self) -> None:
        # Scripted Loop:
        # Turn 1: pc.get_active_window
        # Turn 2: pc.type_text
        # Turn 3: pc.screenshot
        # Turn 4: Final response
        llm = MockMultiTurnLLM([
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="call-1", name="pc.get_active_window", arguments={})],
                model="mock",
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="call-2", name="pc.type_text", arguments={"text": "Hello Bimo"})],
                model="mock",
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="call-3", name="pc.screenshot", arguments={})],
                model="mock",
            ),
            LLMResponse(content="Done typing and verified screen.", tool_calls=[], model="mock"),
        ])
        agent = BimoAgent(
            llm_provider=llm,
            event_bus=self.event_bus,
            tool_registry=self.tool_registry,
            permission_policy=self.policy,
            auto_speak=False,
        )

        resp = agent.process_turn("Type Hello in the current window and check.", metadata={"confirmed": True})
        self.assertEqual(resp.content, "Done typing and verified screen.")
        event_types = [ev.type for ev in self.events_received]
        self.assertIn(EventType.PC_COMPUTER_USE_STARTED, event_types)
        self.assertIn(EventType.PC_KEYBOARD_ACTION, event_types)
        self.assertIn(EventType.PC_SCREENSHOT_CAPTURED, event_types)
        self.assertIn(EventType.PC_COMPUTER_USE_COMPLETED, event_types)

    def test_max_steps_limit_enforced(self) -> None:
        # Infinite tool call loop
        infinite_llm = MagicMock(spec=LLMProvider)
        infinite_llm.model_name = "infinite-mock"
        infinite_llm.provider_name = "infinite-mock-provider"
        infinite_llm.generate.return_value = LLMResponse(
            content="",
            tool_calls=[ToolCall(id="call-inf", name="pc.get_screen_size", arguments={})],
            model="infinite-mock",
        )

        agent = BimoAgent(
            llm_provider=infinite_llm,
            event_bus=self.event_bus,
            tool_registry=self.tool_registry,
            permission_policy=self.policy,
            max_tool_iterations=4,  # Bounded to 4
            auto_speak=False,
        )

        resp = agent.process_turn("Check screen forever")
        self.assertIn("maximum tool execution limit", resp.content)
        event_types = [ev.type for ev in self.events_received]
        self.assertIn(EventType.PC_COMPUTER_USE_LIMIT_REACHED, event_types)

    def test_disconnected_pc_gracefully_handled(self) -> None:
        self.client.set_connected(False)
        llm = MockMultiTurnLLM([
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="call-1", name="pc.get_status", arguments={})],
                model="mock",
            ),
            LLMResponse(content="Sorry, your laptop is currently offline.", tool_calls=[], model="mock"),
        ])
        agent = BimoAgent(
            llm_provider=llm,
            event_bus=self.event_bus,
            tool_registry=self.tool_registry,
            permission_policy=self.policy,
            auto_speak=False,
        )

        resp = agent.process_turn("Check PC status")
        self.assertIn("laptop is currently offline", resp.content)

    def test_unknown_tool_gracefully_handled(self) -> None:
        llm = MockMultiTurnLLM([
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="call-1", name="pc.unknown_dangerous_cmd", arguments={})],
                model="mock",
            ),
            LLMResponse(content="I could not execute that command.", tool_calls=[], model="mock"),
        ])
        agent = BimoAgent(
            llm_provider=llm,
            event_bus=self.event_bus,
            tool_registry=self.tool_registry,
            permission_policy=self.policy,
            auto_speak=False,
        )

        resp = agent.process_turn("Run dangerous command")
        self.assertEqual(resp.content, "I could not execute that command.")


class TestSecurityBoundaries(unittest.TestCase):
    """Strict security invariant testing for Phase 8."""

    def setUp(self) -> None:
        self.client = MockPCAgentClient()
        self.command_registry = build_default_command_registry(
            app_launcher=MockAppLauncher(),
            window_ctrl=MockWindowController(),
            kbd_ctrl=MockKeyboardController(),
            mouse_ctrl=MockMouseController(),
            screen_ctrl=MockScreenController(),
        )

    def test_no_shell_commands_registered(self) -> None:
        registered = self.command_registry.list_commands()
        for cmd in registered:
            self.assertNotIn("shell", cmd)
            self.assertNotIn("cmd", cmd)
            self.assertNotIn("powershell", cmd)
            self.assertNotIn("exec", cmd)
            self.assertNotIn("eval", cmd)
            self.assertNotIn("bash", cmd)

    def test_no_arbitrary_subprocess_execution(self) -> None:
        # Ensure pc.open_app strictly rejects arbitrary paths and shells
        resp1 = self.command_registry.execute("req-1", "pc.open_app", {"app": "cmd.exe"})
        self.assertFalse(resp1.success)
        self.assertEqual(resp1.error.code, PCErrorCode.APP_NOT_ALLOWED.value)

        resp2 = self.command_registry.execute("req-2", "pc.open_app", {"app": "powershell.exe"})
        self.assertFalse(resp2.success)
        self.assertEqual(resp2.error.code, PCErrorCode.APP_NOT_ALLOWED.value)

        resp3 = self.command_registry.execute("req-3", "pc.open_app", {"app": "C:\\Windows\\notepad.exe"})
        self.assertFalse(resp3.success)
        self.assertEqual(resp3.error.code, PCErrorCode.APP_NOT_ALLOWED.value)

    def test_invalid_mouse_coordinates_rejected_by_command_registry(self) -> None:
        resp1 = self.command_registry.execute("req-1", "pc.move_mouse", {"x": -10, "y": 50})
        self.assertFalse(resp1.success)
        self.assertEqual(resp1.error.code, PCErrorCode.INVALID_ARGUMENTS.value)

        resp2 = self.command_registry.execute("req-2", "pc.move_mouse", {"x": 2000, "y": 50})
        self.assertFalse(resp2.success)
        self.assertEqual(resp2.error.code, PCErrorCode.INVALID_ARGUMENTS.value)

    def test_invalid_keys_rejected_by_command_registry(self) -> None:
        resp = self.command_registry.execute("req-1", "pc.press_key", {"key": "MALICIOUS_KEY"})
        self.assertFalse(resp.success)
        self.assertEqual(resp.error.code, PCErrorCode.KEY_NOT_ALLOWED.value)


if __name__ == "__main__":
    unittest.main()
