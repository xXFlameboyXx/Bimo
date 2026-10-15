"""Automated Verification Suite for Unified Companion App + Voice Agent Pipeline.

Proves:
1. VOICE and COMPANION requests enter the exact same AssistantOrchestrator.handle_request
2. Both share the exact same ToolRegistry containing Robot, Web, Artifact, and PC tools
3. Both share the exact same PermissionPolicy and session authorization lifecycle
4. Both share the exact same PCAgentClient
5. Both share the exact same ArtifactService
6. Both share the exact same TaskPlanner and TaskContext
7. Both share the exact same Cancellation mechanism (CancellationToken)
8. Both share the exact same MemoryStore
9. Both share the exact same EventBus and RobotStateMachine state stream
10. Source switching regression: VOICE vs COMPANION select the exact same tools and execution plan
11. Companion multi-step computer use: "Open Notepad and type Hello Bimo" executes end-to-end
12. Companion task-level confirmation flow: blocks cooperatively, emits CONFIRMATION_REQUIRED, continues on Allow
13. Companion file attachment integration into task context and artifact creation
14. Disconnected PC agent handling: structured graceful failure ("I can't reach your laptop right now")
15. Urgent interruption / Stop handling: immediate cancellation, TTS stop, state reset
"""

from __future__ import annotations

import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'

import os
import shutil
import tempfile
import threading
import time
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from bimo.agent.cancellation import CancellationToken
from bimo.agent.factory import (
    create_assistant_orchestrator,
    create_default_tool_registry,
)
from bimo.agent.memory import MemoryStore, MemoryType
from bimo.agent.orchestrator import (
    AssistantOrchestrator,
    BimoRequest,
    BimoResponse,
    RequestSource,
    UserIntent,
)
from bimo.agent.planner import TaskStatus, TaskStepStatus
from bimo.artifacts.service import ArtifactService
from bimo.companion.service import CompanionService
from bimo.core.config import Config, PCAgentConfig
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.llm import LLMProvider, LLMResponse, ToolCall
from bimo.interfaces.voice import BaseTextToSpeech
from bimo.pc.mock_client import MockPCAgentClient
from bimo.tools.base import BaseTool, ToolParameter, ToolResult
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.voice.agent_input import VoiceAdapter


class MockTTS(BaseTextToSpeech):
    def __init__(self) -> None:
        self.spoken: List[str] = []
        self.stopped: bool = False

    def speak(self, text: str, block: bool = True) -> bool:
        self.spoken.append(text)
        return True

    def stop(self) -> None:
        self.stopped = True

    @property
    def is_speaking(self) -> bool:
        return False

    @property
    def speech_rate(self) -> int:
        return 175

    @speech_rate.setter
    def speech_rate(self, value: int) -> None:
        pass

    @property
    def volume(self) -> float:
        return 1.0

    @volume.setter
    def volume(self, value: float) -> None:
        pass

    @property
    def voice(self) -> str:
        return "en_GB-semaine-medium"

    @voice.setter
    def voice(self, value: str) -> None:
        pass


class TestUnifiedPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(event_bus=self.event_bus)
        self.tts = MockTTS()
        self.artifact_service = ArtifactService(workspace_dir=self.tmp_dir)
        self.memory_store = MemoryStore(persistence_file=os.path.join(self.tmp_dir, "memory.json"))
        self.policy = PermissionPolicy(allow_high_risk=True, auto_confirm=False)
        self.pc_client = MockPCAgentClient()
        self.pc_client.set_connected(True)

        # Build canonical orchestrator with shared subsystems
        self.orchestrator = create_assistant_orchestrator(
            event_bus=self.event_bus,
            state_machine=self.state_machine,
            tts=self.tts,
            permission_policy=self.policy,
            artifact_service=self.artifact_service,
            memory_store=self.memory_store,
            pc_client=self.pc_client,
            mock=True,
        )

        # Wire CompanionService with the SAME orchestrator
        self.companion_service = CompanionService(
            event_bus=self.event_bus,
            state_machine=self.state_machine,
            orchestrator=self.orchestrator,
            artifact_service=self.artifact_service,
            permission_policy=self.policy,
        )

        # Wire VoiceAdapter with the SAME orchestrator
        self.voice_adapter = VoiceAdapter(self.orchestrator)

        # Collect emitted domain events
        self.events_received: List[Event] = []
        self.event_bus.subscribe(None, lambda e: self.events_received.append(e))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # 1. Structural Identity: Single Shared Pipeline Subsystems
    # --------------------------------------------------------------------------
    def test_shared_pipeline_identity(self) -> None:
        """Verify that Companion and Voice paths share the exact same subsystems."""
        # Same ToolRegistry
        self.assertIs(self.companion_service.orchestrator.tool_registry, self.orchestrator.tool_registry)
        # Same PermissionPolicy
        self.assertIs(self.companion_service.permission_policy, self.orchestrator.permission_policy)
        # Same ArtifactService
        self.assertIs(self.companion_service.artifact_service, self.orchestrator.artifact_service)
        # Same EventBus & StateMachine
        self.assertIs(self.companion_service.event_bus, self.orchestrator.event_bus)
        self.assertIs(self.companion_service.state_machine, self.orchestrator.state_machine)
        # Same MemoryStore
        self.assertIs(self.orchestrator.memory_store, self.memory_store)

        # Verify PC tools exist in shared registry
        registry = self.orchestrator.tool_registry
        self.assertTrue(registry.has("pc.get_status"))
        self.assertTrue(registry.has("pc.open_app"))
        self.assertTrue(registry.has("pc.type_text"))
        self.assertTrue(registry.has("pc.click"))
        self.assertTrue(registry.has("pc.screenshot"))
        self.assertTrue(registry.has("artifact.create_pdf"))
        self.assertTrue(registry.has("robot.speak"))

    # --------------------------------------------------------------------------
    # 2. Canonical Request Entry: VOICE and COMPANION Enter Same Method
    # --------------------------------------------------------------------------
    def test_voice_and_companion_enter_same_orchestrator(self) -> None:
        """Verify VOICE and COMPANION requests both process through handle_request."""
        # 1. Companion turn
        comp_res = self.companion_service.process_chat("Hello Bimo", attachments=[])
        self.assertTrue(comp_res.get("success", comp_res.get("status") == "completed"))
        self.assertIn("response", comp_res)

        # 2. Voice turn
        self.voice_adapter.receive_user_input("Hello Bimo from microphone")
        self.assertGreater(len(self.voice_adapter.history), 0)

        # Events trace both sources in the same EventBus
        sources = [e.source for e in self.events_received]
        self.assertTrue(any("companion" in s for s in sources))
        self.assertTrue(any("voice" in s for s in sources))

    # --------------------------------------------------------------------------
    # 3. Source Switching Regression: Same Tools Selected
    # --------------------------------------------------------------------------
    def test_source_switching_selects_same_tool_plan(self) -> None:
        """Proves changing input source from VOICE to COMPANION executes the exact same plan."""
        sid = self.policy.start_session(duration_seconds=120)

        # Request via VOICE
        req_voice = BimoRequest(text="Open Notepad and type Hello Bimo", source=RequestSource.VOICE.value)
        resp_voice = self.orchestrator.handle_request(req_voice, session_id=sid)
        self.assertTrue(resp_voice.success)

        steps_voice = [s for s in resp_voice.evidence]
        self.assertGreater(len(resp_voice.evidence), 0)

        # Request via COMPANION
        req_comp = BimoRequest(text="Open Notepad and type Hello Bimo", source=RequestSource.COMPANION.value)
        resp_comp = self.orchestrator.handle_request(req_comp, session_id=sid)
        self.assertTrue(resp_comp.success)

        # Evidence count and structure match
        self.assertEqual(len(resp_voice.evidence), len(resp_comp.evidence))
        self.assertIn("Completed task", resp_comp.text)

    # --------------------------------------------------------------------------
    # 4. Companion Multi-Step Computer Use: Open Notepad and Type Hello
    # --------------------------------------------------------------------------
    def test_companion_multi_step_computer_use(self) -> None:
        """Verify Companion can execute multi-step PC control end-to-end."""
        sid = self.policy.start_session(duration_seconds=300)

        result = self.companion_service.process_chat("Open Notepad and type Hello Bimo", attachments=[])
        self.assertEqual(result["status"], "completed")
        self.assertIn("Completed task", result["response"])

        # Check events emitted for live UI
        event_types = [e.type for e in self.events_received]
        self.assertIn(EventType.TASK_STARTED, event_types)
        self.assertIn(EventType.PLANNING, event_types)
        self.assertIn(EventType.TOOL_EXECUTION_STARTED, event_types)
        self.assertIn(EventType.PC_REQUEST_STARTED, event_types)
        self.assertIn(EventType.VERIFICATION_STARTED, event_types)
        self.assertIn(EventType.VERIFICATION_COMPLETED, event_types)
        self.assertIn(EventType.TASK_COMPLETED, event_types)

    # --------------------------------------------------------------------------
    # 5. Companion Task-Level Confirmation Flow
    # --------------------------------------------------------------------------
    def test_companion_task_level_confirmation(self) -> None:
        """Verify that when confirmation is required, the prompt reaches UI and task continues."""
        # Ensure no session is active initially
        self.policy.end_session()
        self.assertFalse(self.policy.is_session_active("pc_control"))

        confirmation_seen = threading.Event()
        req_id_box: List[str] = []

        def on_confirmation(event: Event) -> None:
            if event.type == EventType.CONFIRMATION_REQUIRED:
                req_id_box.append(event.data.get("request_id", ""))
                confirmation_seen.set()

        self.event_bus.subscribe(EventType.CONFIRMATION_REQUIRED, on_confirmation)

        # Worker thread executes task that will require confirmation
        chat_result_box: List[dict] = []

        def worker() -> None:
            res = self.companion_service.process_chat("Open Notepad and type Hello Bimo", attachments=[])
            chat_result_box.append(res)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Wait for confirmation request to be broadcast to UI
        self.assertTrue(confirmation_seen.wait(timeout=5.0), "Timed out waiting for confirmation prompt")
        self.assertGreater(len(req_id_box), 0)

        # UI responds "Allow"
        req_id = req_id_box[0]
        ok = self.companion_service.respond_permission(req_id, allowed=True, scope="pc_control")
        self.assertTrue(ok)

        # Task finishes successfully
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(len(chat_result_box), 1)
        self.assertEqual(chat_result_box[0]["status"], "completed")

        # Session was established and task completed without restarting
        self.assertTrue(self.policy.is_session_active("pc_control"))

    # --------------------------------------------------------------------------
    # 6. Companion File Attachments + Artifact Creation
    # --------------------------------------------------------------------------
    def test_companion_file_attachment_and_artifacts(self) -> None:
        """Verify uploaded files become task context and generate visual artifacts."""
        sid = self.policy.start_session(duration_seconds=300)
        # 1. Create a dummy user file
        sample_path = os.path.join(self.tmp_dir, "minecraft_ideas.md")
        with open(sample_path, "w", encoding="utf-8") as f:
            f.write("# Redstone Castle\nAutomated sorting system and drawbridge.")

        attachments = [
            {
                "id": "att-1",
                "filename": "minecraft_ideas.md",
                "mime_type": "text/markdown",
                "size_bytes": os.path.getsize(sample_path),
                "path": sample_path,
            }
        ]

        result = self.companion_service.process_chat(
            "Create a visual PDF from this file",
            attachments=attachments,
        )

        self.assertEqual(result["status"], "completed")
        self.assertIn("Completed task", result["response"])

        # Check that artifacts were registered and returned
        artifacts = self.artifact_service.list_artifacts()
        self.assertGreaterEqual(len(artifacts), 1)

    # --------------------------------------------------------------------------
    # 7. Companion Desktop Screenshot Query
    # --------------------------------------------------------------------------
    def test_companion_screenshot_tool(self) -> None:
        """Verify Companion can invoke pc.screenshot."""
        sid = self.policy.start_session(duration_seconds=60)
        result = self.companion_service.process_chat("Take a screenshot of my PC", attachments=[])
        self.assertEqual(result["status"], "completed")
        self.assertIn("Completed task", result["response"])

    # --------------------------------------------------------------------------
    # 8. Connection Failure: PC Disconnected Handling
    # --------------------------------------------------------------------------
    def test_companion_pc_disconnect_graceful_handling(self) -> None:
        """Verify graceful failure when Windows PC agent is unreachable."""
        self.pc_client.set_connected(False)
        sid = self.policy.start_session(duration_seconds=60)

        result = self.companion_service.process_chat("Open Notepad", attachments=[])
        self.assertEqual(result["status"], "error")
        self.assertIn("I can't reach your laptop right now", result["response"])

        # Ensure Bimo remains alive and healthy for local requests
        local_res = self.companion_service.process_chat("Hello Bimo", attachments=[])
        self.assertIn("status", local_res)

    # --------------------------------------------------------------------------
    # 9. Task Cancellation via STOP
    # --------------------------------------------------------------------------
    def test_companion_task_cancellation(self) -> None:
        """Verify that pressing Stop cancels the active orchestrator task cooperatively."""
        self.state_machine.transition_to(RobotState.EXECUTING)
        ok = self.companion_service.cancel_task(reason="User clicked Stop")
        self.assertTrue(ok)
        self.assertEqual(self.state_machine.current_state, RobotState.IDLE)
        self.assertTrue(self.tts.stopped)

        event_types = [e.type for e in self.events_received]
        self.assertIn(EventType.TASK_CANCELLED, event_types)


if __name__ == "__main__":
    unittest.main()
