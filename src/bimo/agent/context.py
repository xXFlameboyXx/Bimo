"""Thread-safe conversation context manager for Bimo Agent.

Maintains a bounded sliding window of dialogue turns, preserving the core
system prompt while pruning older conversation history to conserve token limits.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from bimo.agent.prompts import DEFAULT_BIMO_SYSTEM_PROMPT
from bimo.interfaces.llm import Message, Role, ToolCall

logger = logging.getLogger(__name__)


class ConversationContext:
    """Manages short-term conversation history for Bimo Agent.

    Features:
    - Maintains an immutable or configurable system prompt at index 0.
    - Preserves chronological ordering of user and assistant turns.
    - Enforces a configurable bound (max_history turns) to prevent context explosion.
    - Explicit reset and clear methods.
    - Fully thread-safe using re-entrant locks.
    """

    def __init__(
        self,
        max_history: int = 20,
        system_prompt: str = DEFAULT_BIMO_SYSTEM_PROMPT,
    ) -> None:
        if max_history < 1:
            raise ValueError("max_history must be at least 1")

        self.max_history = max_history
        self._system_prompt = system_prompt
        self._history: list[Message] = []
        self._lock = threading.RLock()

    @property
    def system_prompt(self) -> str:
        """Return the active system prompt."""
        with self._lock:
            return self._system_prompt

    def set_system_prompt(self, prompt: str) -> None:
        """Update the active system prompt."""
        with self._lock:
            self._system_prompt = prompt

    @property
    def message_count(self) -> int:
        """Return the number of dialogue turns (excluding the system prompt)."""
        with self._lock:
            return len(self._history)

    def add_user_message(self, content: str) -> Message:
        """Append a user utterance to conversation history."""
        msg = Message(role=Role.USER, content=content.strip())
        self.add_message(msg)
        return msg

    def add_assistant_message(
        self, content: str, tool_calls: list[ToolCall] | None = None
    ) -> Message:
        """Append an assistant response to conversation history."""
        msg = Message(
            role=Role.ASSISTANT,
            content=content.strip(),
            tool_calls=tool_calls or [],
        )
        self.add_message(msg)
        return msg

    def add_tool_result_message(
        self, tool_call_id: str, tool_name: str, content: str
    ) -> Message:
        """Append a tool execution result to conversation history."""
        msg = Message(
            role=Role.TOOL,
            content=content,
            name=tool_name,
            tool_call_id=tool_call_id,
        )
        self.add_message(msg)
        return msg

    def add_message(self, message: Message) -> None:
        """Append an arbitrary message and enforce sliding window capacity."""
        with self._lock:
            if message.role == Role.SYSTEM:
                self._system_prompt = message.content
                return

            self._history.append(message)
            # Prune oldest non-system messages if capacity exceeded
            if len(self._history) > self.max_history:
                overflow = len(self._history) - self.max_history
                del self._history[:overflow]

    def get_messages(self) -> list[Message]:
        """Construct the complete prompt payload: system message + recent history."""
        with self._lock:
            messages: list[Message] = []
            if self._system_prompt:
                messages.append(Message(role=Role.SYSTEM, content=self._system_prompt))
            messages.extend(self._history)
            return messages

    def clear(self) -> None:
        """Clear all conversation turns while retaining the active system prompt."""
        with self._lock:
            self._history.clear()
            logger.debug("ConversationContext cleared (system prompt retained).")

    def reset(self, system_prompt: str | None = None) -> None:
        """Clear all turns and optionally update the system prompt."""
        with self._lock:
            self._history.clear()
            if system_prompt is not None:
                self._system_prompt = system_prompt
            logger.debug("ConversationContext reset.")

    @property
    def last_user_message(self) -> str | None:
        """Return content of the most recent user turn, if any."""
        with self._lock:
            for msg in reversed(self._history):
                if msg.role == Role.USER:
                    return msg.content
            return None

    @property
    def last_assistant_message(self) -> str | None:
        """Return content of the most recent assistant turn, if any."""
        with self._lock:
            for msg in reversed(self._history):
                if msg.role == Role.ASSISTANT:
                    return msg.content
            return None
