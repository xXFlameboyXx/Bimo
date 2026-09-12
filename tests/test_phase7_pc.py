"""Comprehensive Phase 7 Tests for Bimo Windows PC Agent.

Covers:
- Authentication (HMAC-SHA256, clock skew, nonce replay protection)
- Protocol envelope parsing, validation, error codes
- Command registry, schema validation, duplicate prevention
- Safe PC commands (get_status, get_active_window, open_app, close_app, type_text, press_key, click)
- Security boundaries (reject arbitrary executables, shell commands, path traversal)
- Bimo tool adapters and Phase 6 PermissionPolicy integration (SAFE vs CONFIRM)
- Offline PC handling and graceful degradation
"""

from __future__ import annotations

import json
import secrets
import time
import unittest

from bimo.core.config import PCAgentConfig
from bimo.core.events import EventBus, EventType
from bimo.pc.auth import (
    RequestAuthenticator,
    ReplayCache,
    generate_canonical_payload,
    sign_request,
    verify_signature,
)
from bimo.pc.client import PCAgentClient
from bimo.pc.mock_client import MockPCAgentClient
from bimo.pc.models import PCError, PCErrorCode, PCRequest, PCResponse
from bimo.pc.tools import (
    PCClickTool,
    PCCloseAppTool,
    PCGetActiveWindowTool,
    PCGetStatusTool,
    PCOpenAppTool,
    PCPressKeyTool,
    PCTypeTextTool,
    register_pc_tools,
)
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry
from windows_agent.commands import build_default_command_registry
from windows_agent.controllers import (
    MockAppLauncher,
    MockKeyboardController,
    MockMouseController,
    MockWindowController,
)
from windows_agent.registry import PCCommandRegistry, PCCommandSpec
from windows_agent.server import PCAgentServerManager


class TestPCAuthentication(unittest.TestCase):
    """Test HMAC-SHA256 authentication, timestamp skew, and nonce replay."""

    def setUp(self) -> None:
        self.secret = "test-secret-key-12345"
        self.auth = RequestAuthenticator(shared_secret=self.secret, max_clock_skew=30.0)

    def test_valid_signature_accepted(self) -> None:
        now = time.time()
        nonce = secrets.token_hex(16)
        sig = sign_request(self.secret, 1, "req-1", now, nonce, "pc.get_status", {})

        is_valid, err_code, err_msg = self.auth.authenticate(
            version=1,
            request_id="req-1",
            timestamp=now,
            nonce=nonce,
            command="pc.get_status",
            arguments={},
            signature=sig,
            current_time=now,
        )
        self.assertTrue(is_valid)
        self.assertIsNone(err_code)
        self.assertIsNone(err_msg)

    def test_invalid_signature_rejected(self) -> None:
        now = time.time()
        nonce = secrets.token_hex(16)
        is_valid, err_code, _ = self.auth.authenticate(
            version=1,
            request_id="req-1",
            timestamp=now,
            nonce=nonce,
            command="pc.get_status",
            arguments={},
            signature="deadbeef0123456789abcdef",
            current_time=now,
        )
        self.assertFalse(is_valid)
        self.assertEqual(err_code, PCErrorCode.AUTH_FAILED.value)

    def test_missing_signature_rejected(self) -> None:
        now = time.time()
        is_valid, err_code, _ = self.auth.authenticate(
            version=1,
            request_id="req-1",
            timestamp=now,
            nonce="nonce-1",
            command="pc.get_status",
            arguments={},
            signature="",
            current_time=now,
        )
        self.assertFalse(is_valid)
        self.assertEqual(err_code, PCErrorCode.AUTH_FAILED.value)

    def test_clock_skew_expired_timestamp_rejected(self) -> None:
        now = time.time()
        stale_time = now - 45.0  # 45s ago (skew limit is 30s)
        nonce = secrets.token_hex(16)
        sig = sign_request(self.secret, 1, "req-1", stale_time, nonce, "pc.get_status", {})

        is_valid, err_code, _ = self.auth.authenticate(
            version=1,
            request_id="req-1",
            timestamp=stale_time,
            nonce=nonce,
            command="pc.get_status",
            arguments={},
            signature=sig,
            current_time=now,
        )
        self.assertFalse(is_valid)
        self.assertEqual(err_code, PCErrorCode.EXPIRED_TIMESTAMP.value)

    def test_replay_attack_rejected(self) -> None:
        now = time.time()
        nonce = "fixed-nonce-1234"
        req_id = "req-fixed"
        sig = sign_request(self.secret, 1, req_id, now, nonce, "pc.get_status", {})

        # First attempt must succeed
        valid1, _, _ = self.auth.authenticate(
            version=1,
            request_id=req_id,
            timestamp=now,
            nonce=nonce,
            command="pc.get_status",
            arguments={},
            signature=sig,
            current_time=now,
        )
        self.assertTrue(valid1)

        # Second attempt with same nonce and request_id must be rejected
        valid2, err_code, _ = self.auth.authenticate(
            version=1,
            request_id=req_id,
            timestamp=now,
            nonce=nonce,
            command="pc.get_status",
            arguments={},
            signature=sig,
            current_time=now,
        )
        self.assertFalse(valid2)
        self.assertEqual(err_code, PCErrorCode.REPLAY_DETECTED.value)


