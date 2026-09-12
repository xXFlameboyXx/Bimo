"""Windows PC Agent package for Bimo (Phase 7 & Phase 8)."""

from windows_agent.commands import build_default_command_registry
from windows_agent.controllers import (
    AppLauncher,
    KeyboardController,
    MockAppLauncher,
    MockKeyboardController,
    MockMouseController,
    MockScreenController,
    MockWindowController,
    MouseController,
    ScreenController,
    WindowController,
    WindowsAppLauncher,
    WindowsKeyboardController,
    WindowsMouseController,
    WindowsScreenController,
    WindowsWindowController,
)
from windows_agent.registry import PCCommandRegistry, PCCommandSpec
from windows_agent.server import PCAgentServerManager

__all__ = [
    "PCAgentServerManager",
    "PCCommandRegistry",
    "PCCommandSpec",
    "build_default_command_registry",
    "ScreenController",
    "WindowController",
    "AppLauncher",
    "KeyboardController",
    "MouseController",
    "WindowsScreenController",
    "WindowsWindowController",
    "WindowsAppLauncher",
    "WindowsKeyboardController",
    "WindowsMouseController",
    "MockScreenController",
    "MockWindowController",
    "MockAppLauncher",
    "MockKeyboardController",
    "MockMouseController",
]
