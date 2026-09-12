"""Bimo Tool adapters for the Windows PC Agent.

Integrates with Phase 6 ToolRegistry, BaseTool, and ToolPermission architecture,
respecting SAFE and CONFIRM permission levels.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bimo.pc.client import PCAgentClient
from bimo.pc.mock_client import MockPCAgentClient
from bimo.pc.models import PCErrorCode, PCResponse
from bimo.tools.base import BaseTool, ToolParameter, ToolResult
from bimo.tools.permissions import ToolPermission
from bimo.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _format_pc_result(response: PCResponse, command_name: str) -> ToolResult:
    """Helper to convert a PCResponse into a Phase 6 ToolResult."""
    if response.success:
        out = response.output or {}
        # Human friendly text for LLM/speech
        if command_name == "pc.get_status":
            summary = f"PC is online. Hostname: {out.get('hostname')}, OS: {out.get('os')}."
        elif command_name == "pc.get_active_window":
            summary = f"Active window: '{out.get('title')}' ({out.get('process_name')})."
        elif command_name == "pc.open_app":
            summary = f"Successfully launched {out.get('app')} on PC."
        elif command_name == "pc.close_app":
            summary = f"Closed {out.get('app')} on PC."
        elif command_name == "pc.type_text":
            summary = f"Typed {out.get('length')} characters on PC."
        elif command_name == "pc.press_key":
            summary = f"Pressed key '{out.get('key')}' on PC."
        elif command_name == "pc.click":
            summary = f"Clicked mouse ({out.get('button')}) on PC."
        else:
            summary = f"Executed {command_name} successfully."

        return ToolResult(
            tool_name=command_name,
            success=True,
            output=summary,
            error=None,
            metadata=out,
        )
    else:
        err = response.error
        code = err.code if err else PCErrorCode.COMMAND_FAILED.value
        msg = err.message if err else "PC command failed"

        # Safe human-friendly offline message for Bimo speech
        if code in (PCErrorCode.UNAVAILABLE.value, PCErrorCode.TIMEOUT.value):
            friendly = "Your laptop is unavailable right now."
        elif code == PCErrorCode.AUTH_FAILED.value:
            friendly = "Cannot connect to your laptop due to an authentication error."
        elif code == PCErrorCode.APP_NOT_ALLOWED.value:
            friendly = f"That application is not allowed: {msg}"
        elif code == PCErrorCode.KEY_NOT_ALLOWED.value:
            friendly = f"That key combination is not permitted: {msg}"
        else:
            friendly = f"Laptop command failed: {msg}"

        return ToolResult(
            tool_name=command_name,
            success=False,
            output=friendly,
            error=f"[{code}] {msg}",
            metadata={"code": code},
        )


class PCGetStatusTool(BaseTool):
    """SAFE tool to query Windows PC Agent status."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.get_status",
            description="Get the status, hostname, OS, and availability of the connected Windows PC.",
            parameters=[],
            permission=ToolPermission.SAFE,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        resp = self.client.execute("pc.get_status", {})
        return _format_pc_result(resp, self.name)


class PCGetActiveWindowTool(BaseTool):
    """SAFE tool to get the title and process name of the active foreground window on the PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.get_active_window",
            description="Get the active foreground window title and process name on the Windows PC.",
            parameters=[],
            permission=ToolPermission.SAFE,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        resp = self.client.execute("pc.get_active_window", {})
        return _format_pc_result(resp, self.name)


class PCOpenAppTool(BaseTool):
    """CONFIRM tool to launch an allowlisted application on the Windows PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.open_app",
            description="Open an allowlisted application (e.g. notepad, calculator, explorer) on the Windows PC. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="app",
                    type="string",
                    description="Name of the allowlisted application to open (notepad, calculator, explorer)",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        app_name = kwargs.get("app")
        if not app_name or not isinstance(app_name, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'app' is required and must be a string",
                output="Please specify which application to open.",
            )
        resp = self.client.execute("pc.open_app", {"app": app_name.strip()})
        return _format_pc_result(resp, self.name)


class PCCloseAppTool(BaseTool):
    """CONFIRM tool to close an allowlisted application on the Windows PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.close_app",
            description="Close an allowlisted application (e.g. notepad, calculator, explorer) on the Windows PC. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="app",
                    type="string",
                    description="Name of the allowlisted application to close",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        app_name = kwargs.get("app")
        if not app_name or not isinstance(app_name, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'app' is required and must be a string",
                output="Please specify which application to close.",
            )
        resp = self.client.execute("pc.close_app", {"app": app_name.strip()})
        return _format_pc_result(resp, self.name)


class PCTypeTextTool(BaseTool):
    """CONFIRM tool to type text into the currently focused window on the PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.type_text",
            description="Type text into the active focused window on the Windows PC. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="text",
                    type="string",
                    description="The exact text string to type into the focused window",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        text = kwargs.get("text")
        if text is None or not isinstance(text, str) or len(text) == 0:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'text' must be a non-empty string",
                output="Cannot type empty text.",
            )
        resp = self.client.execute("pc.type_text", {"text": text})
        return _format_pc_result(resp, self.name)