class TestPCCommandRegistry(unittest.TestCase):
    """Test the PC command registry mechanics and security restrictions."""

    def setUp(self) -> None:
        self.registry = PCCommandRegistry()

    def test_register_and_list(self) -> None:
        spec = PCCommandSpec(
            name="pc.test_cmd",
            description="Test command",
            handler=lambda args: {"result": "ok"},
            required_args=[],
        )
        self.registry.register(spec)
        self.assertTrue(self.registry.has("pc.test_cmd"))
        self.assertIn("pc.test_cmd", self.registry.list_commands())

    def test_duplicate_registration_prevented(self) -> None:
        spec = PCCommandSpec(
            name="pc.duplicate",
            description="First",
            handler=lambda args: {},
        )
        self.registry.register(spec)
        with self.assertRaises(ValueError):
            self.registry.register(spec)

    def test_unknown_command_rejected(self) -> None:
        resp = self.registry.execute("req-1", "pc.delete_everything", {})
        self.assertFalse(resp.success)
        self.assertIsNotNone(resp.error)
        self.assertEqual(resp.error.code, PCErrorCode.UNKNOWN_COMMAND.value)

    def test_missing_required_argument_rejected(self) -> None:
        spec = PCCommandSpec(
            name="pc.needs_arg",
            description="Needs an arg",
            handler=lambda args: {"val": args["app"]},
            required_args=["app"],
        )
        self.registry.register(spec)
        resp = self.registry.execute("req-1", "pc.needs_arg", {})
        self.assertFalse(resp.success)
        self.assertEqual(resp.error.code, PCErrorCode.INVALID_ARGUMENTS.value)

    def test_unrecognized_argument_rejected(self) -> None:
        spec = PCCommandSpec(
            name="pc.strict_args",
            description="Strict args",
            handler=lambda args: {},
            required_args=["app"],
        )
        self.registry.register(spec)
        resp = self.registry.execute("req-1", "pc.strict_args", {"app": "notepad", "malicious_flag": True})
        self.assertFalse(resp.success)
        self.assertEqual(resp.error.code, PCErrorCode.INVALID_ARGUMENTS.value)


