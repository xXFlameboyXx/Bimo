"""Permission and authorization policies for Bimo tool execution.

Provides a 3-tier security model:
1. SAFE: Autonomous execution permitted (status queries, face changes, speech).
2. CONFIRM: Requires explicit user/system confirmation before execution.
3. HIGH_RISK: Requires elevated administrative confirmation (or denied by policy).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

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

    def __init__(self, allow_high_risk: bool = False) -> None:
        self.allow_high_risk = allow_high_risk

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
                if ctx.get("confirmed") is True:
                    logger.info("Tool '%s' authorized with user confirmation.", tool.name)
                    return True, None
                reason = f"Permission denied: Tool '{tool.name}' requires explicit user confirmation."
                logger.warning(reason)
                return False, reason

            case ToolPermission.HIGH_RISK:
                if not self.allow_high_risk:
                    reason = f"Permission denied: HIGH_RISK tool '{tool.name}' is prohibited by current policy."
                    logger.warning(reason)
                    return False, reason

                if ctx.get("elevated_confirmed") is True:
                    logger.info("HIGH_RISK tool '%s' authorized with elevated confirmation.", tool.name)
                    return True, None

                reason = f"Permission denied: HIGH_RISK tool '{tool.name}' requires elevated confirmation."
                logger.warning(reason)
                return False, reason

            case _:
                reason = f"Permission denied: Tool '{tool.name}' has unrecognized permission '{permission}'."
                logger.error(reason)
                return False, reason
