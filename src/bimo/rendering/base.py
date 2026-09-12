"""Abstract face renderer interface.

Decouples the robot state machine and business logic from the visual display.
Any concrete display—whether a Windows desktop simulator, a Pygame window,
or a physical 3.5\" SPI LCD (ST7789/ILI9486) on the Raspberry Pi—must implement
this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from bimo.core.state import RobotState


class BaseFaceRenderer(ABC):
    """Abstract contract for rendering Bimo's facial expressions and animations."""

    def __init__(self, width: int = 480, height: int = 320) -> None:
        self.width = width
        self.height = height
        self._current_state = RobotState.IDLE
        self._status_text: str = ""
        self._is_running = False

    @property
    def current_state(self) -> RobotState:
        """Return the state currently being visualized."""
        return self._current_state

    @property
    def is_running(self) -> bool:
        """Check if the renderer is initialized and active."""
        return self._is_running

    @abstractmethod
    def initialize(self) -> None:
        """Initialize display context, canvas, framebuffer, or hardware bus."""

    @abstractmethod
    def set_state(self, state: RobotState) -> None:
        """Update the active expression/animation target according to robot state."""

    @abstractmethod
    def render_frame(self) -> None:
        """Render a single animation frame corresponding to the current state."""

    @abstractmethod
    def display_status(self, text: str) -> None:
        """Display an overlay or debug status message on the face display."""

    @abstractmethod
    def close(self) -> None:
        """Clean up display buffers, windows, or hardware connections."""