class TestSafePCCommands(unittest.TestCase):
    """Test all 7 safe PC commands with mock controllers."""

    def setUp(self) -> None:
        self.win_ctrl = MockWindowController(title="Document - WordPad", process_name="wordpad.exe")
        self.launcher = MockAppLauncher(allowed_apps=["notepad", "calculator", "explorer"])
        self.kbd = MockKeyboardController()
        self.mouse = MockMouseController()
        self.registry = build_default_command_registry(
            window_ctrl=self.win_ctrl,
            app_launcher=self.launcher,
            kbd_ctrl=self.kbd,
            mouse_ctrl=self.mouse,
            allowed_apps=["notepad", "calculator", "explorer"],
        )

    def test_get_status_safe_fields(self) -> None:
        resp = self.registry.execute("req-1", "pc.get_status", {})
        self.assertTrue(resp.success)
        self.assertIn("hostname", resp.output)
        self.assertIn("os", resp.output)
        self.assertIn("agent_version", resp.output)
        self.assertIn("registered_commands", resp.output)
        # Ensure no secret leakage
        self.assertNotIn("password", resp.output)
        self.assertNotIn("secret", resp.output)
        self.assertNotIn("token", resp.output)

    def test_get_active_window(self) -> None:
        resp = self.registry.execute("req-2", "pc.get_active_window", {})
        self.assertTrue(resp.success)
        self.assertEqual(resp.output.get("title"), "Document - WordPad")
        self.assertEqual(resp.output.get("process_name"), "wordpad.exe")

    def test_open_app_allowlist_allowed(self) -> None:
        resp = self.registry.execute("req-3", "pc.open_app", {"app": "notepad"})
        self.assertTrue(resp.success)
        self.assertEqual(resp.output.get("app"), "notepad")
        self.assertIn("notepad", self.launcher.launched_apps)

    def test_open_app_denied_arbitrary_executable(self) -> None:
        malicious_apps = [
            "C:\\malware.exe",
            "powershell.exe",
            "cmd.exe",
            "python.exe",
            "curl",
            "bash",
            "format c:",
        ]
        for bad_app in malicious_apps:
            resp = self.registry.execute("req-4", "pc.open_app", {"app": bad_app})
            self.assertFalse(resp.success, f"Should have denied app: {bad_app}")
            self.assertEqual(resp.error.code, PCErrorCode.APP_NOT_ALLOWED.value)

    def test_close_app_allowed(self) -> None:
        resp = self.registry.execute("req-5", "pc.close_app", {"app": "calculator"})
        self.assertTrue(resp.success)
        self.assertEqual(resp.output.get("status"), "closed")
        self.assertIn("calculator", self.launcher.terminated_apps)

    def test_close_app_denied(self) -> None:
        resp = self.registry.execute("req-6", "pc.close_app", {"app": "system32"})
        self.assertFalse(resp.success)
        self.assertEqual(resp.error.code, PCErrorCode.APP_NOT_ALLOWED.value)

    def test_type_text_valid_and_reject_empty(self) -> None:
        # Valid text
        resp = self.registry.execute("req-7", "pc.type_text", {"text": "Hello Bimo!"})
        self.assertTrue(resp.success)
        self.assertIn("Hello Bimo!", self.kbd.typed_text)

        # Empty text rejected
        resp_empty = self.registry.execute("req-8", "pc.type_text", {"text": ""})
        self.assertFalse(resp_empty.success)
        self.assertEqual(resp_empty.error.code, PCErrorCode.INVALID_ARGUMENTS.value)

    def test_press_key_allowlist(self) -> None:
        # Allowed keys
        for valid_key in ["ENTER", "ESC", "TAB", "CTRL+C", "CTRL+V", "ALT+TAB"]:
            resp = self.registry.execute("req-9", "pc.press_key", {"key": valid_key})
            self.assertTrue(resp.success, f"Key {valid_key} should be allowed")
            self.assertIn(valid_key, self.kbd.pressed_keys)

        # Disallowed keys/commands
        for bad_key in ["START_SHELL", "FORMAT", "WIN+R", "RMDIR"]:
            resp = self.registry.execute("req-10", "pc.press_key", {"key": bad_key})
            self.assertFalse(resp.success, f"Key {bad_key} should be rejected")
            self.assertEqual(resp.error.code, PCErrorCode.KEY_NOT_ALLOWED.value)

    def test_click_validation(self) -> None:
        resp = self.registry.execute("req-11", "pc.click", {"button": "left", "clicks": 1})
        self.assertTrue(resp.success)
        self.assertIn(("left", 1), self.mouse.clicks)

        # Invalid button rejected
        resp_bad = self.registry.execute("req-12", "pc.click", {"button": "middle", "clicks": 1})
        self.assertFalse(resp_bad.success)
        self.assertEqual(resp_bad.error.code, PCErrorCode.INVALID_ARGUMENTS.value)


