"""Cooperative Task Cancellation and Pause/Resume subsystem for Bimo.

Provides:
- CancellationToken: Cooperative token passed to tool loops, sub-tasks, and long-running operations.
- TaskInterruptManager: Global interrupt coordination for spoken commands ("stop", "cancel", "pause", "resume").
- Priority interrupt command classification.
"""

from __future__ import annotations

from enum import Enum
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class InterruptCommand(str, Enum):
    """Classification of urgent spoken interruption commands."""
    STOP = "STOP"
    CANCEL = "CANCEL"
    ABORT = "ABORT"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    CONTINUE = "CONTINUE"
    NONE = "NONE"


class CancellationToken:
    """Cooperative cancellation and pause token for task execution."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._paused = threading.Event()
        self._cancel_reason: Optional[str] = None
        self._callbacks: List[Callable[[str], None]] = []
        self._lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    @property
    def cancel_reason(self) -> Optional[str]:
        return self._cancel_reason

    def cancel(self, reason: str = "User requested cancellation") -> None:
        """Signal cooperative cancellation."""
        with self._lock:
            if not self._cancelled.is_set():
                self._cancel_reason = reason
                self._cancelled.set()
                logger.info("CancellationToken triggered: %s", reason)
                for cb in self._callbacks:
                    try:
                        cb(reason)
                    except Exception as err:
                        logger.warning("Error in cancellation callback: %s", err)

    def pause(self) -> None:
        """Signal pause."""
        self._paused.set()
        logger.info("CancellationToken paused.")

    def resume(self) -> None:
        """Signal resume."""
        self._paused.clear()
        logger.info("CancellationToken resumed.")

    def register_callback(self, callback: Callable[[str], None]) -> None:
        """Register a callback to be invoked on cancellation."""
        with self._lock:
            if self._cancelled.is_set():
                if self._cancel_reason:
                    callback(self._cancel_reason)
            else:
                self._callbacks.append(callback)

    def check_cancelled(self) -> None:
        """Raise TaskCancelledException if cancellation was requested."""
        if self._cancelled.is_set():
            raise TaskCancelledException(self._cancel_reason or "Task was cancelled")

    def wait_if_paused(self, poll_interval: float = 0.1, timeout: Optional[float] = None) -> bool:
        """Block cooperatively while paused until resumed or cancelled.
        
        Returns True if resumed, False if cancelled or timed out.
        """
        start = time.time()
        while self._paused.is_set():
            if self._cancelled.is_set():
                return False
            if timeout and (time.time() - start) > timeout:
                return False
            time.sleep(poll_interval)
        return True


class TaskCancelledException(Exception):
    """Raised when a task or tool execution is cancelled via CancellationToken."""
    pass

TaskCancelledError = TaskCancelledException


class TaskInterruptManager:
    """Classifies spoken utterances for interruption commands and coordinates cancellation."""

    STOP_WORDS = {
        "stop", "cancel", "abort", "never mind", "nevermind", "quit", "halt",
        "shut up", "quiet", "shh", "rok do", "bas", "band karo",
    }
    PAUSE_WORDS = {
        "pause", "wait", "hold on", "hold up", "hang on", "wait wait",
        "wait a minute", "wait a second", "wait a sec", "just wait",
        "ruko", "ruk jao", "thahar jao", "zara ruko",
    }
    RESUME_WORDS = {
        "continue", "resume", "go ahead", "keep going", "proceed",
        "carry on", "chalu karo", "chalu rakho", "aage badho",
    }

    @classmethod
    def classify(cls, utterance: str) -> InterruptCommand:
        """Classify if an utterance is an urgent interruption command."""
        if not utterance:
            return InterruptCommand.NONE
        clean = utterance.strip().lower().rstrip(".!?,")

        # Strip common wake words and conversational prefixes
        for prefix in ("hey bimo", "ok bimo", "bimo,", "bimo", "please", "arrey bimo"):
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip().lstrip(",").strip()

        # Strip conversational suffixes with proper word boundaries
        for suffix in ("please", "now", "it", "bimo"):
            if clean.endswith(" " + suffix):
                clean = clean[:-len(suffix) - 1].strip()

        # Fast direct match
        if clean in ("cancel", "abort", "never mind", "nevermind"):
            return InterruptCommand.CANCEL
        elif clean in ("stop", "quit", "halt", "shut up", "quiet", "shh", "rok do", "bas", "band karo"):
            return InterruptCommand.STOP
        elif clean in cls.PAUSE_WORDS:
            return InterruptCommand.PAUSE
        elif clean in cls.RESUME_WORDS:
            return InterruptCommand.RESUME

        # Multi-word and phrase-level fuzzy matching
        if any(clean.startswith(w) or clean == w for w in ("stop", "halt", "cancel", "abort", "quit")):
            return InterruptCommand.STOP
        if any(clean.startswith(w) or clean == w for w in ("wait", "pause", "hold on", "hold up", "hang on", "ruko", "ruk jao")):
            return InterruptCommand.PAUSE
        if any(clean.startswith(w) or clean == w for w in ("continue", "resume", "go ahead", "chalu rakho", "aage badho")):
            return InterruptCommand.RESUME

        return InterruptCommand.NONE
