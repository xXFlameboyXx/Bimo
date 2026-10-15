"""Permission and authorization policies for Bimo tool execution.

Provides a 3-tier security model:
1. SAFE: Autonomous execution permitted (status queries, face changes, speech).
2. CONFIRM: Requires explicit user/system confirmation before execution.
3. HIGH_RISK: Requires elevated administrative confirmation (or denied by policy).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from bimo.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolPermission(str, Enum):
    """Permission levels for robot tool execution."""

    SAFE = "SAFE"
    CONFIRM = "CONFIRM"
    HIGH_RISK = "HIGH_RISK"


class PermissionPolicy:
    """Central policy engine for tool authorization.

    Enforces that:
    - SAFE tools execute autonomously.
    - CONFIRM tools require an explicit 'confirmed=True' flag in execution context.
    - HIGH_RISK tools require an explicit 'elevated_confirmed=True' flag in execution context.
    - Tools cannot alter or escalate their own permission level.
    - Unknown or missing permission defaults to HIGH_RISK.
    """

    def __init__(
        self,
        allow_high_risk: bool = False,
        auto_confirm: bool = False,
        confirmation_handler: Optional[Callable[[str, str, str, str], bool]] = None,
    ) -> None:
        self.allow_high_risk = allow_high_risk
        self.auto_confirm = auto_confirm
        self._session: dict[str, Any] | None = None
        self.confirmation_handler = confirmation_handler

    def start_session(
        self,
        duration_seconds: float = 300.0,
        user_identity: str = "paneer",
        scope: str = "pc_control",
    ) -> str:
        """Start a bounded, time-limited computer-control authorization session."""
        import time
        import uuid
        session_id = f"session-{uuid.uuid4().hex[:8]}"
        now = time.time()
        self._session = {
            "session_id": session_id,
            "user_identity": user_identity,
            "scope": scope,
            "start_time": now,
            "expiry_time": now + duration_seconds,
            "duration_seconds": duration_seconds,
            "task_id": None,
            "remaining_steps": 50,
        }
        logger.info(
            "Computer-control session started: id=%s, user=%s, scope=%s, duration=%.1fs",
            session_id, user_identity, scope, duration_seconds
        )
        return session_id

    def bind_session_to_task(self, task_id: str, max_steps: int = 30) -> None:
        """Bind active authorization session to a specific task and step budget."""
        if self._session:
            self._session["task_id"] = task_id
            self._session["remaining_steps"] = max_steps
            logger.info("Bound session %s to task %s with %d steps", self._session["session_id"], task_id, max_steps)

    def consume_session_step(self) -> bool:
        """Consume one authorized action step from session budget."""
        if self._session and "remaining_steps" in self._session:
            self._session["remaining_steps"] -= 1
            if self._session["remaining_steps"] <= 0:
                logger.info("Session %s reached step limit. Expiring session.", self._session["session_id"])
                self._session = None
                return False
        return True

    def end_session(self) -> bool:
        """Revoke the active authorization session."""
        if self._session:
            sid = self._session.get("session_id")
            self._session = None
            logger.info("Computer-control session ended/revoked: id=%s", sid)
            return True
        return False

    def is_session_active(self, scope: str = "pc_control") -> bool:
        """Check if an authorization session is currently active and unexpired."""
        import time
        if not self._session:
            return False
        if time.time() > self._session.get("expiry_time", 0.0):
            logger.info("Computer-control session expired: id=%s", self._session.get("session_id"))
            self._session = None
            return False
        if self._session.get("scope") != scope and self._session.get("scope") != "all":
            return False
        return True

    def check_permission(
        self, tool: BaseTool, context: dict[str, Any] | None = None
    ) -> tuple[bool, str | None]:
        """Check whether execution of the specified tool is authorized.

        Returns (is_allowed, error_reason_if_denied).
        """
        ctx = context or {}
        permission = getattr(tool, "permission", ToolPermission.HIGH_RISK)

        match permission:
            case ToolPermission.SAFE:
                return True, None

            case ToolPermission.CONFIRM:
                if ctx.get("confirmed") is True or self.auto_confirm or (tool.name.startswith("pc.") and self.is_session_active("pc_control")):
                    logger.info("Tool '%s' authorized with user confirmation or active session.", tool.name)
                    return True, None

                if self.confirmation_handler:
                    tool_desc = getattr(tool, "description", tool.name)
                    title = "Bimo wants to control your PC" if tool.name.startswith("pc.") else "Bimo needs permission"
                    scope = "pc_control" if tool.name.startswith("pc.") else "default"
                    logger.info("Invoking confirmation handler for tool '%s'...", tool.name)
                    allowed = self.confirmation_handler(tool.name, title, tool_desc, scope)
                    if allowed:
                        logger.info("Tool '%s' authorized via interactive confirmation handler.", tool.name)
                        return True, None
                    reason = f"Permission denied: User declined confirmation for '{tool.name}'."
                    logger.warning(reason)
                    return False, reason

                reason = f"Permission denied: Tool '{tool.name}' requires explicit user confirmation."
                logger.warning(reason)
                return False, reason

            case ToolPermission.HIGH_RISK:
                if not self.allow_high_risk:
                    reason = f"Permission denied: HIGH_RISK tool '{tool.name}' is prohibited by current policy."
                    logger.warning(reason)
                    return False, reason

                if ctx.get("elevated_confirmed") is True or self.auto_confirm:
                    logger.info("HIGH_RISK tool '%s' authorized with elevated confirmation.", tool.name)
                    return True, None

                if self.confirmation_handler:
                    tool_desc = getattr(tool, "description", tool.name)
                    title = f"Bimo needs elevated confirmation for {tool.name}"
                    scope = "high_risk"
                    allowed = self.confirmation_handler(tool.name, title, tool_desc, scope)
                    if allowed:
                        return True, None
                    reason = f"Permission denied: User declined elevated confirmation for '{tool.name}'."
                    logger.warning(reason)
                    return False, reason

                reason = f"Permission denied: HIGH_RISK tool '{tool.name}' requires elevated confirmation."
                logger.warning(reason)
                return False, reason

            case _:
                reason = f"Permission denied: Tool '{tool.name}' has unrecognized permission '{permission}'."
                logger.error(reason)
                return False, reason
