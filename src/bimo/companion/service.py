"""Service layer coordinating Bimo brain subsystems with Companion App clients."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set
from fastapi import WebSocket

from bimo.agent.bimo_agent import BimoAgent
from bimo.agent.cancellation import CancellationToken
from bimo.agent.orchestrator import AssistantOrchestrator, BimoRequest, RequestSource
from bimo.artifacts.models import Artifact
from bimo.artifacts.service import ArtifactService, SUPPORTED_EXTENSIONS
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.tools.permissions import PermissionPolicy, ToolPermission

logger = logging.getLogger(__name__)


class CompanionService:
    """Singleton/central service orchestrating backend data and events for Companion clients."""

    def __init__(
        self,
        event_bus: EventBus,
        state_machine: RobotStateMachine,
        agent: Optional[BimoAgent] = None,
        artifact_service: Optional[ArtifactService] = None,
        permission_policy: Optional[PermissionPolicy] = None,
        orchestrator: Optional[AssistantOrchestrator] = None,
    ) -> None:
        self.event_bus = event_bus
        self.state_machine = state_machine
        self.artifact_service = artifact_service or ArtifactService()
        
        # Unify permission policy
        if permission_policy is not None:
            self.permission_policy = permission_policy
        elif orchestrator is not None:
            self.permission_policy = orchestrator.permission_policy
        elif agent is not None and agent.permission_policy is not None:
            self.permission_policy = agent.permission_policy
        else:
            self.permission_policy = PermissionPolicy(allow_high_risk=True)

        # Wire interactive confirmation handler into PermissionPolicy
        self.permission_policy.confirmation_handler = self.request_permission

        # Unify canonical AssistantOrchestrator
        self.orchestrator = orchestrator
        self.agent = agent
        if self.orchestrator is None:
            if self.agent is not None:
                self.orchestrator = AssistantOrchestrator(
                    llm_provider=self.agent.llm_provider,
                    tool_registry=self.agent.tool_registry,
                    permission_policy=self.permission_policy,
                    state_machine=self.state_machine,
                    event_bus=self.event_bus,
                    tts=self.agent.tts,
                    artifact_service=self.artifact_service,
                    agent=self.agent,
                )
            else:
                from bimo.agent.factory import create_assistant_orchestrator
                self.orchestrator = create_assistant_orchestrator(
                    event_bus=self.event_bus,
                    state_machine=self.state_machine,
                    permission_policy=self.permission_policy,
                    artifact_service=self.artifact_service,
                )
                self.agent = self.orchestrator.agent

        self._active_connections: Set[WebSocket] = set()
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Track pending permission requests: request_id -> prompt dict
        self._pending_permissions: Dict[str, dict[str, Any]] = {}
        self._permission_events: Dict[str, threading.Event] = {}
        self._permission_results: Dict[str, bool] = {}

        # Active task tracking
        self._active_task_id: Optional[str] = None
        self._active_cancellation_token: Optional[CancellationToken] = None
        self._current_status_text: str = "Hey! I'm ready ♡"

        # Register event listeners on event_bus
        self._register_event_subscribers()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _register_event_subscribers(self) -> None:
        """Subscribe to Bimo domain events to forward to connected companion frontends."""
        forwarded_events = [
            EventType.AI_STARTED,
            EventType.AI_FINISHED,
            EventType.AI_ERROR,
            EventType.TOOL_STARTED,
            EventType.TOOL_COMPLETED,
            EventType.TOOL_EXECUTION_STARTED,
            EventType.TOOL_EXECUTION_COMPLETED,
            EventType.TOOL_EXECUTION_FAILED,
            EventType.TOOL_EXECUTION_DENIED,
            EventType.SPEECH_OUTPUT_STARTED,
            EventType.SPEECH_OUTPUT_FINISHED,
            EventType.SPEECH_OUTPUT_ERROR,
            EventType.SPEECH_OUTPUT_CANCELLED,
            EventType.LISTENING_STARTED,
            EventType.LISTENING_STOPPED,
            EventType.TASK_STARTED,
            EventType.TASK_COMPLETED,
            EventType.TASK_FAILED,
            EventType.PC_CONNECTED,
            EventType.PC_DISCONNECTED,
            EventType.PC_REQUEST_STARTED,
            EventType.PC_REQUEST_COMPLETED,
            EventType.PLANNING,
            EventType.VERIFICATION_STARTED,
            EventType.VERIFICATION_COMPLETED,
            EventType.CONFIRMATION_REQUIRED,
            EventType.CONFIRMATION_RESOLVED,
            EventType.TASK_CANCELLED,
        ]

        for et in forwarded_events:
            self.event_bus.subscribe(et, self._on_domain_event)

        # State machine transition listener accepts (old_state, new_state, reason)
        self.state_machine.add_listener(self._on_state_change)

    def _on_state_change(self, old_state: RobotState, new_state: RobotState, reason: str = "") -> None:
        """Forward state machine transitions immediately to clients."""
        default_status_map = {
            RobotState.IDLE: "Hey! I'm ready ♡",
            RobotState.LISTENING: "Listening...",
            RobotState.THINKING: "Thinking...",
            RobotState.EXECUTING: "Working on it...",
            RobotState.SPEAKING: "Speaking...",
            RobotState.SUCCESS: "Done! ✨",
            RobotState.ERROR: "Something went wrong...",
            RobotState.SLEEPING: "Resting...",
        }
        status_text = default_status_map.get(new_state, f"State: {new_state.value.lower()}")
        self._current_status_text = status_text

        msg = {
            "type": "state_change",
            "state": new_state.value.lower(),
            "old_state": old_state.value.lower(),
            "status_text": status_text,
            "timestamp": time.time(),
        }
        self.broadcast_message(msg)

    def _on_domain_event(self, event: Event) -> None:
        """Handle domain events from event bus."""
        event_type = event.type.value
        payload: dict[str, Any] = {
            "type": "domain_event",
            "event_type": event_type,
            "data": dict(event.data),
            "timestamp": time.time(),
        }

        # Status text refinements based on specific events
        if event.type == EventType.AI_STARTED:
            self._current_status_text = "Thinking..."
        elif event.type == EventType.PLANNING:
            self._current_status_text = "Planning steps..."
        elif event.type in (EventType.TOOL_STARTED, EventType.TOOL_EXECUTION_STARTED):
            tool_name = event.data.get("name") or event.data.get("tool", "")
            if "pdf" in tool_name or "visual_guide" in tool_name:
                self._current_status_text = "Creating your PDF..."
            elif "image" in tool_name:
                self._current_status_text = "Creating an image..."
            elif tool_name.startswith("pc."):
                self._current_status_text = "Working on your PC..."
            else:
                self._current_status_text = f"Executing {tool_name}..."
            payload["status_text"] = self._current_status_text
        elif event.type in (EventType.PC_REQUEST_STARTED,):
            self._current_status_text = "Controlling your PC..."
            payload["status_text"] = self._current_status_text
        elif event.type == EventType.VERIFICATION_STARTED:
            self._current_status_text = "Verifying actions..."
            payload["status_text"] = self._current_status_text
        elif event.type == EventType.CONFIRMATION_REQUIRED:
            self._current_status_text = "Waiting for permission..."
            payload["status_text"] = self._current_status_text
        elif event.type in (EventType.TOOL_COMPLETED, EventType.TOOL_EXECUTION_COMPLETED):
            self._current_status_text = "Done! ✨"
            payload["status_text"] = self._current_status_text
        elif event.type == EventType.TASK_CANCELLED:
            self._current_status_text = "Task stopped ♡"
            payload["status_text"] = self._current_status_text

        self.broadcast_message(payload)

    def register_client(self, websocket: WebSocket) -> None:
        with self._lock:
            self._active_connections.add(websocket)
        logger.info("Companion client connected (Total: %d)", len(self._active_connections))

    def unregister_client(self, websocket: WebSocket) -> None:
        with self._lock:
            self._active_connections.discard(websocket)
        logger.info("Companion client disconnected (Remaining: %d)", len(self._active_connections))

    def broadcast_message(self, message: dict[str, Any]) -> None:
        """Broadcast JSON payload to all connected Companion WebSockets."""
        with self._lock:
            clients = list(self._active_connections)
        if not clients:
            return

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_broadcast(clients, message), self._loop)

    async def _async_broadcast(self, clients: List[WebSocket], message: dict[str, Any]) -> None:
        for client in clients:
            try:
                await client.send_json(message)
            except Exception as err:
                logger.debug("Failed to send message to companion client: %s", err)

    def get_status(self) -> dict[str, Any]:
        """Return comprehensive live status for Companion."""
        with self._lock:
            cur_state = self.state_machine.current_state.value.lower()
            active_task = self._active_task_id
            status_text = self._current_status_text

        return {
            "status": "online",
            "state": cur_state,
            "status_text": status_text,
            "bimo_version": "0.1.0",
            "companion_api_version": "1.0.0",
            "voice": {
                "selected": "en_GB-semaine-medium",
                "available": ["en_GB-semaine-medium"],
            },
            "active_task": active_task,
            "timestamp": time.time(),
        }

    def process_chat(
        self,
        message: str,
        attachments: List[dict[str, Any]],
        conversation_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Process incoming user turn through canonical AssistantOrchestrator."""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        cancel_token = CancellationToken()

        with self._lock:
            self._active_task_id = task_id
            self._active_cancellation_token = cancel_token

        req = BimoRequest(
            text=message,
            source=RequestSource.COMPANION.value,
            request_id=task_id,
            conversation_id=conversation_id,
            attachments=attachments,
        )

        try:
            # Route through unified AssistantOrchestrator
            bimo_resp = self.orchestrator.handle_request(req, cancel_token=cancel_token)
            result = bimo_resp.to_dict()

            self.broadcast_message({
                "type": "chat_response",
                **result,
            })
            return result

        except Exception as err:
            logger.error("Error processing companion chat task %s: %s", task_id, err, exc_info=True)
            self.state_machine.transition_to(RobotState.ERROR, reason=str(err), force=True)
            err_result = {
                "task_id": task_id,
                "status": "error",
                "response": "I ran into a problem while processing that. Let's try again! ♡",
                "state": "error",
                "artifacts": [],
                "error_details": str(err),
                "created_at": time.time(),
            }
            self.broadcast_message({
                "type": "error",
                **err_result,
            })
            threading.Timer(3.0, lambda: self.state_machine.transition_to(RobotState.IDLE, reason="Error cooldown", force=True)).start()
            return err_result

        finally:
            with self._lock:
                if self._active_task_id == task_id:
                    self._active_task_id = None
                    self._active_cancellation_token = None

    def cancel_task(self, task_id: Optional[str] = None, reason: str = "User cancelled") -> bool:
        """Cancel active task cooperatively."""
        with self._lock:
            token = self._active_cancellation_token
            cur_task = self._active_task_id

        if self.orchestrator:
            self.orchestrator.cancel_task(task_id or cur_task, reason=reason)
        elif token:
            token.cancel(reason)

        if self.agent and self.agent.tts:
            self.agent.tts.stop()

        self.state_machine.transition_to(RobotState.IDLE, reason=f"Cancelled: {reason}", force=True)
        self.broadcast_message({
            "type": "task_cancelled",
            "task_id": task_id or cur_task,
            "reason": reason,
            "timestamp": time.time(),
        })
        return True

    def request_permission(
        self,
        tool_name: str,
        title: str,
        description: str,
        scope: str = "pc_control",
        timeout: float = 60.0,
    ) -> bool:
        """Prompt companion client for confirmation and block until decision or timeout."""
        request_id = f"perm-{uuid.uuid4().hex[:8]}"
        prompt_data = {
            "request_id": request_id,
            "tool_name": tool_name,
            "title": title,
            "description": description,
            "scope": scope,
            "created_at": time.time(),
        }

        event = threading.Event()
        with self._lock:
            self._pending_permissions[request_id] = prompt_data
            self._permission_events[request_id] = event

        # Emit domain event
        self.event_bus.publish(Event(
            EventType.CONFIRMATION_REQUIRED,
            data=prompt_data,
            source="companion_service",
        ))

        # Broadcast WebSocket event to Companion UI
        self.broadcast_message({
            "type": "permission_request",
            **prompt_data,
        })

        # Wait for user decision
        signaled = event.wait(timeout=timeout)
        with self._lock:
            allowed = self._permission_results.pop(request_id, False) if signaled else False
            self._pending_permissions.pop(request_id, None)
            self._permission_events.pop(request_id, None)

        self.event_bus.publish(Event(
            EventType.CONFIRMATION_RESOLVED,
            data={"request_id": request_id, "allowed": allowed, "tool_name": tool_name},
            source="companion_service",
        ))

        return allowed

    def respond_permission(self, request_id: str, allowed: bool, scope: str = "pc_control") -> bool:
        """Handle user decision from Companion UI."""
        with self._lock:
            event = self._permission_events.get(request_id)
            if not event:
                return False
            self._permission_results[request_id] = allowed

            if allowed:
                self.permission_policy.start_session(duration_seconds=300.0, scope=scope)
                if self.orchestrator and self.orchestrator.active_task:
                    self.permission_policy.bind_session_to_task(self.orchestrator.active_task.task_id)
                logger.info("Permission granted for %s; active session established.", request_id)

            event.set()

        self.broadcast_message({
            "type": "permission_resolved",
            "request_id": request_id,
            "allowed": allowed,
            "scope": scope,
            "timestamp": time.time(),
        })
        return True
