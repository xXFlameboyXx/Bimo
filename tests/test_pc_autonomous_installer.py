"""Tests for high-autonomy PC operator and software installer tools."""

from __future__ import annotations

import unittest

from bimo.agent.mock_provider import MockLLMProvider
from bimo.agent.orchestrator import AssistantOrchestrator, BimoRequest
from bimo.agent.planner import Task, TaskPlanner, TaskStage, TaskStatus, TaskStep
from bimo.agent.recovery import ErrorClass, RecoveryManager, RecoveryStrategy
from bimo.agent.verification import ActionVerifier, VerificationLevel
from bimo.core.events import EventBus
from bimo.pc.mock_client import MockPCAgentClient
from bimo.pc.tools import (
    PCDownloadInstallerTool,
    PCPackageInstallTool,
    PCPackageSearchTool,
    PCPackageShowTool,
    PCRunInstallerTool,
    PCSystemInfoTool,
    PCVerifyAppRunningTool,
    PCVerifyInstallerTool,
    register_pc_tools,
)
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry


class TestAutonomousPCOperator(unittest.TestCase):
    """Test suite for autonomous installer and PC orchestration."""

    def setUp(self) -> None:
        self.mock_client = MockPCAgentClient()
        self.permission_policy = PermissionPolicy(auto_confirm=True)
        self.tool_registry = ToolRegistry(policy=self.permission_policy)
        register_pc_tools(self.tool_registry, self.mock_client)
        self.event_bus = EventBus()
        self.llm_provider = MockLLMProvider()

    def test_pc_system_info_tool(self):
        res = self.tool_registry.execute("pc.system_info")
        self.assertTrue(res.success)
        self.assertIn("PC System", res.output)
        self.assertIn("os", res.metadata)
        self.assertIn("architecture", res.metadata)
        self.assertIn("winget_available", res.metadata)

    def test_pc_package_search_and_show(self):
        res = self.tool_registry.execute("pc.package_search", query="KiCad")
        self.assertTrue(res.success)
        self.assertIn("packages", res.metadata)
        self.assertGreater(len(res.metadata["packages"]), 0)
        package_id = res.metadata["packages"][0]["id"]

        show_res = self.tool_registry.execute("pc.package_show", package_id=package_id)
        self.assertTrue(show_res.success)
        self.assertEqual(show_res.metadata["package"]["id"], package_id)
        self.assertIn("installer_type", show_res.metadata["package"])

    def test_pc_package_install(self):
        res = self.tool_registry.execute("pc.package_install", package_id="KiCad.KiCad")
        self.assertTrue(res.success)
        self.assertTrue(res.metadata["success"])
        self.assertEqual(res.metadata["return_code"], 0)

    def test_pc_download_verify_run_installer(self):
        # Download
        dl_res = self.tool_registry.execute(
            "pc.download_installer",
            url="https://kicad.org/download/kicad.exe",
            filename="kicad_setup.exe",
        )
        self.assertTrue(dl_res.success)
        target_path = dl_res.metadata["file_path"]
        self.assertIn("kicad_setup.exe", target_path)

        # Verify
        vf_res = self.tool_registry.execute(
            "pc.verify_installer",
            file_path=target_path,
            expected_sha256="fake_hash",
        )
        self.assertTrue(vf_res.success)
        self.assertTrue(vf_res.metadata["valid"])

        # Run
        run_res = self.tool_registry.execute(
            "pc.run_installer",
            installer_path=target_path,
            arguments="/S",
        )
        self.assertTrue(run_res.success)
        self.assertEqual(run_res.metadata["return_code"], 0)

    def test_pc_verify_app_running(self):
        res = self.tool_registry.execute("pc.verify_app_running", app_name="kicad.exe")
        self.assertTrue(res.success)
        self.assertTrue(res.metadata["is_running"])

    def test_step_verifier_software_steps(self):
        verifier = ActionVerifier()

        res_info = verifier.verify("pc.system_info", {}, {"os": "Windows 11", "winget_available": True})
        self.assertTrue(res_info.is_verified)

        res_search = verifier.verify("pc.package_search", {"query": "KiCad"}, {"matches": [{"id": "KiCad.KiCad"}]})
        self.assertTrue(res_search.is_verified)

        res_install = verifier.verify("pc.package_install", {"package_id": "KiCad.KiCad"}, {"installed": True, "exit_code": 0})
        self.assertTrue(res_install.is_verified)

        res_running = verifier.verify("pc.verify_app_running", {"app_name": "kicad.exe"}, {"running": True, "matching_processes": [{"pid": 1234}]})
        self.assertTrue(res_running.is_verified)

        # Failure case verification
        res_not_running = verifier.verify("pc.verify_app_running", {"app_name": "kicad.exe"}, {"running": False, "matching_processes": []})
        self.assertTrue(res_not_running.is_failed)

    def test_error_recovery_manager_fallback(self):
        recovery = RecoveryManager()
        plan = recovery.plan_recovery(
            task_id="task-123",
            tool_name="pc.package_install",
            error_message="Package installation failed exit_code 1603",
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.strategy, RecoveryStrategy.FALLBACK_DIRECT_DOWNLOAD)

    def test_error_recovery_manager_classification(self):
        recovery = RecoveryManager()
        err_class = recovery.classify_error("Elevation_required Win32 740")
        self.assertEqual(err_class, ErrorClass.NON_RECOVERABLE)

        err_recoverable = recovery.classify_error("Target window mismatch: expected KiCad")
        self.assertEqual(err_recoverable, ErrorClass.RECOVERABLE)

    def test_planner_stages(self):
        task = Task.create("Install KiCad PCB designer")
        self.assertEqual(task.status, TaskStatus.PLANNING)
        self.assertEqual(task.current_stage, "PLANNING")

        task.set_status(TaskStatus.RESEARCHING)
        self.assertEqual(task.current_stage, TaskStage.RESEARCHING.value)
        self.assertEqual(task.status, TaskStatus.RESEARCHING)

        task.set_status(TaskStatus.INSTALLING)
        self.assertEqual(task.current_stage, TaskStage.INSTALLING.value)
        self.assertEqual(task.status, TaskStatus.INSTALLING)

    def test_orchestrator_autonomous_pipeline(self):
        orchestrator = AssistantOrchestrator(
            llm_provider=self.llm_provider,
            tool_registry=self.tool_registry,
            permission_policy=self.permission_policy,
            event_bus=self.event_bus,
        )

        request = BimoRequest(
            text="Find a suitable PCB design application, download it from its official source, install it, launch it, and verify that it works."
        )
        response = orchestrator.handle_request(request)

        self.assertTrue(response.success)
        self.assertIn("KiCad", response.text)
        self.assertIn("verified", response.text.lower())


if __name__ == "__main__":
    unittest.main()