class PCPressKeyTool(BaseTool):
    """CONFIRM tool to press an allowlisted key or shortcut on the PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.press_key",
            description="Press an allowlisted key or shortcut (e.g. ENTER, ESC, TAB, CTRL+C, CTRL+V) on the Windows PC. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="key",
                    type="string",
                    description="Allowed key name: ENTER, ESC, TAB, CTRL+C, CTRL+V, ALT+TAB",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        key = kwargs.get("key")
        if not key or not isinstance(key, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'key' must be a non-empty string",
                output="Please specify a valid key name.",
            )
        resp = self.client.execute("pc.press_key", {"key": key.strip().upper()})
        return _format_pc_result(resp, self.name)


class PCClickTool(BaseTool):
    """CONFIRM tool to click the mouse on the Windows PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.click",
            description="Perform a mouse click (left or right) at optional coordinates on the Windows PC. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="button",
                    type="string",
                    description="Mouse button to click: 'left' or 'right' (default 'left')",
                    required=False,
                    enum_values=["left", "right"],
                ),
                ToolParameter(
                    name="clicks",
                    type="integer",
                    description="Number of clicks: 1 or 2 (default 1)",
                    required=False,
                ),
                ToolParameter(
                    name="x",
                    type="integer",
                    description="Optional X coordinate on screen to click",
                    required=False,
                ),
                ToolParameter(
                    name="y",
                    type="integer",
                    description="Optional Y coordinate on screen to click",
                    required=False,
                ),
            ],
            permission=ToolPermission.CONFIRM,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        payload: Dict[str, Any] = {
            "button": kwargs.get("button", "left"),
            "clicks": kwargs.get("clicks", 1),
        }
        if "x" in kwargs and kwargs["x"] is not None:
            payload["x"] = kwargs["x"]
        if "y" in kwargs and kwargs["y"] is not None:
            payload["y"] = kwargs["y"]
        resp = self.client.execute("pc.click", payload)
        return _format_pc_result(resp, self.name)


class PCScreenshotTool(BaseTool):
    """SAFE tool to capture a desktop screenshot from the Windows PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.screenshot",
            description="Capture the primary desktop screenshot from the Windows PC.",
            parameters=[],
            permission=ToolPermission.SAFE,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        resp = self.client.execute("pc.screenshot", {})
        if resp.success:
            out = resp.output or {}
            w = out.get("width", 0)
            h = out.get("height", 0)
            fmt = out.get("format", "png")
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=f"Captured desktop screenshot ({w}x{h}, {fmt}).",
                metadata=out,
            )
        return _format_pc_result(resp, self.name)


class PCGetScreenSizeTool(BaseTool):
    """SAFE tool to query the primary monitor screen dimensions."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.get_screen_size",
            description="Get the primary monitor screen resolution (width and height) on the Windows PC.",
            parameters=[],
            permission=ToolPermission.SAFE,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        resp = self.client.execute("pc.get_screen_size", {})
        if resp.success:
            out = resp.output or {}
            w = out.get("width", 0)
            h = out.get("height", 0)
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=f"Screen size is {w}x{h}.",
                metadata=out,
            )
        return _format_pc_result(resp, self.name)


class PCFocusWindowTool(BaseTool):
    """CONFIRM tool to focus a visible window by title."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.focus_window",
            description="Bring a visible application window matching the given title to the foreground. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="title",
                    type="string",
                    description="Title of the window to focus (e.g. 'Notepad', 'Calculator')",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        title = kwargs.get("title")
        if not title or not isinstance(title, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'title' is required and must be a string",
                output="Please specify the title of the window to focus.",
            )
        resp = self.client.execute("pc.focus_window", {"title": title.strip()})
        if resp.success:
            out = resp.output or {}
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=f"Focused window '{out.get('title')}'.",
                metadata=out,
            )
        return _format_pc_result(resp, self.name)


class PCMoveMouseTool(BaseTool):
    """CONFIRM tool to move the mouse cursor to specific coordinates."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.move_mouse",
            description="Move the mouse cursor to the given (x, y) coordinates on the Windows PC. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="x",
                    type="integer",
                    description="Target X coordinate on screen",
                    required=True,
                ),
                ToolParameter(
                    name="y",
                    type="integer",
                    description="Target Y coordinate on screen",
                    required=True,
                ),
            ],
            permission=ToolPermission.CONFIRM,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        x = kwargs.get("x")
        y = kwargs.get("y")
        if x is None or y is None:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Both 'x' and 'y' integer coordinates are required",
                output="Please specify x and y coordinates.",
            )
        resp = self.client.execute("pc.move_mouse", {"x": x, "y": y})
        if resp.success:
            out = resp.output or {}
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=f"Moved mouse to ({out.get('x')}, {out.get('y')}).",
                metadata=out,
            )
        return _format_pc_result(resp, self.name)


class PCScrollTool(BaseTool):
    """CONFIRM tool to scroll the mouse wheel."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.scroll",
            description="Scroll the mouse wheel up (positive) or down (negative) by the given amount [-1000..1000]. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="amount",
                    type="integer",
                    description="Scroll amount between -1000 and 1000",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        amount = kwargs.get("amount")
        if amount is None:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'amount' is required",
                output="Please specify a scroll amount.",
            )
        resp = self.client.execute("pc.scroll", {"amount": amount})
        if resp.success:
            out = resp.output or {}
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=f"Scrolled mouse wheel ({out.get('amount')}).",
                metadata=out,
            )
        return _format_pc_result(resp, self.name)


def register_pc_tools(registry: ToolRegistry, client: PCAgentClient | MockPCAgentClient) -> None:
    """Register all Phase 7 & 8 PC tools into the Bimo ToolRegistry."""
    registry.register(PCGetStatusTool(client))
    registry.register(PCGetActiveWindowTool(client))
    registry.register(PCGetScreenSizeTool(client))
    registry.register(PCScreenshotTool(client))
    registry.register(PCFocusWindowTool(client))
    registry.register(PCOpenAppTool(client))
    registry.register(PCCloseAppTool(client))
    registry.register(PCTypeTextTool(client))
    registry.register(PCPressKeyTool(client))
    registry.register(PCMoveMouseTool(client))
    registry.register(PCClickTool(client))
    registry.register(PCScrollTool(client))
