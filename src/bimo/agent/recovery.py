"""Bounded Recovery and Self-Healing subsystem for Bimo.

Provides:
- ErrorClassification: RECOVERABLE vs NON_RECOVERABLE
- RecoveryStrategy: RE_RESOLVE_TARGET, RE_FOCUS, RE_CONNECT, RETRY_WITH_BACKOFF, ABORT
- RecoveryManager: Evaluates failure, classifies it, selects recovery strategy, and limits MAX_RECOVERY_ATTEMPTS=2.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MAX_RECOVERY_ATTEMPTS = 2


class ErrorClass(str, Enum):
    """Categorization of failure recoverability."""
    RECOVERABLE = "RECOVERABLE"
    NON_RECOVERABLE = "NON_RECOVERABLE"


class RecoveryStrategy(str, Enum):
    """Actionable strategy to heal from a recoverable error."""
    RE_RESOLVE_TARGET = "RE_RESOLVE_TARGET"
    RE_FOCUS = "RE_FOCUS"
    RE_CONNECT = "RE_CONNECT"
    RETRY_WITH_BACKOFF = "RETRY_WITH_BACKOFF"
    FALLBACK_DIRECT_DOWNLOAD = "FALLBACK_DIRECT_DOWNLOAD"
    RETRY_AFTER_UAC = "RETRY_AFTER_UAC"
    ABORT = "ABORT"


@dataclass
class RecoveryPlan:
    """Bounded plan of action to recover from a specific failure."""
    strategy: RecoveryStrategy
    reason: str
    target_app: Optional[str] = None
    target_hwnd: Optional[int] = None
    backoff_seconds: float = 0.5
    remaining_attempts: int = MAX_RECOVERY_ATTEMPTS


class RecoveryManager:
    """Manages failure classification and bounded self-healing attempts."""

    def __init__(self, max_attempts: int = MAX_RECOVERY_ATTEMPTS) -> None:
        self.max_attempts = max_attempts
        self._recovery_counts: Dict[str, int] = {}

    def classify_error(self, error_message: str, error_code: Optional[str] = None) -> ErrorClass:
        """Deterministically classify an error as RECOVERABLE or NON_RECOVERABLE."""
        clean_msg = (error_message or "").lower()
        clean_code = (error_code or "").lower()

        # Non-recoverable conditions: permissions, security boundaries, uninstalled apps, auth failures
        non_recoverable_indicators = [
            "permission denied",
            "requires explicit user confirmation",
            "not installed or could not be found",
            "arbitrary path execution is prohibited",
            "elevation_required",
            "secure_desktop_unavailable",
            "target_access_denied",
            "auth_failed",
            "unsupported operation",
            "prohibited by current policy",
        ]
        for ind in non_recoverable_indicators:
            if ind in clean_msg or ind in clean_code:
                return ErrorClass.NON_RECOVERABLE

        # Recoverable conditions: window lost, focus lost, temporary connection timeout, installer method fallback
        recoverable_indicators = [
            "target window mismatch",
            "is not the foreground window",
            "foreground verification failed",
            "window no longer exists",
            "connection timeout",
            "timed out waiting for",
            "window focus could not be verified",
            "package installation failed",
            "is not running or responsive",
            "download incomplete",
        ]
        for ind in recoverable_indicators:
            if ind in clean_msg or ind in clean_code:
                return ErrorClass.RECOVERABLE

        # Default to non-recoverable if unknown to avoid infinite blind retry loops
        return ErrorClass.NON_RECOVERABLE

    def plan_recovery(
        self,
        task_id: str,
        tool_name: str,
        error_message: str,
        error_code: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[RecoveryPlan]:
        """Produce a bounded recovery plan if the error is recoverable and attempts remain."""
        err_class = self.classify_error(error_message, error_code)
        if err_class == ErrorClass.NON_RECOVERABLE:
            logger.info("Error in task %s is NON_RECOVERABLE: %s", task_id, error_message)
            return None

        attempts = self._recovery_counts.get(task_id, 0)
        if attempts >= self.max_attempts:
            logger.warning("Task %s exceeded max recovery attempts (%d).", task_id, self.max_attempts)
            return None

        self._recovery_counts[task_id] = attempts + 1
        remaining = self.max_attempts - self._recovery_counts[task_id]

        ctx = context or {}
        clean_msg = (error_message or "").lower()

        if tool_name == "pc.package_install" and "package installation failed" in clean_msg:
            return RecoveryPlan(
                strategy=RecoveryStrategy.FALLBACK_DIRECT_DOWNLOAD,
                reason="Package manager install failed; falling back to direct official installer download and execution.",
                target_app=ctx.get("target_app"),
                remaining_attempts=remaining,
            )
        elif "is not running or responsive" in clean_msg:
            return RecoveryPlan(
                strategy=RecoveryStrategy.RETRY_WITH_BACKOFF,
                reason="Application still initializing; waiting and re-verifying process.",
                backoff_seconds=2.0 * (attempts + 1),
                target_app=ctx.get("target_app"),
                remaining_attempts=remaining,
            )
        elif "is not the foreground window" in clean_msg or "foreground verification failed" in clean_msg:
            return RecoveryPlan(
                strategy=RecoveryStrategy.RE_FOCUS,
                reason="Window lost foreground focus; refocusing target window.",
                target_hwnd=ctx.get("target_hwnd"),
                remaining_attempts=remaining,
            )
        elif "window no longer exists" in clean_msg or "target window mismatch" in clean_msg:
            return RecoveryPlan(
                strategy=RecoveryStrategy.RE_RESOLVE_TARGET,
                reason="Target window closed or changed; re-resolving application window.",
                target_app=ctx.get("current_app"),
                remaining_attempts=remaining,
            )
        elif "connection timeout" in clean_msg or "timed out" in clean_msg:
            return RecoveryPlan(
                strategy=RecoveryStrategy.RETRY_WITH_BACKOFF,
                reason="Temporary timeout; retrying with backoff.",
                backoff_seconds=1.0 * (attempts + 1),
                remaining_attempts=remaining,
            )

        return RecoveryPlan(
            strategy=RecoveryStrategy.RETRY_WITH_BACKOFF,
            reason=f"Attempting retry for recoverable error: {error_message}",
            remaining_attempts=remaining,
        )

    def reset_task(self, task_id: str) -> None:
        self._recovery_counts.pop(task_id, None)
