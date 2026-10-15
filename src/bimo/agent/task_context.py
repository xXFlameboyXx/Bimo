"""Bounded short-term task context tracking for Bimo.

Provides:
- TaskContext: Tracks active task, current app, current target window, current artifact,
  last action, last observation, and bounded recent steps.
- Bounded limits: TASK_CONTEXT_MAX_ITEMS=30, TASK_CONTEXT_MAX_TEXT=50000.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TASK_CONTEXT_MAX_ITEMS = 30
TASK_CONTEXT_MAX_TEXT = 50000


@dataclass
class ContextItem:
    """A bounded item of historical context within a task."""
    item_type: str  # "action", "observation", "app", "window", "artifact", "error"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_type": self.item_type,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


class TaskContext:
    """Manages short-term task context with bounded item counts and text sizes."""

    def __init__(
        self,
        max_items: int = TASK_CONTEXT_MAX_ITEMS,
        max_text_chars: int = TASK_CONTEXT_MAX_TEXT,
    ) -> None:
        self.max_items = max_items
        self.max_text_chars = max_text_chars

        self.current_task_id: Optional[str] = None
        self.current_goal: Optional[str] = None
        self.current_app: Optional[str] = None
        self.current_target_hwnd: Optional[int] = None
        self.current_target_title: Optional[str] = None
        self.current_artifact_id: Optional[str] = None
        self.current_artifact_path: Optional[str] = None
        self.current_selected_item: Optional[str] = None
        self.last_action: Optional[str] = None
        self.last_observation: Optional[str] = None

        self._items: List[ContextItem] = []

    def set_task(self, task_id: str, goal: str) -> None:
        self.current_task_id = task_id
        self.current_goal = goal
        self.add_item("task", f"Started task '{task_id}': {goal}")

    def set_app(self, app_name: str) -> None:
        self.current_app = app_name
        self.add_item("app", f"Current application set to '{app_name}'")

    def set_target_window(self, hwnd: int, title: str) -> None:
        self.current_target_hwnd = hwnd
        self.current_target_title = title
        self.add_item("window", f"Target window bound to HWND {hwnd} ('{title}')", metadata={"hwnd": hwnd, "title": title})

    def set_artifact(self, artifact_id: str, path: str) -> None:
        self.current_artifact_id = artifact_id
        self.current_artifact_path = path
        self.add_item("artifact", f"Current artifact set to '{artifact_id}' at {path}")

    def record_action(self, action_name: str, arguments: Dict[str, Any]) -> None:
        self.last_action = action_name
        summary = f"Executed {action_name}({arguments})"
        self.add_item("action", summary, metadata={"action": action_name, "args": arguments})

    def record_observation(self, observation: str) -> None:
        self.last_observation = observation
        self.add_item("observation", observation)

    def add_item(self, item_type: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Add a context item while enforcing max item count and text length limits."""
        # Truncate content if individually too long
        if len(content) > 5000:
            content = content[:5000] + "... [truncated]"

        item = ContextItem(item_type=item_type, content=content, metadata=metadata or {})
        self._items.append(item)

        # Enforce item count limit
        if len(self._items) > self.max_items:
            self._items = self._items[-self.max_items:]

        # Enforce total text limit
        total_len = sum(len(i.content) for i in self._items)
        while total_len > self.max_text_chars and len(self._items) > 1:
            removed = self._items.pop(0)
            total_len -= len(removed.content)

    def get_summary(self) -> str:
        """Render a concise summary string of active task context for LLM prompt insertion."""
        lines = []
        if self.current_task_id:
            lines.append(f"Active Task: {self.current_task_id} ({self.current_goal})")
        if self.current_app:
            lines.append(f"Current App: {self.current_app}")
        if self.current_target_hwnd:
            lines.append(f"Current Target Window: HWND {self.current_target_hwnd} ('{self.current_target_title}')")
        if self.current_artifact_path:
            lines.append(f"Current Artifact: {self.current_artifact_path}")
        if self.last_action:
            lines.append(f"Last Action: {self.last_action}")
        if self.last_observation:
            lines.append(f"Last Observation: {self.last_observation}")

        recent = self._items[-5:]
        if recent:
            lines.append("Recent Context:")
            for it in recent:
                lines.append(f"  - [{it.item_type}] {it.content}")
        return "\n".join(lines)

    def clear(self) -> None:
        self.current_task_id = None
        self.current_goal = None
        self.current_app = None
        self.current_target_hwnd = None
        self.current_target_title = None
        self.current_artifact_id = None
        self.current_artifact_path = None
        self.current_selected_item = None
        self.last_action = None
        self.last_observation = None
        self._items.clear()
