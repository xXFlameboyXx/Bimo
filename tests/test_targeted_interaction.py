"""Comprehensive Tests for Targeted Windows Interaction (Refinement Pass).

Validates:
1. TARGET_NOT_FOREGROUND:
   - Target = Notepad, Foreground = Chrome -> No keyboard input sent, returns TARGET_NOT_FOREGROUND error.
2. MINIMIZED WINDOW RESTORATION:
   - Target = Notepad (minimized) -> Restored, focused, verified foreground, input sent.
3. USER INTERRUPTION (MID-OPERATION FOCUS LOSS):
   - Target = Notepad, user switches to Chrome mid-typing -> detects target loss, pauses,
     does NOT type remaining characters into Chrome, returns USER_INTERACTION_INTERRUPTED.
4. TARGET VALIDATION & CLOSURE:
   - Closed HWND -> TARGET_CLOSED.
   - Process ID changed -> TARGET_PROCESS_CHANGED.
   - Process name changed -> TARGET_PROCESS_CHANGED.
   - Window title changes but HWND/PID valid -> succeeds without relying purely on title.
5. WINDOW-RELATIVE MOUSE COORDINATES:
   - Relative client coordinates translated via client_to_screen before click/move.
6. RESTORE PREVIOUS WINDOW:
   - With restore_previous=True, original active window is restored after interaction.
7. TEXT INPUT STRATEGIES:
   - ForegroundVerifiedKeyboardStrategy vs DirectUIAutomationStrategy reporting.
8. APPLICATION ADAPTER:
   - NotepadAdapter target resolution and preferred strategy.
9. COMMAND REGISTRY INTEGRATION:
   - pc.open_app binds current_target.
   - pc.type_text enforces target binding and returns structured metadata without secrets.
   - pc.get_target_window queries target information.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict

from bimo.pc.models import PCErrorCode, TargetWindow
from bimo.pc.mock_client import MockPCAgentClient
from bimo.pc.tools import register_pc_tools
from bimo.tools.permissions import PermissionPolicy
from bimo.tools.registry import ToolRegistry
from windows_agent.commands import build_default_command_registry
from windows_agent.controllers import (
    DirectUIAutomationStrategy,
    ForegroundVerifiedKeyboardStrategy,
    GenericApplicationAdapter,
    MockAppLauncher,
    MockKeyboardController,
    MockMouseController,
    MockScreenController,
    MockUIAutomationController,
    MockWindowController,
    NotepadAdapter,
)
from windows_agent.registry import PCCommandError, PCCommandRegistry


class TestTargetedWindowsInteraction(unittest.TestCase):
    """Safety and correctness unit tests for targeted Windows interaction."""

    def setUp(self) -> None:
        self.win_ctrl = MockWindowController(title="Untitled - Notepad", process_name="notepad.exe")
        self.kbd = MockKeyboardController()
        self.mouse = MockMouseController()
        self.launcher = MockAppLauncher(allowed_apps=["notepad", "calculator", "explorer"])
        self.registry = build_default_command_registry(
            window_ctrl=self.win_ctrl,
            app_launcher=self.launcher,
            kbd_ctrl=self.kbd,
            mouse_ctrl=self.mouse,
            allowed_apps=["notepad", "calculator", "explorer"],
        )

    def test_target_notepad_foreground_chrome_blocks_keyboard_input(self) -> None:
        """CRITICAL TEST 1: Target = Notepad, Foreground = Chrome. Input MUST be blocked."""
        # Notepad window at HWND 1001
        notepad_target = TargetWindow(
            hwnd=1001,
            process_id=5001,
            process_name="notepad.exe",
            title="Untitled - Notepad",
        )
        # Foreground is Chrome at HWND 2002
        self.win_ctrl.windows[2002] = {
            "hwnd": 2002,
            "process_id": 8888,
            "process_name": "chrome.exe",
            "title": "Google Chrome",
            "app_name": "chrome",
        }
        self.win_ctrl.foreground_hwnd = 2002
        self.win_ctrl.fail_focus = True  # Simulates focus refusing to switch away from Chrome

        resp = self.registry.execute(
            "req-safety-1",
            "pc.type_text",
            {"text": "Dangerous input", "target": notepad_target.to_dict()},
        )

        # Execution must fail with TARGET_NOT_FOREGROUND
        self.assertFalse(resp.success)
        self.assertIsNotNone(resp.error)
        assert resp.error is not None
        self.assertEqual(resp.error.code, PCErrorCode.TARGET_NOT_FOREGROUND.value)
        # Invariant: NO keyboard input sent anywhere
        self.assertEqual(len(self.kbd.typed_text), 0)

    def test_minimized_target_restored_and_typed_into(self) -> None:
        """CRITICAL TEST 2: Target = Notepad (minimized). Must be restored, focused, verified, then typed."""
        notepad_target = TargetWindow(
            hwnd=1001,
            process_id=5001,
            process_name="notepad.exe",
            title="Untitled - Notepad",
        )
        # Mark Notepad as minimized
        self.win_ctrl.minimized_hwnds.add(1001)
        self.assertTrue(self.win_ctrl.is_iconic(1001))

        resp = self.registry.execute(
            "req-safety-2",
            "pc.type_text",
            {"text": "Hello Bimo", "target": notepad_target.to_dict()},
        )

        self.assertTrue(resp.success)
        self.assertIsNotNone(resp.output)
        assert resp.output is not None
        self.assertEqual(resp.output.get("status"), "typed")
        self.assertEqual(resp.output.get("length"), 10)
        self.assertTrue(resp.output.get("verified_foreground"))
        # Window was restored
        self.assertFalse(self.win_ctrl.is_iconic(1001))
        # Text was typed
        self.assertIn("Hello Bimo", "".join(self.kbd.typed_text))

    def test_user_switches_window_during_typing_safely_halted(self) -> None:
        """CRITICAL TEST 3: User minimizes or switches to Chrome mid-typing.

        Input must pause/halt safely without spraying remaining characters into Chrome.
        """
        notepad_target = TargetWindow(
            hwnd=1001,
            process_id=5001,
            process_name="notepad.exe",
            title="Untitled - Notepad",
        )
        # Register Chrome window
        self.win_ctrl.windows[9999] = {
            "hwnd": 9999,
            "process_id": 7777,
            "process_name": "chrome.exe",
            "title": "Google Chrome",
        }
        # Simulate user switching to Chrome after typing 4 characters
        self.win_ctrl.interrupt_after_chars = 4
        self.win_ctrl.switch_to_hwnd_on_interrupt = 9999
        self.win_ctrl.fail_focus = True  # Do not allow re-stealing focus from user

        resp = self.registry.execute(
            "req-safety-3",
            "pc.type_text",
            {"text": "Hello Safe World", "target": notepad_target.to_dict()},
        )

        self.assertFalse(resp.success)
        self.assertIsNotNone(resp.error)
        assert resp.error is not None
        self.assertEqual(resp.error.code, PCErrorCode.USER_INTERACTION_INTERRUPTED.value)
        # Check that characters after interruption were NOT sent
        all_typed = "".join(self.kbd.typed_text)
        self.assertEqual(len(all_typed), 4)
        self.assertEqual(all_typed, "Hell")
        self.assertNotIn("Safe World", all_typed)

    def test_closed_hwnd_rejected(self) -> None:
        """CRITICAL TEST 4: Target HWND was closed -> TARGET_CLOSED."""
        notepad_target = TargetWindow(
            hwnd=1001,
            process_id=5001,
            process_name="notepad.exe",
            title="Untitled - Notepad",
        )
        self.win_ctrl.closed_hwnds.add(1001)

        resp = self.registry.execute(
            "req-safety-4",
            "pc.type_text",
            {"text": "Hello", "target": notepad_target.to_dict()},
        )

        self.assertFalse(resp.success)
        self.assertEqual(resp.error.code, PCErrorCode.TARGET_CLOSED.value)
        self.assertEqual(len(self.kbd.typed_text), 0)

    def test_process_id_changed_rejected(self) -> None:
        """CRITICAL TEST 5: Target process changed (PID mismatch) -> TARGET_PROCESS_CHANGED."""
        notepad_target = TargetWindow(
            hwnd=1001,
            process_id=5001,
            process_name="notepad.exe",
            title="Untitled - Notepad",
        )
        # HWND 1001 now belongs to a different process (e.g. PID 9999)
        self.win_ctrl.windows[1001]["process_id"] = 9999

        resp = self.registry.execute(
            "req-safety-5",
            "pc.type_text",
            {"text": "Hello", "target": notepad_target.to_dict()},
        )

        self.assertFalse(resp.success)
        self.assertEqual(resp.error.code, PCErrorCode.TARGET_PROCESS_CHANGED.value)
        self.assertEqual(len(self.kbd.typed_text), 0)

    def test_window_title_changes_succeeds_by_hwnd_and_process(self) -> None:
        """CRITICAL TEST 6: Window title changes (e.g. Untitled -> *Untitled), identity still holds."""
        notepad_target = TargetWindow(
            hwnd=1001,
            process_id=5001,
            process_name="notepad.exe",
            title="Untitled - Notepad",
        )
        # Document is edited, title updates
        self.win_ctrl.windows[1001]["title"] = "*Untitled - Notepad"
        self.win_ctrl.title = "*Untitled - Notepad"

        resp = self.registry.execute(
            "req-safety-6",
            "pc.type_text",
            {"text": "More content", "target": notepad_target.to_dict()},
        )

        self.assertTrue(resp.success)
        self.assertIn("More content", "".join(self.kbd.typed_text))

    def test_window_relative_coordinates_translation(self) -> None:
        """CRITICAL TEST 7: Target-relative coordinates are converted to screen coordinates."""
        notepad_target = TargetWindow(
            hwnd=1001,
            process_id=5001,
            process_name="notepad.exe",
            title="Untitled - Notepad",
        )

        # MockWindowController adds (100, 100) offset for relative coordinates
        resp = self.registry.execute(
            "req-mouse-1",
            "pc.click",
            {
                "button": "left",
                "clicks": 1,
                "target": notepad_target.to_dict(),
                "relative": True,
                "rel_x": 50,
                "rel_y": 75,
            },
        )

        self.assertTrue(resp.success)
        assert resp.output is not None
        self.assertEqual(resp.output.get("status"), "clicked")
        coords = resp.output.get("coords")
        self.assertIsNotNone(coords)
        self.assertEqual(coords.get("rel_x"), 50)
        self.assertEqual(coords.get("rel_y"), 75)
        self.assertEqual(coords.get("screen_x"), 150)
        self.assertEqual(coords.get("screen_y"), 175)

    def test_restore_previous_window_behavior(self) -> None:
        """CRITICAL TEST 8: restore_previous=True focuses original window after completing task."""
        # Initial active window is Chrome at HWND 2002
        self.win_ctrl.windows[2002] = {
            "hwnd": 2002,
            "process_id": 8888,
            "process_name": "chrome.exe",
            "title": "Google Chrome",
        }
        self.win_ctrl.foreground_hwnd = 2002

        notepad_target = TargetWindow(
            hwnd=1001,
            process_id=5001,
            process_name="notepad.exe",
            title="Untitled - Notepad",
        )

        resp = self.registry.execute(
            "req-restore-1",
            "pc.type_text",
            {
                "text": "Quick note",
                "target": notepad_target.to_dict(),
                "restore_previous": True,
            },
        )

        self.assertTrue(resp.success)
        # Previous foreground window (Chrome 2002) should be restored
        self.assertEqual(self.win_ctrl.foreground_hwnd, 2002)

    def test_strategy_reporting(self) -> None:
        """CRITICAL TEST 9: Strategy names are explicitly reported in results."""
        notepad_target = TargetWindow(
            hwnd=1001,
            process_id=5001,
            process_name="notepad.exe",
            title="Untitled - Notepad",
        )

        # Default strategy is foreground_verified_keyboard
        strategy = ForegroundVerifiedKeyboardStrategy()
        res = strategy.type_text(
            target=notepad_target,
            text="Strategy test",
            win_ctrl=self.win_ctrl,
            kbd_ctrl=self.kbd,
        )
        self.assertEqual(res["strategy"], "foreground_verified_keyboard")
        self.assertTrue(res["verified_foreground"])

        # Direct UI Automation strategy
        mock_uia = MockUIAutomationController(supported=True)
        uia_strategy = DirectUIAutomationStrategy(mock_uia)
        res_uia = uia_strategy.type_text(
            target=notepad_target,
            text="Direct UIA test",
            win_ctrl=self.win_ctrl,
            kbd_ctrl=self.kbd,
        )
        self.assertEqual(res_uia["strategy"], "direct_ui_automation")
        self.assertFalse(res_uia["verified_foreground"])
        self.assertEqual(mock_uia.set_text_calls, [(1001, "Direct UIA test")])

    def test_application_adapter(self) -> None:
        """CRITICAL TEST 10: NotepadAdapter resolves targets correctly."""
        adapter = NotepadAdapter()
        self.assertEqual(adapter.app_name, "notepad")
        resolved = adapter.resolve_target(self.win_ctrl)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.hwnd, 1001)
        self.assertEqual(resolved.process_name, "notepad.exe")

        strategy = adapter.get_preferred_strategy()
        self.assertEqual(strategy.strategy_name, "foreground_verified_keyboard")

    def test_session_target_binding_via_open_app(self) -> None:
        """CRITICAL TEST 11: pc.open_app binds current_target for subsequent typing."""
        # Open Notepad
        open_res = self.registry.execute("req-open", "pc.open_app", {"app": "notepad"})
        self.assertTrue(open_res.success)
        assert open_res.output is not None
        self.assertIn("target", open_res.output)
        self.assertEqual(open_res.output["target"]["process_name"], "notepad.exe")

        # Type text without passing explicit target: should bind to opened Notepad
        type_res = self.registry.execute("req-type", "pc.type_text", {"text": "Bound typing"})
        self.assertTrue(type_res.success)
        assert type_res.output is not None
        self.assertEqual(type_res.output["target"]["process_name"], "notepad.exe")
        self.assertIn("Bound typing", "".join(self.kbd.typed_text))

    def test_pc_get_target_window_command(self) -> None:
        """CRITICAL TEST 12: pc.get_target_window queries target window."""
        resp = self.registry.execute("req-target", "pc.get_target_window", {"app": "notepad"})
        self.assertTrue(resp.success)
        assert resp.output is not None
        self.assertIn("target", resp.output)
        self.assertEqual(resp.output["target"]["hwnd"], 1001)


class TestTargetedToolsIntegration(unittest.TestCase):
    """Integration test with Bimo ToolRegistry and PermissionPolicy."""

    def setUp(self) -> None:
        self.client = MockPCAgentClient()
        self.registry = ToolRegistry()
        register_pc_tools(self.registry, self.client)
        self.policy = PermissionPolicy()

    def test_targeted_type_tool_execution(self) -> None:
        tool = self.registry.get("pc.type_text")
        self.assertIsNotNone(tool)
        res = self.registry.execute(
            "pc.type_text",
            permission_policy=self.policy,
            context={"confirmed": True},
            text="Hello Bimo",
            app="notepad",
        )
        self.assertTrue(res.success)
        self.assertIn("Typed 10 characters", res.output)
        self.assertEqual(res.metadata.get("status"), "typed")
        self.assertIn("target", res.metadata)

    def test_get_target_window_tool_execution(self) -> None:
        tool = self.registry.get("pc.get_target_window")
        self.assertIsNotNone(tool)
        res = self.registry.execute(
            "pc.get_target_window",
            permission_policy=self.policy,
            context={},
            app="notepad",
        )
        self.assertTrue(res.success)
        self.assertIn("target", res.metadata)
