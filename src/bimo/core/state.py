"""Robot state machine and state definitions for Bimo.

Provides the RobotState enum and a decoupled, event-driven state machine.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from enum import Enum
from typing import Any

from bimo.core.events import Event, EventBus, EventType

logger = logging.getLogger(__name__)


class RobotState(str, Enum):
    """Core operating states for the Bimo robot."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    SPEAKING = "SPEAKING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    SLEEPING = "SLEEPING"


StateTransitionListener = Callable[[RobotState, RobotState, str], None]


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


class RobotStateMachine:
    """Manages the robot's operating state and transitions.

    Decoupled from graphics and hardware. Communicates changes via EventBus
    and explicit listener callbacks.
    """

    # Permitted natural transitions between states.
    # Emergency transitions to ERROR, IDLE, or SLEEPING are permitted from anywhere when forced.
    VALID_TRANSITIONS: dict[RobotState, set[RobotState]] = {
        RobotState.IDLE: {
            RobotState.LISTENING,
            RobotState.THINKING,
            RobotState.SLEEPING,
            RobotState.EXECUTING,
            RobotState.SPEAKING,
            RobotState.ERROR,
        },
        RobotState.LISTENING: {
            RobotState.THINKING,
            RobotState.IDLE,
            RobotState.ERROR,
            RobotState.SLEEPING,
            RobotState.SPEAKING,
        },
        RobotState.THINKING: {
            RobotState.EXECUTING,
            RobotState.SPEAKING,
            RobotState.IDLE,
            RobotState.ERROR,
            RobotState.LISTENING,
        },
        RobotState.EXECUTING: {
            RobotState.SUCCESS,
            RobotState.THINKING,
            RobotState.ERROR,
            RobotState.SPEAKING,
            RobotState.IDLE,
        },
        RobotState.SPEAKING: {
            RobotState.IDLE,
            RobotState.LISTENING,
            RobotState.EXECUTING,
            RobotState.ERROR,
        },
        RobotState.SUCCESS: {
            RobotState.IDLE,
            RobotState.SPEAKING,
            RobotState.THINKING,
            RobotState.EXECUTING,
            RobotState.LISTENING,
        },
        RobotState.ERROR: {
            RobotState.IDLE,
            RobotState.SLEEPING,
            RobotState.LISTENING,
        },
        RobotState.SLEEPING: {
            RobotState.IDLE,
            RobotState.LISTENING,
            RobotState.ERROR,
        },
    }

    def __init__(
        self,
        initial_state: RobotState = RobotState.IDLE,
        event_bus: EventBus | None = None,
        auto_subscribe_events: bool = True,
    ) -> None:
        self._state = initial_state
        self._previous_state = initial_state
        self._event_bus = event_bus
        self._listeners: list[StateTransitionListener] = []
        self._lock = threading.RLock()

        if self._event_bus and auto_subscribe_events:
            self._register_event_handlers()

    @property
    def current_state(self) -> RobotState:
        """Return the current robot state."""
        with self._lock:
            return self._state

    @property
    def previous_state(self) -> RobotState:
        """Return the previous robot state."""
        with self._lock:
            return self._previous_state

    def add_listener(self, listener: StateTransitionListener) -> None:
        """Register a callback for state transition notifications."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: StateTransitionListener) -> bool:
        """Unregister a transition listener."""
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)
                return True
            return False

    def can_transition_to(self, new_state: RobotState) -> bool:
        """Check if transition to new_state is allowed by rule set."""
        with self._lock:
            if new_state == self._state:
                return True
            allowed = self.VALID_TRANSITIONS.get(self._state, set())
            return new_state in allowed

    def transition_to(
        self,
        new_state: RobotState,
        reason: str = "",
        force: bool = False,
    ) -> bool:
        """Perform a state transition.

        Args:
            new_state: Target state to enter.
            reason: Contextual description of why transition occurred.
            force: If True, bypasses transition validation rules (e.g., manual override).

        Returns:
            True if transition succeeded, False if rejected.
        """
        with self._lock:
            old_state = self._state
            if old_state == new_state and not force:
                return True

            if not force and not self.can_transition_to(new_state):
                logger.warning(
                    "Invalid state transition rejected: %s -> %s (reason: %s)",
                    old_state.value,
                    new_state.value,
                    reason,
                )
                return False

            self._previous_state = old_state
            self._state = new_state
            logger.info(
                "Robot state changed: %s -> %s [reason: %s]",
                old_state.value,
                new_state.value,
                reason,
            )

        # Notify direct listeners
        for listener in self._listeners:
            try:
                listener(old_state, new_state, reason)
            except Exception as e:
                logger.exception("Error in state transition listener: %s", e)

        # Publish event to EventBus if attached
        if self._event_bus:
            self._event_bus.publish(
                Event(
                    type=EventType.STATE_CHANGED,
                    data={
                        "from_state": old_state.value,
                        "to_state": new_state.value,
                        "reason": reason,
                    },
                    source="state_machine",
                )
            )

        return True

    def handle_event(self, event: Event) -> bool:
        """Process incoming domain events and trigger appropriate state transitions.

        Returns True if a state transition occurred as a result.
        """
        with self._lock:
            current = self._state

        match event.type:
            case EventType.WAKE_WORD_DETECTED | EventType.LISTENING_STARTED:
                if current in (
                    RobotState.IDLE,
                    RobotState.SLEEPING,
                    RobotState.ERROR,
                    RobotState.SPEAKING,
                ):
                    return self.transition_to(
                        RobotState.LISTENING, reason=f"{event.type.value} event"
                    )

            case EventType.LISTENING_STOPPED:
                if current == RobotState.LISTENING:
                    reason = event.data.get("reason", "Listening stopped")
                    if reason != "speech_captured":
                        return self.transition_to(
                            RobotState.IDLE, reason=reason
                        )
                    return False

            case (
                EventType.USER_SPOKE
                | EventType.VOICE_ACTIVITY_STARTED
                | EventType.USER_SPEAKING
            ):
                # If we're already listening, stay in listening; otherwise wake up or barge-in
                if current != RobotState.LISTENING and current in (
                    RobotState.IDLE,
                    RobotState.SLEEPING,
                    RobotState.ERROR,
                    RobotState.THINKING,
                    RobotState.SPEAKING,
                ):
                    return self.transition_to(
                        RobotState.LISTENING, reason="Voice activity detected"
                    )

            case EventType.VOICE_ACTIVITY_STOPPED:
                logger.debug("Voice activity stopped")
                return False

            case EventType.SPEECH_RECEIVED:
                if current in (RobotState.LISTENING, RobotState.IDLE):
                    return self.transition_to(
                        RobotState.THINKING, reason="Speech input received"
                    )

            case EventType.SPEECH_ERROR | EventType.SPEECH_TRANSCRIPTION_ERROR | EventType.WAKE_WORD_ERROR:
                err_msg = event.data.get("error", "Speech processing error")
                return self.transition_to(
                    RobotState.ERROR, reason=f"Speech error: {err_msg}", force=True
                )

            case EventType.SPEECH_OUTPUT_STARTED:
                return self.transition_to(
                    RobotState.SPEAKING, reason="Speech output started"
                )

            case EventType.SPEECH_OUTPUT_FINISHED | EventType.SPEECH_OUTPUT_CANCELLED:
                if current == RobotState.SPEAKING:
                    return self.transition_to(
                        RobotState.IDLE, reason="Speech output finished"
                    )
                return False

            case EventType.SPEECH_OUTPUT_ERROR:
                err_msg = event.data.get("error", "TTS speech error")
                return self.transition_to(
                    RobotState.ERROR, reason=f"TTS error: {err_msg}", force=True
                )

            case EventType.AI_STARTED:
                if current != RobotState.THINKING:
                    return self.transition_to(
                        RobotState.THINKING, reason="AI processing started"
                    )

            case EventType.AI_FINISHED:
                next_action = event.data.get("action", "speak")
                if next_action == "tool":
                    return self.transition_to(
                        RobotState.EXECUTING, reason="AI requested tool execution"
                    )
                elif next_action == "speak":
                    return self.transition_to(
                        RobotState.SPEAKING, reason="AI generated speech response"
                    )
                else:
                    return self.transition_to(
                        RobotState.IDLE, reason="AI processing finished"
                    )

            case EventType.AI_ERROR:
                err_msg = event.data.get("error", "AI processing error")
                return self.transition_to(
                    RobotState.ERROR, reason=f"AI error: {err_msg}", force=True
                )

            case EventType.TASK_STARTED | EventType.TOOL_STARTED | EventType.TOOL_EXECUTION_STARTED:
                return self.transition_to(
                    RobotState.EXECUTING,
                    reason=f"Execution started ({event.data.get('name', 'unnamed')})",
                )

            case EventType.TOOL_COMPLETED | EventType.TASK_COMPLETED | EventType.TOOL_EXECUTION_COMPLETED:
                return self.transition_to(
                    RobotState.SUCCESS,
                    reason=f"Completed successfully ({event.data.get('name', 'task')})",
                )

            case EventType.TASK_FAILED | EventType.TOOL_EXECUTION_FAILED | EventType.TOOL_EXECUTION_DENIED:
                return self.transition_to(
                    RobotState.ERROR,
                    reason=f"Failed: {event.data.get('error', 'tool execution error')}",
                )

            case EventType.LAPTOP_CONNECTED | EventType.PC_CONNECTED:
                logger.info("PC / Laptop connected: %s", event.data.get("name", "pc"))
                return False

            case EventType.LAPTOP_DISCONNECTED | EventType.PC_DISCONNECTED:
                logger.info("PC / Laptop disconnected")
                return False

            case EventType.PC_AUTH_FAILED:
                logger.warning("PC agent authentication failed: %s", event.data.get("error", "auth error"))
                return False

            case _:
                return False

        return False

    def _register_event_handlers(self) -> None:
        """Subscribe state machine to handle domain events on the EventBus."""
        if not self._event_bus:
            return

        events_to_handle = [
            EventType.WAKE_WORD_DETECTED,
            EventType.WAKE_WORD_ERROR,
            EventType.LISTENING_STARTED,
            EventType.LISTENING_STOPPED,
            EventType.SPEECH_CAPTURE_STARTED,
            EventType.SPEECH_CAPTURE_STOPPED,
            EventType.USER_SPOKE,
            EventType.VOICE_ACTIVITY_STARTED,
            EventType.VOICE_ACTIVITY_STOPPED,
            EventType.USER_SPEAKING,
            EventType.SPEECH_RECEIVED,
            EventType.SPEECH_TRANSCRIPTION_STARTED,
            EventType.SPEECH_TRANSCRIPTION_COMPLETED,
            EventType.SPEECH_TRANSCRIPTION_ERROR,
            EventType.SPEECH_ERROR,
            EventType.SPEECH_OUTPUT_STARTED,
            EventType.SPEECH_OUTPUT_FINISHED,
            EventType.SPEECH_OUTPUT_ERROR,
            EventType.SPEECH_OUTPUT_CANCELLED,
            EventType.AI_STARTED,
            EventType.AI_FINISHED,
            EventType.AI_ERROR,
            EventType.TASK_STARTED,
            EventType.TOOL_STARTED,
            EventType.TOOL_COMPLETED,
            EventType.TASK_COMPLETED,
            EventType.TASK_FAILED,
            EventType.TOOL_CALL_REQUESTED,
            EventType.TOOL_EXECUTION_STARTED,
            EventType.TOOL_EXECUTION_COMPLETED,
            EventType.TOOL_EXECUTION_FAILED,
            EventType.TOOL_EXECUTION_DENIED,
            EventType.LAPTOP_CONNECTED,
            EventType.LAPTOP_DISCONNECTED,
            EventType.PC_CONNECTED,
            EventType.PC_DISCONNECTED,
            EventType.PC_AUTH_FAILED,
            EventType.PC_REQUEST_STARTED,
            EventType.PC_REQUEST_COMPLETED,
            EventType.PC_REQUEST_FAILED,
        ]

        for et in events_to_handle:
            self._event_bus.subscribe(et, self.handle_event)
