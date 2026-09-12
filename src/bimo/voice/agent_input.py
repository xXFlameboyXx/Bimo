"""Agent input interface receiver for Phase 3 speech integration.

Collects transcribed user speech into the agent conversation turn buffer
without triggering LLM inference (strictly preserved for future phases).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from bimo.interfaces.voice import AgentInputInterface

logger = logging.getLogger(__name__)


class DefaultAgentInput(AgentInputInterface):
    """Thread-safe agent input receiver tracking user speech turns.

    Decoupled from LLM execution: stores the transcribed query, notifies
    registered listeners, and prepares the dialogue context for Phase 4+.
    """

    def __init__(self) -> None:
        self._history: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._listeners: list[Any] = []

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return chronological record of received user speech inputs."""
        with self._lock:
            return list(self._history)

    @property
    def last_input(self) -> str | None:
        """Return the most recently received user input text."""
        with self._lock:
            if self._history:
                return self._history[-1]["text"]
            return None

    @property
    def input_count(self) -> int:
        """Total number of transcribed speech inputs received."""
        with self._lock:
            return len(self._history)

    def add_listener(self, callback: Any) -> None:
        """Register a callback for new user speech input arrivals."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def receive_user_input(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Store transcribed user speech into the agent conversation buffer."""
        clean_text = text.strip()
        if not clean_text:
            return

        turn_entry = {
            "text": clean_text,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }

        with self._lock:
            self._history.append(turn_entry)
            listeners = list(self._listeners)

        logger.info(
            "Agent input received: '%s' (Total turns: %d)",
            clean_text,
            len(self._history),
        )

        for listener in listeners:
            try:
                listener(clean_text, metadata or {})
            except Exception as e:
                logger.error("Error in agent input listener callback: %s", e)

    def clear(self) -> None:
        """Clear conversation history."""
        with self._lock:
            self._history.clear()
