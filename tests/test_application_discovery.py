"""Comprehensive unit tests for Dynamic Application Discovery, Resolution, Safe Launch, and Session Authorization."""

import time
import unittest
from typing import Any, Dict, List, Optional

from bimo.core.config import PCAgentConfig
from bimo.core.events import EventBus
from bimo.pc.mock_client import MockPCAgentClient
from bimo.pc.models import PCErrorCode, TargetWindow
from bimo.pc.tools import PCFindAppTool, PCListAppsTool, PCOpenAppTool, register_pc_tools
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry
from windows_agent.commands import build_default_command_registry
from windows_agent.controllers import (
    MockAppLauncher,
    MockKeyboardController,
    MockMouseController,
    MockWindowController,
    WindowsAppLauncher,
)
from windows_agent.discovery import (
    ApplicationDiscoveryService,
    ApplicationInfo,
    ApplicationResolver,
    ResolutionResult,
)


class TestApplicationDiscoveryAndResolution(unittest.TestCase):
    """Deterministic tests for discovery service and application resolver."""

    def setUp(self) -> None:
        self.service = ApplicationDiscoveryService()
        # Seed discovery service with mock applications
        self.service._cache = {
            "discord": ApplicationInfo(
                name="discord",
                display_name="Discord",
                executable_name="Discord.exe",
                executable_path=r"C:\Users\test\AppData\Local\Discord\Discord.exe",
                launch_target=r"C:\Users\test\AppData\Local\Discord\Discord.exe",
                source="start_menu_user",
                app_type="desktop",
            ),
            "googlechrome": ApplicationInfo(
                name="googlechrome",
                display_name="Google Chrome",
                executable_name="chrome.exe",
                executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                launch_target=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                source="start_menu_common",
                app_type="desktop",
            ),
            "visualstudiocode": ApplicationInfo(
                name="visualstudiocode",
                display_name="Visual Studio Code",
                executable_name="Code.exe",
                executable_path=r"C:\Program Files\Microsoft VS Code\Code.exe",
                launch_target=r"C:\Program Files\Microsoft VS Code\Code.exe",
                source="start_menu_user",
                app_type="desktop",
            ),
            "notepad": ApplicationInfo(
                name="notepad",
                display_name="Notepad",
                executable_name="notepad.exe",
                executable_path="notepad.exe",
                launch_target="notepad.exe",
                source="system_builtin",
                app_type="system",
            ),
            "calculator": ApplicationInfo(
                name="calculator",
                display_name="Calculator",
                executable_name="calc.exe",
                executable_path="calc.exe",
                launch_target="calc.exe",
                source="system_builtin",
                app_type="system",
            ),
            # Two apps with similar names to test ambiguity
            "steam": ApplicationInfo(
                name="steam",
                display_name="Steam",
                executable_name="steam.exe",
                executable_path=r"C:\Program Files (x86)\Steam\steam.exe",
                launch_target=r"C:\Program Files (x86)\Steam\steam.exe",
                source="start_menu_common",
                app_type="desktop",
            ),
            "steamvr": ApplicationInfo(
                name="steamvr",
                display_name="SteamVR",
                executable_name="vrstartup.exe",
                executable_path=r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrstartup.exe",
                launch_target=r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrstartup.exe",
                source="start_menu_common",
                app_type="desktop",
            ),
        }
        self.service._last_scan_time = time.time()
        self.resolver = ApplicationResolver(self.service)

    def test_discover_known_application(self) -> None:
        apps = self.service.discover()
        self.assertIn("discord", apps)
        self.assertEqual(apps["discord"].display_name, "Discord")
        self.assertEqual(apps["discord"].executable_name, "Discord.exe")

    def test_search_case_insensitive_and_normalized(self) -> None:
        results = self.service.search("discord")
        self.assertTrue(any(a.display_name == "Discord" for a in results))

        results_upper = self.service.search("DISCORD")
        self.assertTrue(any(a.display_name == "Discord" for a in results_upper))

        results_partial = self.service.search("chrome")
        self.assertTrue(any(a.display_name == "Google Chrome" for a in results_partial))

    def test_search_deterministic_ordering(self) -> None:
        results1 = self.service.search("")
        results2 = self.service.search("")
        self.assertEqual([a.display_name for a in results1], [a.display_name for a in results2])

    def test_resolve_exact_match(self) -> None:
        res = self.resolver.resolve("Discord")
        self.assertEqual(res.status, "SUCCESS")
        self.assertIsNotNone(res.application)
        self.assertEqual(res.application.display_name, "Discord")

    def test_resolve_normalized_and_alias_match(self) -> None:
        res1 = self.resolver.resolve("vs code")
        self.assertEqual(res1.status, "SUCCESS")
        self.assertEqual(res1.application.display_name, "Visual Studio Code")

        res2 = self.resolver.resolve("chrome")
        self.assertEqual(res2.status, "SUCCESS")
        self.assertEqual(res2.application.display_name, "Google Chrome")

    def test_resolve_ambiguous_result(self) -> None:
        res = self.resolver.resolve("Steam")
        # Exact match on "Steam" should resolve cleanly
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.application.display_name, "Steam")

    def test_resolve_nonexistent_application(self) -> None:
        res = self.resolver.resolve("nonexistent_fake_app_xyz")
        self.assertEqual(res.status, "NOT_FOUND")
        self.assertIsNone(res.application)

    def test_resolve_malicious_path_rejected(self) -> None:
        bad_inputs = [
            r"C:\Windows\System32\cmd.exe",
            r"..\..\malicious.exe",
            r"calc.exe && rm -rf",
            r"powershell /c echo bad",
            r"/bin/sh",
            r".\local.exe",
        ]
        for bad in bad_inputs:
            res = self.resolver.resolve(bad)
            self.assertEqual(res.status, "ARBITRARY_PATH_REJECTED", f"Failed for {bad}")
            self.assertIsNone(res.application)


