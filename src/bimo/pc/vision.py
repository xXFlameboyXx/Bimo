"""Vision and DesktopCapture abstractions for Phase 8 Controlled Computer Use.

Provides structured Screenshot representations and image capture interfaces,
decoupled from specific LLM vision APIs or provider SDKs.
"""

from __future__ import annotations

import abc
import base64
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Screenshot:
    """Immutable representation of a captured desktop screenshot."""

    width: int
    height: int
    format: str  # e.g. "png", "jpeg"
    data: str  # Base64-encoded image string
    timestamp: float = 0.0

    @property
    def byte_size(self) -> int:
        """Estimate payload size in bytes."""
        return len(self.data)

    def get_bytes(self) -> bytes:
        """Decode base64 payload to raw image bytes."""
        return base64.b64decode(self.data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "image": self.data,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Screenshot:
        return cls(
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            format=str(data.get("format", "png")),
            data=str(data.get("image", "")),
            timestamp=float(data.get("timestamp", 0.0)),
        )


class DesktopCapture(abc.ABC):
    """Abstract interface for capturing primary desktop display images."""

    @abc.abstractmethod
    def capture(
        self,
        max_width: int = 1920,
        max_height: int = 1080,
        max_bytes: int = 1_500_000,
    ) -> Screenshot:
        """Capture the primary display into a bounded Screenshot."""
        pass


class ScreenControllerDesktopCapture(DesktopCapture):
    """Adapter implementing DesktopCapture backed by a ScreenController or client."""

    def __init__(self, screen_controller: Any) -> None:
        self.screen_controller = screen_controller

    def capture(
        self,
        max_width: int = 1920,
        max_height: int = 1080,
        max_bytes: int = 1_500_000,
    ) -> Screenshot:
        res = self.screen_controller.capture_screenshot(
            max_width=max_width,
            max_height=max_height,
            max_bytes=max_bytes,
        )
        return Screenshot.from_dict(res)
