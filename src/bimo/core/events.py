"""Event definitions and asynchronous event bus for Bimo.

Provides typed events and a decoupled publish-subscribe event bus.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Core domain event types in Bimo."""

    # Voice / Audio interaction events
    WAKE_WORD_DETECTED = "WAKE_WORD_DETECTED"
    WAKE_WORD_ERROR = "WAKE_WORD_ERROR"
    LISTENING_STARTED = "LISTENING_STARTED"
    LISTENING_STOPPED = "LISTENING_STOPPED"
    SPEECH_CAPTURE_STARTED = "SPEECH_CAPTURE_STARTED"
    SPEECH_CAPTURE_STOPPED = "SPEECH_CAPTURE_STOPPED"
    USER_SPOKE = "USER_SPOKE"
    VOICE_ACTIVITY_STARTED = "VOICE_ACTIVITY_STARTED"
    VOICE_ACTIVITY_STOPPED = "VOICE_ACTIVITY_STOPPED"
    USER_SPEAKING = "USER_SPEAKING"
    SPEECH_RECEIVED = "SPEECH_RECEIVED"
    SPEECH_TRANSCRIPTION_STARTED = "SPEECH_TRANSCRIPTION_STARTED"
    SPEECH_TRANSCRIPTION_COMPLETED = "SPEECH_TRANSCRIPTION_COMPLETED"
    SPEECH_TRANSCRIPTION_ERROR = "SPEECH_TRANSCRIPTION_ERROR"
    SPEECH_ERROR = "SPEECH_ERROR"

    # Text-To-Speech (TTS) output events
    SPEECH_OUTPUT_STARTED = "SPEECH_OUTPUT_STARTED"
    SPEECH_OUTPUT_FINISHED = "SPEECH_OUTPUT_FINISHED"
    SPEECH_OUTPUT_ERROR = "SPEECH_OUTPUT_ERROR"
    SPEECH_OUTPUT_CANCELLED = "SPEECH_OUTPUT_CANCELLED"

    # AI / LLM processing events
    AI_STARTED = "AI_STARTED"
    AI_FINISHED = "AI_FINISHED"
    AI_ERROR = "AI_ERROR"

    # Task & Tool execution events
    TASK_STARTED = "TASK_STARTED"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TOOL_CALL_REQUESTED = "TOOL_CALL_REQUESTED"
    TOOL_EXECUTION_STARTED = "TOOL_EXECUTION_STARTED"
    TOOL_EXECUTION_COMPLETED = "TOOL_EXECUTION_COMPLETED"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_EXECUTION_DENIED = "TOOL_EXECUTION_DENIED"

    # Device & Connectivity events
    LAPTOP_CONNECTED = "LAPTOP_CONNECTED"
    LAPTOP_DISCONNECTED = "LAPTOP_DISCONNECTED"
    PC_CONNECTED = "PC_CONNECTED"
    PC_DISCONNECTED = "PC_DISCONNECTED"
    PC_AUTH_FAILED = "PC_AUTH_FAILED"
    PC_REQUEST_STARTED = "PC_REQUEST_STARTED"
    PC_REQUEST_COMPLETED = "PC_REQUEST_COMPLETED"
    PC_REQUEST_FAILED = "PC_REQUEST_FAILED"

    # Phase 8: Controlled Computer-Use & Desktop events
    PC_SCREENSHOT_CAPTURED = "PC_SCREENSHOT_CAPTURED"
    PC_MOUSE_ACTION = "PC_MOUSE_ACTION"
    PC_KEYBOARD_ACTION = "PC_KEYBOARD_ACTION"
    PC_WINDOW_FOCUSED = "PC_WINDOW_FOCUSED"
    PC_COMPUTER_USE_STARTED = "PC_COMPUTER_USE_STARTED"
    PC_COMPUTER_USE_STEP = "PC_COMPUTER_USE_STEP"
    PC_COMPUTER_USE_COMPLETED = "PC_COMPUTER_USE_COMPLETED"
    PC_COMPUTER_USE_FAILED = "PC_COMPUTER_USE_FAILED"
    PC_COMPUTER_USE_LIMIT_REACHED = "PC_COMPUTER_USE_LIMIT_REACHED"

    # State machine events
    STATE_CHANGED = "STATE_CHANGED"


@dataclass(frozen=True)
class Event:
    """Immutable event payload passed through the EventBus."""

    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return f"Event(type={self.type.value}, source={self.source}, data={self.data})"


EventHandler = Callable[[Event], None]


class EventBus:
    """Thread-safe publish/subscribe event bus.

    Enables decoupled communication across robot components:
    sensors, speech engines, AI coordinators, device monitors, and renderers.
    """

    def __init__(self, history_size: int = 50) -> None:
        self._subscribers: dict[EventType | None, list[EventHandler]] = {}
        self._lock = threading.RLock()
        self._history: deque[Event] = deque(maxlen=history_size)

    def subscribe(self, event_type: EventType | None, handler: EventHandler) -> None:
        """Subscribe a handler to a specific event type, or None for all events."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if handler not in self._subscribers[event_type]:
                self._subscribers[event_type].append(handler)
                logger.debug("Subscribed %s to event %s", handler, event_type)

    def unsubscribe(self, event_type: EventType | None, handler: EventHandler) -> bool:
        """Unsubscribe a handler from an event type. Returns True if removed."""
        with self._lock:
            if event_type in self._subscribers and handler in self._subscribers[event_type]:
                self._subscribers[event_type].remove(handler)
                if not self._subscribers[event_type]:
                    del self._subscribers[event_type]
                logger.debug("Unsubscribed %s from event %s", handler, event_type)
                return True
            return False

    def publish(self, event: Event) -> None:
        """Dispatch an event to all registered subscribers.

        Ensures that errors in one subscriber do not disrupt other subscribers.
        """
        with self._lock:
            self._history.append(event)
            # Gather specific subscribers and catch-all subscribers
            handlers = list(self._subscribers.get(event.type, []))
            catch_all = list(self._subscribers.get(None, []))

        all_handlers = handlers + catch_all
        for handler in all_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.exception(
                    "Error executing subscriber %s for event %s: %s",
                    handler,
                    event.type,
                    e,
                )

    def get_history(self) -> list[Event]:
        """Return a copy of recent events."""
        with self._lock:
            return list(self._history)

    def clear(self) -> None:
        """Clear all subscribers and history."""
        with self._lock:
            self._subscribers.clear()
            self._history.clear()