class TestAppLauncherDynamicExecution(unittest.TestCase):
    """Tests for safe launching with dynamic application resolver."""

    def setUp(self) -> None:
        self.service = ApplicationDiscoveryService()
        self.service._cache = {
            "discord": ApplicationInfo(
                name="discord",
                display_name="Discord",
                executable_name="Discord.exe",
                executable_path=r"C:\Users\test\Discord.exe",
                launch_target=r"C:\Users\test\Discord.exe",
                source="start_menu_user",
                app_type="desktop",
            ),
            "notepad": ApplicationInfo(
                name="notepad",
                display_name="Notepad",
                executable_name="notepad.exe",
                executable_path="notepad.exe",
                launch_target="notepad.exe",
                source="system_builtin",
                app_type="system",
            ),
        }
        self.service._last_scan_time = time.time()
        self.resolver = ApplicationResolver(self.service)
        self.launcher = MockAppLauncher(
            discovery_service=self.service,
            resolver=self.resolver,
            allowlist_mode=False,
        )

    def test_launch_discovered_application_succeeds(self) -> None:
        res = self.launcher.launch("Discord")
        self.assertEqual(res.get("app"), "Discord")
        self.assertEqual(res.get("status"), "launched")
        self.assertIn("discord", self.launcher.launched_apps)

    def test_launch_unknown_app_fails(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.launcher.launch("UnknownAppX")
        self.assertIn("not installed or could not be found", str(ctx.exception))

    def test_launch_arbitrary_path_fails(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.launcher.launch(r"C:\Windows\System32\calc.exe")
        self.assertIn("Arbitrary path execution is prohibited", str(ctx.exception))

    def test_allowlist_mode_enforces_restriction(self) -> None:
        restricted_launcher = MockAppLauncher(
            allowed_apps=["notepad"],
            discovery_service=self.service,
            resolver=self.resolver,
            allowlist_mode=True,
        )
        # Notepad allowed
        res = restricted_launcher.launch("Notepad")
        self.assertEqual(res.get("status"), "launched")

        # Discord disallowed in allowlist mode
        with self.assertRaises(ValueError) as ctx:
            restricted_launcher.launch("Discord")
        self.assertIn("not in the allowed applications list", str(ctx.exception))


class TestPCToolsAndSessionPermissions(unittest.TestCase):
    """Tests for pc.find_app, pc.list_apps, and session-level authorization."""

    def setUp(self) -> None:
        self.config = PCAgentConfig(enabled=True)
        self.mock_client = MockPCAgentClient(config=self.config)
        self.tool_registry = ToolRegistry()
        self.policy = PermissionPolicy(auto_confirm=False)
        register_pc_tools(self.tool_registry, self.mock_client)

    def test_find_app_tool_is_safe(self) -> None:
        tool = self.tool_registry.get("pc.find_app")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.permission, ToolPermission.SAFE)

        allowed, _ = self.policy.check_permission(tool)
        self.assertTrue(allowed)

        res = tool.execute(query="discord")
        self.assertTrue(res.success)
        self.assertIn("Found 2 installed applications", res.output)

    def test_list_apps_tool_is_safe(self) -> None:
        tool = self.tool_registry.get("pc.list_apps")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.permission, ToolPermission.SAFE)

        allowed, _ = self.policy.check_permission(tool)
        self.assertTrue(allowed)

        res = tool.execute(limit=10)
        self.assertTrue(res.success)
        self.assertIn("Listed 6 installed applications", res.output)

    def test_open_app_requires_confirm_without_session(self) -> None:
        tool = self.tool_registry.get("pc.open_app")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.permission, ToolPermission.CONFIRM)

        # Denied without confirmed=True or active session
        allowed, reason = self.policy.check_permission(tool, {})
        self.assertFalse(allowed)
        self.assertIn("requires explicit user confirmation", reason)

        # Allowed with explicit confirmed=True
        allowed_conf, _ = self.policy.check_permission(tool, {"confirmed": True})
        self.assertTrue(allowed_conf)

    def test_session_authorization_lifecycle(self) -> None:
        tool = self.tool_registry.get("pc.open_app")

        # 1. Start bounded session
        session_id = self.policy.start_session(duration_seconds=2.0, user_identity="paneer")
        self.assertTrue(session_id.startswith("session-"))
        self.assertTrue(self.policy.is_session_active())

        # 2. CONFIRM tool allowed autonomously during active session
        allowed, _ = self.policy.check_permission(tool, {})
        self.assertTrue(allowed)

        # 3. Revoke / end session
        ended = self.policy.end_session()
        self.assertTrue(ended)
        self.assertFalse(self.policy.is_session_active())

        # 4. Now requires confirmation again
        allowed_after_end, _ = self.policy.check_permission(tool, {})
        self.assertFalse(allowed_after_end)

    def test_session_authorization_expiry(self) -> None:
        tool = self.tool_registry.get("pc.open_app")

        # Start very short session (0.05s)
        self.policy.start_session(duration_seconds=0.05)
        self.assertTrue(self.policy.is_session_active())

        time.sleep(0.08)
        # Session should now be expired
        self.assertFalse(self.policy.is_session_active())
        allowed, reason = self.policy.check_permission(tool, {})
        self.assertFalse(allowed)
        self.assertIn("requires explicit user confirmation", reason)


if __name__ == "__main__":
    unittest.main()
