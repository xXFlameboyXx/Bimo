"""Safe PC Command implementations for Phase 7 and Phase 8 (Controlled Computer Use).

Implements strictly bounded, allowlisted PC commands:
- Observation: pc.get_status, pc.get_active_window, pc.get_screen_size, pc.screenshot
- Window control: pc.focus_window, pc.open_app, pc.close_app
- Mouse: pc.move_mouse, pc.click, pc.scroll
- Keyboard: pc.type_text, pc.press_key

Prohibits any shell execution, PowerShell, eval, or arbitrary system calls.
"""

from __future__ import annotations

import logging
import platform
import socket
import time
from typing import Any, Dict, List, Optional

from windows_agent.controllers import (
    AppLauncher,
    KeyboardController,
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

logger = logging.getLogger(__name__)

AGENT_VERSION = "1.0.0"
_START_TIME = time.time()
MAX_TYPE_TEXT_LENGTH = 1000


def build_default_command_registry(
    screen_ctrl: Optional[ScreenController] = None,
    window_ctrl: Optional[WindowController] = None,
    app_launcher: Optional[AppLauncher] = None,
    kbd_ctrl: Optional[KeyboardController] = None,
    mouse_ctrl: Optional[MouseController] = None,
    allowed_apps: Optional[List[str]] = None,
) -> PCCommandRegistry:
    """Instantiate and populate the PCCommandRegistry with all Phase 7 & 8 safe commands."""
    registry = PCCommandRegistry()

    # Default to platform-appropriate controllers if not supplied
    scr_ctrl = screen_ctrl or WindowsScreenController()
    win_ctrl = window_ctrl or WindowsWindowController()
    launcher = app_launcher or WindowsAppLauncher(allowed_apps=allowed_apps)
    kbd = kbd_ctrl or WindowsKeyboardController()
    mouse = mouse_ctrl or WindowsMouseController(screen_ctrl=scr_ctrl)

    # 1. pc.get_status (SAFE)
    def handle_get_status(args: Dict[str, Any]) -> Dict[str, Any]:
        uptime = int(time.time() - _START_TIME)
        return {
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()}",
            "agent_version": AGENT_VERSION,
            "status": "online",
            "uptime_seconds": uptime,
            "current_time": time.time(),
            "registered_commands": registry.list_commands(),
        }

    registry.register(
        PCCommandSpec(
            name="pc.get_status",
            description="Returns operational status, hostname, OS, and registered commands.",
            handler=handle_get_status,
            required_args=[],
            optional_args=[],
        )
    )

    # 2. pc.get_active_window (SAFE)
    def handle_get_active_window(args: Dict[str, Any]) -> Dict[str, Any]:
        info = win_ctrl.get_active_window()
        return {
            "title": info.get("title", ""),
            "process_name": info.get("process_name", ""),
            "is_foreground": info.get("is_foreground", True),
        }

    registry.register(
        PCCommandSpec(
            name="pc.get_active_window",
            description="Returns the visible title and process name of the active foreground window.",
            handler=handle_get_active_window,
            required_args=[],
            optional_args=[],
        )
    )

    # 3. pc.get_screen_size (SAFE)
    def handle_get_screen_size(args: Dict[str, Any]) -> Dict[str, Any]:
        w, h = scr_ctrl.get_screen_size()
        return {"width": w, "height": h}

    registry.register(
        PCCommandSpec(
            name="pc.get_screen_size",
            description="Returns the pixel dimensions of the primary display (width, height).",
            handler=handle_get_screen_size,
            required_args=[],
            optional_args=[],
        )
    )

    # 4. pc.screenshot (SAFE)
    def handle_screenshot(args: Dict[str, Any]) -> Dict[str, Any]:
        max_w = args.get("max_width", 1920)
        max_h = args.get("max_height", 1080)
        max_b = args.get("max_bytes", 1_500_000)

        if not isinstance(max_w, int) or isinstance(max_w, bool) or max_w <= 0:
            raise ValueError("Argument 'max_width' must be a positive integer.")
        if not isinstance(max_h, int) or isinstance(max_h, bool) or max_h <= 0:
            raise ValueError("Argument 'max_height' must be a positive integer.")
        if not isinstance(max_b, int) or isinstance(max_b, bool) or max_b <= 0:
            raise ValueError("Argument 'max_bytes' must be a positive integer.")

        return scr_ctrl.capture_screenshot(
            max_width=max_w,
            max_height=max_h,
            max_bytes=max_b,
        )

    registry.register(
        PCCommandSpec(
            name="pc.screenshot",
            description="Captures the primary desktop screen as a bounded, optimized base64 image.",
            handler=handle_screenshot,
            required_args=[],
            optional_args=["max_width", "max_height", "max_bytes"],
        )
    )

    # 5. pc.focus_window (CONFIRM)
    def handle_focus_window(args: Dict[str, Any]) -> Dict[str, Any]:
        title = args.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Argument 'title' must be a non-empty string.")
        return win_ctrl.focus_window(title.strip())

    registry.register(
        PCCommandSpec(
            name="pc.focus_window",
            description="Finds and brings a visible window matching title to the foreground.",
            handler=handle_focus_window,
            required_args=["title"],
            optional_args=[],
        )
    )

    # 6. pc.open_app (CONFIRM)
    def handle_open_app(args: Dict[str, Any]) -> Dict[str, Any]:
        app_name = args.get("app")
        if not isinstance(app_name, str) or not app_name.strip():
            raise ValueError("Argument 'app' must be a non-empty string.")
        return launcher.launch(app_name.strip())

    registry.register(
        PCCommandSpec(
            name="pc.open_app",
            description="Launch an allowlisted application by friendly name.",
            handler=handle_open_app,
            required_args=["app"],
            optional_args=[],
        )
    )

    # 7. pc.close_app (CONFIRM)
    def handle_close_app(args: Dict[str, Any]) -> Dict[str, Any]:
        app_name = args.get("app")
        if not isinstance(app_name, str) or not app_name.strip():
            raise ValueError("Argument 'app' must be a non-empty string.")
        success = launcher.terminate(app_name.strip())
        return {"app": app_name.strip(), "status": "closed" if success else "close_failed"}

    registry.register(
        PCCommandSpec(
            name="pc.close_app",
            description="Close an allowlisted application by friendly name.",
            handler=handle_close_app,
            required_args=["app"],
            optional_args=[],
        )
    )

    # 8. pc.type_text (CONFIRM)
    def handle_type_text(args: Dict[str, Any]) -> Dict[str, Any]:
        text = args.get("text")
        if not isinstance(text, str) or len(text) == 0:
            raise ValueError("Argument 'text' must be a non-empty string.")
        if len(text) > MAX_TYPE_TEXT_LENGTH:
            raise ValueError(f"Argument 'text' exceeds maximum allowed length of {MAX_TYPE_TEXT_LENGTH} characters.")
        typed_count = kbd.type_text(text)
        return {"status": "typed", "length": typed_count}

    registry.register(
        PCCommandSpec(
            name="pc.type_text",
            description="Type text directly into the focused window.",
            handler=handle_type_text,
            required_args=["text"],
            optional_args=[],
        )
    )

    # 9. pc.press_key (CONFIRM)
    def handle_press_key(args: Dict[str, Any]) -> Dict[str, Any]:
        key = args.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Argument 'key' must be a non-empty string.")
        normalized = key.strip().upper()
        kbd.press_key(normalized)
        return {"key": normalized, "status": "pressed"}

    registry.register(
        PCCommandSpec(
            name="pc.press_key",
            description="Press an allowlisted key or shortcut combination.",
            handler=handle_press_key,
            required_args=["key"],
            optional_args=[],
        )
    )

    # 10. pc.move_mouse (CONFIRM)
    def handle_move_mouse(args: Dict[str, Any]) -> Dict[str, Any]:
        x = args.get("x")
        y = args.get("y")
        if not isinstance(x, int) or isinstance(x, bool):
            raise ValueError("Argument 'x' must be an integer.")
        if not isinstance(y, int) or isinstance(y, bool):
            raise ValueError("Argument 'y' must be an integer.")
        mouse.move_mouse(x, y)
        return {"x": x, "y": y, "status": "moved"}

    registry.register(
        PCCommandSpec(
            name="pc.move_mouse",
            description="Move mouse cursor to coordinates within primary screen bounds.",
            handler=handle_move_mouse,
            required_args=["x", "y"],
            optional_args=[],
        )
    )

    # 11. pc.click (CONFIRM)
    def handle_click(args: Dict[str, Any]) -> Dict[str, Any]:
        button = args.get("button", "left")
        clicks = args.get("clicks", 1)
        x = args.get("x")
        y = args.get("y")

        if not isinstance(button, str) or button.lower() not in ("left", "right"):
            raise ValueError("Argument 'button' must be either 'left' or 'right'.")
        if not isinstance(clicks, int) or clicks not in (1, 2):
            raise ValueError("Argument 'clicks' must be 1 or 2.")
        if x is not None and (not isinstance(x, int) or isinstance(x, bool)):
            raise ValueError("Argument 'x' must be an integer.")
        if y is not None and (not isinstance(y, int) or isinstance(y, bool)):
            raise ValueError("Argument 'y' must be an integer.")

        mouse.click(button=button.lower(), clicks=clicks, x=x, y=y)
        res = {"button": button.lower(), "clicks": clicks, "status": "clicked"}
        if x is not None and y is not None:
            res["x"] = x
            res["y"] = y
        return res

    registry.register(
        PCCommandSpec(
            name="pc.click",
            description="Click mouse button at current position or specified (x, y) coordinates.",
            handler=handle_click,
            required_args=[],
            optional_args=["button", "clicks", "x", "y"],
        )
    )

    # 12. pc.scroll (CONFIRM)
    def handle_scroll(args: Dict[str, Any]) -> Dict[str, Any]:
        amount = args.get("amount")
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ValueError("Argument 'amount' must be an integer.")
        if amount < -1000 or amount > 1000:
            raise ValueError(f"Argument 'amount' {amount} out of allowed range (-1000..1000).")
        mouse.scroll(amount)
        return {"amount": amount, "status": "scrolled"}

    registry.register(
        PCCommandSpec(
            name="pc.scroll",
            description="Scroll vertical mouse wheel by amount (-1000..1000). Positive=up, Negative=down.",
            handler=handle_scroll,
            required_args=["amount"],
            optional_args=[],
        )
    )

    return registry