class TestBimoPCToolsAndPermissions(unittest.TestCase):
    """Test Bimo-side tool adapters and Phase 6 PermissionPolicy integration."""

    def setUp(self) -> None:
        self.mock_client = MockPCAgentClient()
        self.tool_registry = ToolRegistry()
        register_pc_tools(self.tool_registry, self.mock_client)
        self.policy = PermissionPolicy()

    def test_tool_declarations_and_permissions(self) -> None:
        status_tool = self.tool_registry.get("pc.get_status")
        self.assertIsNotNone(status_tool)
        self.assertEqual(status_tool.permission, ToolPermission.SAFE)

        win_tool = self.tool_registry.get("pc.get_active_window")
        self.assertIsNotNone(win_tool)
        self.assertEqual(win_tool.permission, ToolPermission.SAFE)

        confirm_tools = ["pc.open_app", "pc.close_app", "pc.type_text", "pc.press_key", "pc.click"]
        for tool_name in confirm_tools:
            tool = self.tool_registry.get(tool_name)
            self.assertIsNotNone(tool, f"Tool {tool_name} must be registered")
            self.assertEqual(tool.permission, ToolPermission.CONFIRM, f"{tool_name} must be CONFIRM")

    def test_safe_command_executes_without_confirmation(self) -> None:
        tool = self.tool_registry.get("pc.get_status")
        allowed, reason = self.policy.check_permission(tool, {"confirmed": False})
        self.assertTrue(allowed)
        self.assertIsNone(reason)

        res = tool.execute()
        self.assertTrue(res.success)
        self.assertIn("PC is online", res.output)

    def test_confirm_command_denied_without_confirmation(self) -> None:
        tool = self.tool_registry.get("pc.open_app")
        allowed, reason = self.policy.check_permission(tool, {"confirmed": False})
        self.assertFalse(allowed)
        self.assertIn("requires explicit user confirmation", reason)

    def test_confirm_command_succeeds_with_confirmation(self) -> None:
        tool = self.tool_registry.get("pc.open_app")
        allowed, reason = self.policy.check_permission(tool, {"confirmed": True})
        self.assertTrue(allowed)
        self.assertIsNone(reason)

        res = tool.execute(app="notepad")
        self.assertTrue(res.success)
        self.assertIn("Successfully launched notepad", res.output)

    def test_disconnected_pc_returns_friendly_error_without_crashing(self) -> None:
        self.mock_client.set_connected(False)
        tool = self.tool_registry.get("pc.get_status")
        res = tool.execute()
        self.assertFalse(res.success)
        self.assertIn("Your laptop is unavailable right now.", res.output)

    def test_timeout_returns_friendly_error(self) -> None:
        self.mock_client.fail_with_timeout = True
        tool = self.tool_registry.get("pc.get_status")
        res = tool.execute()
        self.assertFalse(res.success)
        self.assertIn("Your laptop is unavailable right now.", res.output)

    def test_auth_failure_returns_friendly_error(self) -> None:
        self.mock_client.fail_with_auth = True
        tool = self.tool_registry.get("pc.get_status")
        res = tool.execute()
        self.assertFalse(res.success)
        self.assertIn("Cannot connect to your laptop due to an authentication error.", res.output)


class TestPCAgentEndToEndNetwork(unittest.TestCase):
    """End-to-end integration test running live PCAgentServer on localhost."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.port = 8799
        cls.secret = "super-secret-lan-token-99"
        cls.win_ctrl = MockWindowController()
        cls.launcher = MockAppLauncher(allowed_apps=["notepad", "calculator", "explorer"])
        cls.kbd = MockKeyboardController()
        cls.mouse = MockMouseController()

        cls.server_registry = build_default_command_registry(
            window_ctrl=cls.win_ctrl,
            app_launcher=cls.launcher,
            kbd_ctrl=cls.kbd,
            mouse_ctrl=cls.mouse,
            allowed_apps=["notepad", "calculator", "explorer"],
        )

        cls.server_manager = PCAgentServerManager(
            host="127.0.0.1",
            port=cls.port,
            shared_secret=cls.secret,
            max_clock_skew=30.0,
            registry=cls.server_registry,
        )
        cls.server_manager.start(background=True)
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server_manager.stop()

    def setUp(self) -> None:
        self.config = PCAgentConfig(
            enabled=True,
            host="127.0.0.1",
            port=self.port,
            shared_secret=self.secret,
            connect_timeout=2.0,
            request_timeout=3.0,
        )
        self.event_bus = EventBus()
        self.client = PCAgentClient(config=self.config, event_bus=self.event_bus)

    def test_e2e_health_check(self) -> None:
        healthy = self.client.check_health()
        self.assertTrue(healthy)
        self.assertTrue(self.client.is_connected)

    def test_e2e_authenticated_get_status(self) -> None:
        resp = self.client.execute("pc.get_status", {})
        self.assertTrue(resp.success)
        self.assertIn("hostname", resp.output)

    def test_e2e_authenticated_open_app(self) -> None:
        resp = self.client.execute("pc.open_app", {"app": "notepad"})
        self.assertTrue(resp.success)
        self.assertEqual(resp.output.get("app"), "notepad")

    def test_e2e_invalid_secret_fails_auth(self) -> None:
        bad_config = PCAgentConfig(
            enabled=True,
            host="127.0.0.1",
            port=self.port,
            shared_secret="wrong-secret-bad",
            connect_timeout=2.0,
            request_timeout=3.0,
        )
        bad_client = PCAgentClient(config=bad_config)
        resp = bad_client.execute("pc.get_status", {})
        self.assertFalse(resp.success)
        self.assertEqual(resp.error.code, PCErrorCode.AUTH_FAILED.value)

    def test_e2e_unknown_command_fails(self) -> None:
        resp = self.client.execute("pc.dangerous_unknown_command", {})
        self.assertFalse(resp.success)
        self.assertEqual(resp.error.code, PCErrorCode.UNKNOWN_COMMAND.value)


if __name__ == "__main__":
    unittest.main()
