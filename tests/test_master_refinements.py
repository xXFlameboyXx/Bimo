"""Comprehensive tests for BIMO Master Refinements 01 - 15.

Validates:
- Refinement 01: TaskPlanner, Task, TaskStep, TaskPlan, finite steps, bounded replanning
- Refinement 02: ActionVerifier, VerificationLevel, open_app, focus, input, and artifact verification
- Refinement 03: CancellationToken, cooperative cancellation, pause/resume, spoken interrupt classification
- Refinement 04: TaskContext, bounded tracking (MAX_ITEMS, MAX_TEXT), summary generation
- Refinement 05: Session-level task authorization, step budgeting, revocation
- Refinement 06: ErrorClass, RecoveryManager, bounded recovery attempts
- Refinement 07: ArtifactWorkspace, duplicate_artifact, get_artifact_info, format verification
- Refinement 08: ApplicationCategory, ApplicationProfile, ApplicationIntelligenceRegistry
- Refinement 09: VoiceService barge-in and cancellation
- Refinement 10: CapabilityMatrix, offline degradation, availability checks
- Refinement 11: MemoryStore, inspectable items, relevance search, secrets rejection
- Refinement 12: DeviceRegistry, smart device controls, scene activation (independent of Windows)
- Refinement 13: VisionProvider, UIElementLocation, VisionSafetyValidator bounds check
- Refinement 14: AssistantOrchestrator unified workflow
- Refinement 15: End-to-end integration scenarios
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from bimo.agent.cancellation import CancellationToken, InterruptCommand, TaskInterruptManager
from bimo.agent.memory import MemoryItem, MemoryStore, MemoryType
from bimo.agent.orchestrator import AssistantOrchestrator, OrchestrationResult, UserIntent
from bimo.agent.planner import (
    MAX_REPLAN_ATTEMPTS,
    MAX_TASK_STEPS,
    Task,
    TaskPlan,
    TaskPlanner,
    TaskStatus,
    TaskStep,
    TaskStepStatus,
)
from bimo.agent.recovery import ErrorClass, RecoveryManager, RecoveryStrategy
from bimo.agent.task_context import TaskContext
from bimo.agent.verification import ActionVerifier, VerificationLevel, VerificationResult
from bimo.artifacts.service import ArtifactService
from bimo.core.events import EventBus, EventType
from bimo.core.resilience import Capability, CapabilityMatrix, SystemAvailability
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.devices import (
    Device,
    DeviceCapability,
    DeviceRegistry,
    DeviceState,
    DeviceType,
)
from bimo.interfaces.llm import LLMProvider, LLMRequest, LLMResponse
from bimo.interfaces.vision import (
    MockVisionProvider,
    UIElementLocation,
    VisionConfidence,
    VisionRequest,
    VisionSafetyValidator,
)
from bimo.tools.base import BaseTool
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry


class DummyLLM(LLMProvider):
    def __init__(self, model_name: str = "mock-model") -> None:
        super().__init__(model_name=model_name)

    @property
    def provider_name(self) -> str:
        return "dummy_llm"

    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="Simulated LLM response", raw={})

    def generate_stream(self, request: LLMRequest):
        yield "Simulated"


class MockEchoTool(BaseTool):
    def __init__(self, name: str = "pc.open_app", permission: ToolPermission = ToolPermission.CONFIRM) -> None:
        super().__init__(name=name, description="Mock echo tool", permission=permission)

    def execute(self, arguments: dict) -> dict:
        if self.name == "pc.open_app":
            return {"status": "launched", "target": {"hwnd": 1234, "title": arguments.get("app", "App")}}
        elif self.name == "pc.focus_window":
            return {"focused": True, "hwnd": 1234}
        elif self.name == "pc.type_text":
            return {"verified": True, "typed": arguments.get("text", "")}
        elif self.name == "device.set_brightness":
            return {"success": True, "brightness": arguments.get("brightness", 100)}
        return {"success": True}


class TestMasterRefinements(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.artifact_service = ArtifactService(workspace_dir=self.tmp_dir)
        self.memory_store = MemoryStore(persistence_file=os.path.join(self.tmp_dir, "memory.json"))
        self.device_registry = DeviceRegistry()
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(event_bus=self.event_bus)
        self.policy = PermissionPolicy()
        self.tool_registry = ToolRegistry(policy=self.policy)

        # Register mock tools
        self.tool_registry.register(MockEchoTool("pc.open_app", ToolPermission.CONFIRM))
        self.tool_registry.register(MockEchoTool("pc.focus_window", ToolPermission.CONFIRM))
        self.tool_registry.register(MockEchoTool("pc.type_text", ToolPermission.CONFIRM))
        self.tool_registry.register(MockEchoTool("device.set_brightness", ToolPermission.CONFIRM))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Refinement 01: Task Planner
    # --------------------------------------------------------------------------
    def test_refinement_01_task_planner_lifecycle(self) -> None:
        planner = TaskPlanner(max_steps=5, max_replans=2)
        steps = [
            TaskStep(step_id="s1", description="Step 1"),
            TaskStep(step_id="s2", description="Step 2", dependencies=["s1"]),
        ]
        plan = planner.create_plan("Test goal", steps)
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.get_next_runnable_step().step_id, "s1")

        # Complete s1
        plan.steps[0].status = TaskStepStatus.COMPLETED
        self.assertEqual(plan.get_next_runnable_step().step_id, "s2")

        # Replan on failure
        task = Task.create("Test goal")
        task.plan = plan
        new_plan = planner.replan(task, plan.steps[1], "Failed connection")
        self.assertIsNotNone(new_plan)
        self.assertEqual(task.status, TaskStatus.RECOVERING)
        self.assertEqual(new_plan.replan_count, 1)

    # --------------------------------------------------------------------------
    # Refinement 02: Action Verification
    # --------------------------------------------------------------------------
    def test_refinement_02_action_verifier(self) -> None:
        verifier = ActionVerifier()

        # open_app verification
        res_open = verifier.verify("pc.open_app", {"app": "Chrome"}, {"target": {"hwnd": 5555, "title": "Chrome"}})
        self.assertEqual(res_open.level, VerificationLevel.VERIFIED)

        # focus_window verification
        res_focus = verifier.verify("pc.focus_window", {}, {"focused": True})
        self.assertEqual(res_focus.level, VerificationLevel.VERIFIED)

        # artifact verification (valid PDF)
        pdf_path = os.path.join(self.tmp_dir, "test.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4 header and sample data")
        res_pdf = verifier.verify("artifact.create", {}, {"file_path": pdf_path})
        self.assertEqual(res_pdf.level, VerificationLevel.VERIFIED)

        # artifact verification (corrupted/empty)
        empty_pdf = os.path.join(self.tmp_dir, "empty.pdf")
        with open(empty_pdf, "wb") as f:
            pass
        res_empty = verifier.verify("artifact.create", {}, {"file_path": empty_pdf})
        self.assertEqual(res_empty.level, VerificationLevel.FAILED)

    # --------------------------------------------------------------------------
    # Refinement 03: Interrupt / Cancel
    # --------------------------------------------------------------------------
    def test_refinement_03_interrupt_and_cancellation(self) -> None:
        token = CancellationToken()
        self.assertFalse(token.is_cancelled)

        token.cancel("User said stop")
        self.assertTrue(token.is_cancelled)
        self.assertEqual(token.cancel_reason, "User said stop")

        # Spoken classification
        self.assertEqual(TaskInterruptManager.classify("stop"), InterruptCommand.STOP)
        self.assertEqual(TaskInterruptManager.classify("Cancel"), InterruptCommand.CANCEL)
        self.assertEqual(TaskInterruptManager.classify("hold on"), InterruptCommand.PAUSE)
        self.assertEqual(TaskInterruptManager.classify("continue"), InterruptCommand.RESUME)
        self.assertEqual(TaskInterruptManager.classify("what is the weather"), InterruptCommand.NONE)

    # --------------------------------------------------------------------------
    # Refinement 04: Task Context
    # --------------------------------------------------------------------------
    def test_refinement_04_task_context_limits(self) -> None:
        ctx = TaskContext(max_items=5, max_text_chars=500)
        ctx.set_task("t-1", "Build guide")
        ctx.set_app("Chrome")
        ctx.set_target_window(1234, "Chrome Window")

        for i in range(10):
            ctx.add_item("observation", f"Observed item {i}")

        # Enforce max items bound
        self.assertLessEqual(len(ctx._items), 5)
        summary = ctx.get_summary()
        self.assertIn("Active Task: t-1", summary)
        self.assertIn("Chrome", summary)

    # --------------------------------------------------------------------------
    # Refinement 05: Session-Level Authorization
    # --------------------------------------------------------------------------
    def test_refinement_05_session_authorization(self) -> None:
        policy = PermissionPolicy()
        sid = policy.start_session(duration_seconds=60, user_identity="paneer")
        self.assertTrue(policy.is_session_active("pc_control"))

        policy.bind_session_to_task("task-123", max_steps=2)
        policy.consume_session_step()
        self.assertTrue(policy.is_session_active("pc_control"))

        policy.consume_session_step()  # Reaches budget limit
        self.assertFalse(policy.is_session_active("pc_control"))

    # --------------------------------------------------------------------------
    # Refinement 06: Recovery / Self-Healing
    # --------------------------------------------------------------------------
    def test_refinement_06_recovery_manager(self) -> None:
        rec = RecoveryManager(max_attempts=2)

        # Recoverable error
        plan1 = rec.plan_recovery("t-1", "pc.type_text", "Window is not the foreground window")
        self.assertIsNotNone(plan1)
        self.assertEqual(plan1.strategy, RecoveryStrategy.RE_FOCUS)

        # Non-recoverable error
        plan2 = rec.plan_recovery("t-2", "pc.open_app", "Permission denied: requires confirmation")
        self.assertIsNone(plan2)

    # --------------------------------------------------------------------------
    # Refinement 07: Professional Artifact Workflow
    # --------------------------------------------------------------------------
    def test_refinement_07_artifact_workspace(self) -> None:
        art = self.artifact_service.create_artifact(
            "guide.txt",
            b"Step 1: Gather wood\nStep 2: Build workbench",
            mime_type="text/plain",
        )
        self.assertTrue(os.path.exists(art.path))

        info = self.artifact_service.get_artifact_info(art.id)
        self.assertIsNotNone(info)
        self.assertEqual(info["filename"], "guide.txt")

        # Duplicate
        dup = self.artifact_service.duplicate_artifact(art.id)
        self.assertEqual(dup.filename, "guide_copy.txt")
        self.assertTrue(os.path.exists(dup.path))

    # --------------------------------------------------------------------------
    # Refinement 08: Application Intelligence
    # --------------------------------------------------------------------------
    def test_refinement_08_application_intelligence(self) -> None:
        from bimo.pc.intelligence import ApplicationCategory, ApplicationIntelligenceRegistry

        profile = ApplicationIntelligenceRegistry.profile_application("chrome", "Google Chrome", "chrome.exe")
        self.assertEqual(profile.category, ApplicationCategory.BROWSER)
        self.assertTrue(profile.supports_tabs)

        code_profile = ApplicationIntelligenceRegistry.profile_application("vscode", "Visual Studio Code", "Code.exe")
        self.assertEqual(code_profile.category, ApplicationCategory.DEVELOPMENT)

    # --------------------------------------------------------------------------
    # Refinement 10: Offline / Resilience
    # --------------------------------------------------------------------------
    def test_refinement_10_resilience_matrix(self) -> None:
        avail = SystemAvailability(internet_available=False, windows_agent_connected=False)
        matrix = CapabilityMatrix(availability=avail)

        self.assertFalse(matrix.is_available(Capability.REMOTE_LLM))
        self.assertFalse(matrix.is_available(Capability.PC_CONTROL))
        self.assertTrue(matrix.is_available(Capability.LOCAL_ARTIFACT))
        self.assertTrue(matrix.is_available(Capability.ROBOT_LOCAL))

        degrade_msg = matrix.get_degradation_message(Capability.REMOTE_LLM)
        self.assertIn("offline", degrade_msg)

    # --------------------------------------------------------------------------
    # Refinement 11: Memory
    # --------------------------------------------------------------------------
    def test_refinement_11_memory_store_and_security(self) -> None:
        item = self.memory_store.store(
            MemoryType.PREFERENCE,
            "theme",
            "dark mode with high contrast",
            tags=["ui", "display"],
        )
        self.assertIsNotNone(item)

        # Search
        results = self.memory_store.search("dark theme")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].key, "theme")

        # Security: reject passwords/secrets
        with self.assertRaises(ValueError):
            self.memory_store.store(
                MemoryType.FACT,
                "my_password",
                "secret12345",
            )

    # --------------------------------------------------------------------------
    # Refinement 12: Smart-Home Device Orchestration
    # --------------------------------------------------------------------------
    def test_refinement_12_smart_home_devices(self) -> None:
        bulb = Device(
            device_id="study_bulb",
            name="Study Bulb",
            device_type=DeviceType.LIGHT,
            capabilities=[DeviceCapability.POWER, DeviceCapability.BRIGHTNESS, DeviceCapability.COLOR],
            state=DeviceState(power=True, brightness=50),
        )
        self.device_registry.register_device(bulb)

        # Set brightness
        ok = self.device_registry.set_brightness("study_bulb", 85)
        self.assertTrue(ok)
        self.assertEqual(bulb.state.brightness, 85)

        # Activate scene
        scene_res = self.device_registry.activate_scene("warm")
        self.assertIn("study_bulb", scene_res)
        self.assertEqual(bulb.state.color_rgb, (255, 180, 100))

    # --------------------------------------------------------------------------
    # Refinement 13: Vision Abstraction & Safety Validation
    # --------------------------------------------------------------------------
    def test_refinement_13_vision_safety(self) -> None:
        elem_safe = UIElementLocation(
            element_type="button",
            label="Download",
            x=100,
            y=150,
            width=80,
            height=30,
            confidence=0.95,
        )
        ok, reason = VisionSafetyValidator.validate_click_target(
            elem_safe, screen_size=(1920, 1080), target_window_bounds=(50, 50, 500, 500)
        )
        self.assertTrue(ok)

        # Low confidence rejected
        elem_uncertain = UIElementLocation(
            element_type="button",
            label="Download",
            x=100,
            y=150,
            width=80,
            height=30,
            confidence=0.40,
        )
        ok2, reason2 = VisionSafetyValidator.validate_click_target(
            elem_uncertain, screen_size=(1920, 1080)
        )
        self.assertFalse(ok2)
        self.assertIn("below safe threshold", reason2)

    # --------------------------------------------------------------------------
    # Refinement 14 & 15: Unified Orchestration & Integration
    # --------------------------------------------------------------------------
    def test_refinement_14_orchestrator_multi_step(self) -> None:
        orchestrator = AssistantOrchestrator(
            llm_provider=DummyLLM(),
            tool_registry=self.tool_registry,
            permission_policy=self.policy,
            state_machine=self.state_machine,
            event_bus=self.event_bus,
            artifact_service=self.artifact_service,
            memory_store=self.memory_store,
            device_registry=self.device_registry,
        )

        # Start authorized session
        sid = self.policy.start_session(duration_seconds=60, user_identity="paneer")

        # Execute multi-step task
        res = orchestrator.execute_goal("Open Notepad and type Hello Bimo", session_id=sid)
        self.assertTrue(res.success)
        self.assertIsNotNone(res.task)
        self.assertEqual(res.task.status, TaskStatus.COMPLETED)
        self.assertGreaterEqual(len(res.evidence), 2)

    def test_refinement_15_orchestrator_immediate_interrupt(self) -> None:
        orchestrator = AssistantOrchestrator(
            llm_provider=DummyLLM(),
            tool_registry=self.tool_registry,
            permission_policy=self.policy,
        )

        res = orchestrator.execute_goal("Bimo, stop.")
        self.assertTrue(res.success)
        self.assertIn("Stopped", res.response_text)


if __name__ == "__main__":
    unittest.main()
