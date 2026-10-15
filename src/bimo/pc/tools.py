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
        elif command_name == "pc.find_app":
            count = out.get("count", len(out.get("matches", [])))
            matches = out.get("matches", [])
            names = [m.get("display_name") for m in matches if isinstance(m, dict)]
            summary = f"Found {count} installed applications: {', '.join(names)}" if names else "No matching applications found."
        elif command_name == "pc.list_apps":
            count = out.get("count", len(out.get("apps", [])))
            summary = f"Listed {count} installed applications."
        elif command_name == "pc.play_youtube":
            title = out.get("title") or out.get("query")
            summary = f"Playing '{title}' on YouTube."
        elif command_name == "pc.close_app":
            summary = f"Closed {out.get('app')} on PC."
        elif command_name == "pc.type_text":
            summary = f"Typed {out.get('length')} characters on PC."
        elif command_name == "pc.press_key":
            summary = f"Pressed key '{out.get('key')}' on PC."
        elif command_name == "pc.open_url":
            summary = f"Opened {out.get('url')} on PC."
        elif command_name == "pc.click":
            summary = f"Clicked mouse ({out.get('button')}) on PC."
        elif command_name == "pc.system_info":
            summary = f"PC System: {out.get('os')} ({out.get('architecture')}), Free Storage: {out.get('free_storage_gb')} GB / {out.get('total_storage_gb')} GB, Winget: {'Available' if out.get('winget_available') else 'Not Found'}."
        elif command_name == "pc.package_search":
            pkgs = out.get('packages', [])
            count = out.get('count', len(pkgs))
            names = [p.get('id', p.get('name', '')) for p in pkgs[:5]]
            summary = f"Found {count} matching packages: {', '.join(names)}." if names else "No matching packages found."
        elif command_name == "pc.package_show":
            pkg = out.get('package', {})
            summary = f"Package {pkg.get('name', '')} ({pkg.get('id', '')} v{pkg.get('version', '')}): Publisher: {pkg.get('publisher')}, License: {pkg.get('license')}, Homepage: {pkg.get('homepage')}."
        elif command_name == "pc.package_install":
            summary = f"Successfully installed package {out.get('package_id')}." if out.get('success') else f"Package installation failed: {out.get('error')}."
        elif command_name == "pc.download_installer":
            summary = f"Downloaded installer to {out.get('file_path')} ({out.get('file_size_mb')} MB, SHA256: {str(out.get('sha256', ''))[:12]}...)." if out.get('success') else f"Download failed: {out.get('error')}."
        elif command_name == "pc.verify_installer":
            summary = f"Installer verification: Valid={out.get('valid')}, SHA256 Match={out.get('sha256_matches')}, Signature={out.get('signature_status')} ({out.get('signer')})."
        elif command_name in ("pc.run_installer", "pc.execute_installer"):
            summary = f"Installer executed with exit code {out.get('return_code')} (Elevation required: {out.get('elevation_required')})."
        elif command_name == "pc.verify_app_running":
            summary = f"Application '{out.get('app_name')}' running check: {out.get('status')} (PIDs: {out.get('running_pids')}, Windows: {out.get('window_titles')})."
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
            description="Open an allowlisted application (e.g. notepad, calculator, explorer, youtube) on the Windows PC. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="app",
                    type="string",
                    description="Name of the allowlisted application to open (notepad, calculator, explorer, youtube)",
                    required=True,
                ),
                ToolParameter(
                    name="query",
                    type="string",
                    description="Optional search query or video to play on YouTube (e.g. 'psi song')",
                    required=False,
                ),
                ToolParameter(
                    name="url",
                    type="string",
                    description="Optional direct URL to open in YouTube or web application",
                    required=False,
                ),
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        app_name = kwargs.get("app") or kwargs.get("app_name") or kwargs.get("name") or kwargs.get("application")
        if not app_name or not isinstance(app_name, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'app' is required and must be a string",
                output="Please specify which application to open.",
            )
        payload: Dict[str, Any] = {"app": app_name.strip()}
        if kwargs.get("query"):
            payload["query"] = str(kwargs["query"]).strip()
        if kwargs.get("url"):
            payload["url"] = str(kwargs["url"]).strip()
        resp = self.client.execute("pc.open_app", payload)
        return _format_pc_result(resp, self.name)


class PCCloseAppTool(BaseTool):
    """CONFIRM tool to close an allowlisted application on the Windows PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.close_app",
            description="Close an allowlisted application (e.g. notepad, calculator, explorer, youtube) on the Windows PC. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="app",
                    type="string",
                    description="Name of the allowlisted application to close (notepad, calculator, explorer, youtube)",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        app_name = kwargs.get("app") or kwargs.get("app_name") or kwargs.get("name") or kwargs.get("application")
        if not app_name or not isinstance(app_name, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'app' is required and must be a string",
                output="Please specify which application to close.",
            )
        resp = self.client.execute("pc.close_app", {"app": app_name.strip()})
        return _format_pc_result(resp, self.name)


class PCGetTargetWindowTool(BaseTool):
    """SAFE tool to query target window information on the PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.get_target_window",
            description="Query the target window on the PC by app name, title, or HWND.",
            parameters=[
                ToolParameter(
                    name="app",
                    type="string",
                    description="Optional friendly name of the application (e.g. 'notepad')",
                    required=False,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Optional title string to match",
                    required=False,
                ),
                ToolParameter(
                    name="hwnd",
                    type="integer",
                    description="Optional window HWND integer",
                    required=False,
                ),
            ],
            permission=ToolPermission.SAFE,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        payload: Dict[str, Any] = {}
        for k in ("app", "title", "hwnd", "pid"):
            if k in kwargs and kwargs[k] is not None:
                payload[k] = kwargs[k]
        resp = self.client.execute("pc.get_target_window", payload)
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
                ),
                ToolParameter(
                    name="app",
                    type="string",
                    description="Optional target application name (e.g. 'notepad')",
                    required=False,
                ),
                ToolParameter(
                    name="hwnd",
                    type="integer",
                    description="Optional target window HWND",
                    required=False,
                ),
                ToolParameter(
                    name="restore_previous",
                    type="boolean",
                    description="Whether to restore user previous foreground window after typing",
                    required=False,
                ),
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        text = kwargs.get("text") or kwargs.get("content") or kwargs.get("message")
        if text is None or not isinstance(text, str) or len(text) == 0:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'text' must be a non-empty string",
                output="Cannot type empty text.",
            )
        payload: Dict[str, Any] = {"text": text}
        for opt in ("target", "app", "hwnd", "restore_previous", "strategy"):
            if opt in kwargs and kwargs[opt] is not None:
                payload[opt] = kwargs[opt]
        resp = self.client.execute("pc.type_text", payload)
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
                ),
                ToolParameter(
                    name="app",
                    type="string",
                    description="Optional target application name",
                    required=False,
                ),
                ToolParameter(
                    name="hwnd",
                    type="integer",
                    description="Optional target window HWND",
                    required=False,
                ),
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        key = kwargs.get("key") or kwargs.get("keys") or kwargs.get("key_combination")
        if not key or not isinstance(key, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'key' must be a non-empty string",
                output="Please specify a valid key name.",
            )
        payload: Dict[str, Any] = {"key": key.strip().upper()}
        for opt in ("target", "app", "hwnd"):
            if opt in kwargs and kwargs[opt] is not None:
                payload[opt] = kwargs[opt]
        resp = self.client.execute("pc.press_key", payload)
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
                    description="Optional X coordinate on screen or window to click",
                    required=False,
                ),
                ToolParameter(
                    name="y",
                    type="integer",
                    description="Optional Y coordinate on screen or window to click",
                    required=False,
                ),
                ToolParameter(
                    name="relative",
                    type="boolean",
                    description="Whether coordinates are relative to target window client area",
                    required=False,
                ),
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        payload: Dict[str, Any] = {
            "button": kwargs.get("button", "left"),
            "clicks": kwargs.get("clicks", 1),
        }
        for opt in ("x", "y", "target", "app", "hwnd", "relative", "rel_x", "rel_y"):
            if opt in kwargs and kwargs[opt] is not None:
                payload[opt] = kwargs[opt]
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


class PCOpenUrlTool(BaseTool):
    """CONFIRM tool to open a validated HTTP/HTTPS URL in the default web browser on the Windows PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.open_url",
            description="Open a validated web URL (http/https) in the default web browser on the Windows PC. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="The HTTP or HTTPS URL to open (e.g. 'https://www.youtube.com')",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        url = kwargs.get("url") or kwargs.get("link") or kwargs.get("webpage")
        if not url or not isinstance(url, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'url' is required and must be a string",
                output="Please specify a URL to open.",
            )
        clean_url = url.strip()
        if not clean_url.startswith(("http://", "https://")):
            clean_url = f"https://{clean_url}"
        resp = self.client.execute("pc.open_url", {"url": clean_url})
        return _format_pc_result(resp, self.name)


class PCPlayYouTubeTool(BaseTool):
    """CONFIRM tool to search and play the most relevant video on YouTube in the desktop app."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.play_youtube",
            description="Search for a song or video and play the most relevant result directly in YouTube desktop app. Requires confirmation.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="The song, video title, or search term to play (e.g. 'psi song', 'despacito')",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query") or kwargs.get("song") or kwargs.get("video") or kwargs.get("search")
        if not query or not isinstance(query, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'query' is required and must be a string",
                output="Please specify what you would like to play.",
            )
        resp = self.client.execute("pc.play_youtube", {"query": query.strip()})
        return _format_pc_result(resp, self.name)


class PCFindAppTool(BaseTool):
    """SAFE tool to search installed applications on the Windows PC by friendly name."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.find_app",
            description="Search installed applications on the Windows PC by friendly name without launching. Returns safe application metadata.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="The application name or keyword to search for (e.g. 'discord', 'chrome', 'code')",
                    required=True,
                )
            ],
            permission=ToolPermission.SAFE,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query") or kwargs.get("app") or kwargs.get("name")
        if not query or not isinstance(query, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'query' is required and must be a string",
                output="Please specify an application name to search for.",
            )
        resp = self.client.execute("pc.find_app", {"query": query.strip()})
        return _format_pc_result(resp, self.name)


class PCListAppsTool(BaseTool):
    """SAFE tool to list installed applications on the Windows PC."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.list_apps",
            description="List installed applications on the Windows PC with safe metadata. Does not launch applications.",
            parameters=[
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum number of applications to return (default 50)",
                    required=False,
                ),
                ToolParameter(
                    name="query",
                    type="string",
                    description="Optional filter query to narrow down application list",
                    required=False,
                ),
            ],
            permission=ToolPermission.SAFE,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        payload: Dict[str, Any] = {}
        if "limit" in kwargs and kwargs["limit"] is not None:
            try:
                payload["limit"] = int(kwargs["limit"])
            except Exception:
                pass
        if "query" in kwargs and kwargs["query"]:
            payload["query"] = str(kwargs["query"]).strip()
        resp = self.client.execute("pc.list_apps", payload)
        return _format_pc_result(resp, self.name)


class PCSystemInfoTool(BaseTool):
    """SAFE tool to query PC system architecture, storage, and capabilities."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.system_info",
            description="Query the Windows PC operating system, CPU architecture, available disk storage, and winget availability.",
            parameters=[],
            permission=ToolPermission.SAFE,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        resp = self.client.execute("pc.system_info", {})
        return _format_pc_result(resp, self.name)


class PCPackageSearchTool(BaseTool):
    """SAFE tool to search package manager repositories (winget) for software."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.package_search",
            description="Search Windows Package Manager (winget) repositories for software packages matching a query.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Software application or keyword to search for (e.g. 'KiCad', 'Blender')",
                    required=True,
                ),
                ToolParameter(
                    name="source",
                    type="string",
                    description="Repository source (default: 'winget')",
                    required=False,
                ),
            ],
            permission=ToolPermission.SAFE,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query") or kwargs.get("app") or kwargs.get("name")
        if not query or not isinstance(query, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'query' is required and must be a string.",
                output="Please specify a software name or keyword to search.",
            )
        payload = {"query": query.strip(), "source": str(kwargs.get("source", "winget")).strip()}
        resp = self.client.execute("pc.package_search", payload)
        return _format_pc_result(resp, self.name)


class PCPackageShowTool(BaseTool):
    """SAFE tool to inspect package metadata, publisher, homepage, and license."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.package_show",
            description="Query detailed package information, publisher, homepage, license, and installer URL for a package ID.",
            parameters=[
                ToolParameter(
                    name="package_id",
                    type="string",
                    description="Exact package ID from package search (e.g. 'KiCad.KiCad')",
                    required=True,
                ),
                ToolParameter(
                    name="source",
                    type="string",
                    description="Repository source (default: 'winget')",
                    required=False,
                ),
            ],
            permission=ToolPermission.SAFE,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        package_id = kwargs.get("package_id") or kwargs.get("id")
        if not package_id or not isinstance(package_id, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'package_id' is required.",
                output="Please specify a valid package ID.",
            )
        payload = {"package_id": package_id.strip(), "source": str(kwargs.get("source", "winget")).strip()}
        resp = self.client.execute("pc.package_show", payload)
        return _format_pc_result(resp, self.name)


class PCPackageInstallTool(BaseTool):
    """CONFIRM tool to install a verified software package unattended via winget."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.package_install",
            description="Install an authorized software package by package ID using Windows Package Manager unattended.",
            parameters=[
                ToolParameter(
                    name="package_id",
                    type="string",
                    description="Package ID to install (e.g. 'KiCad.KiCad')",
                    required=True,
                ),
                ToolParameter(
                    name="silent",
                    type="boolean",
                    description="Whether to run silent unattended installation (default: True)",
                    required=False,
                ),
                ToolParameter(
                    name="timeout",
                    type="integer",
                    description="Maximum timeout in seconds for installation (default: 600)",
                    required=False,
                ),
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client
        self.timeout = 1800.0

    def execute(self, **kwargs: Any) -> ToolResult:
        package_id = kwargs.get("package_id") or kwargs.get("id")
        if not package_id or not isinstance(package_id, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'package_id' is required.",
                output="Please specify a valid package ID to install.",
            )
        timeout_sec = int(kwargs.get("timeout", 1800))
        payload: Dict[str, Any] = {
            "package_id": package_id.strip(),
            "silent": bool(kwargs.get("silent", True)),
            "timeout": timeout_sec,
        }
        resp = self.client.execute("pc.package_install", payload, timeout=float(timeout_sec))
        return _format_pc_result(resp, self.name)


class PCDownloadInstallerTool(BaseTool):
    """CONFIRM tool to download an installer from a verified HTTPS URL to staging."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.download_installer",
            description="Download an installer executable from an official HTTPS URL to the local staging directory.",
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="Verified HTTPS URL of the installer executable",
                    required=True,
                ),
                ToolParameter(
                    name="dest_filename",
                    type="string",
                    description="Optional filename to save as in staging directory",
                    required=False,
                ),
                ToolParameter(
                    name="timeout",
                    type="integer",
                    description="Download timeout in seconds (default: 300)",
                    required=False,
                ),
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client
        self.timeout = 1800.0

    def execute(self, **kwargs: Any) -> ToolResult:
        url = kwargs.get("url")
        if not url or not isinstance(url, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'url' is required.",
                output="Please specify an HTTPS URL to download.",
            )
        payload: Dict[str, Any] = {"url": url.strip()}
        dest_name = kwargs.get("dest_filename") or kwargs.get("filename")
        if dest_name:
            payload["dest_filename"] = str(dest_name).strip()
        timeout_sec = 1800
        if kwargs.get("timeout"):
            try:
                timeout_sec = int(kwargs["timeout"])
            except Exception:
                pass
        payload["timeout"] = timeout_sec
        resp = self.client.execute("pc.download_installer", payload, timeout=float(timeout_sec))
        return _format_pc_result(resp, self.name)


class PCVerifyInstallerTool(BaseTool):
    """SAFE tool to verify SHA256 and Authenticode digital signature of an installer."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.verify_installer",
            description="Verify an installer file's SHA256 checksum and Windows Authenticode digital signature.",
            parameters=[
                ToolParameter(
                    name="file_path",
                    type="string",
                    description="Absolute path to the downloaded installer file",
                    required=True,
                ),
                ToolParameter(
                    name="expected_sha256",
                    type="string",
                    description="Optional expected SHA256 hash to compare against",
                    required=False,
                ),
            ],
            permission=ToolPermission.SAFE,
            strict_parameters=False,
        )
        self.client = client
        self.timeout = 300.0

    def execute(self, **kwargs: Any) -> ToolResult:
        file_path = kwargs.get("file_path") or kwargs.get("path")
        if not file_path or not isinstance(file_path, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'file_path' is required.",
                output="Please specify a valid installer file path.",
            )
        payload: Dict[str, Any] = {"file_path": file_path.strip()}
        expected = kwargs.get("expected_sha256") or kwargs.get("sha256")
        if expected:
            payload["expected_sha256"] = str(expected).strip()
        resp = self.client.execute("pc.verify_installer", payload)
        return _format_pc_result(resp, self.name)


class PCRunInstallerTool(BaseTool):
    """CONFIRM tool to execute a verified installer with unattended/silent arguments."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient, name: str = "pc.run_installer") -> None:
        super().__init__(
            name=name,
            description="Execute a verified installer executable or MSI package with unattended/silent arguments.",
            parameters=[
                ToolParameter(
                    name="installer_path",
                    type="string",
                    description="Path to the installer binary (.exe or .msi)",
                    required=True,
                ),
                ToolParameter(
                    name="arguments",
                    type="string",
                    description="Optional command-line arguments (e.g. '/S' or '/qn')",
                    required=False,
                ),
                ToolParameter(
                    name="timeout",
                    type="integer",
                    description="Maximum execution timeout in seconds (default: 300)",
                    required=False,
                ),
            ],
            permission=ToolPermission.CONFIRM,
            strict_parameters=False,
        )
        self.client = client
        self.timeout = 1800.0

    def validate_arguments(self, kwargs: dict[str, Any]) -> tuple[bool, str | None]:
        if "arguments" in kwargs and isinstance(kwargs["arguments"], list):
            copied = dict(kwargs)
            copied["arguments"] = " ".join(str(a) for a in kwargs["arguments"])
            return super().validate_arguments(copied)
        return super().validate_arguments(kwargs)

    def execute(self, **kwargs: Any) -> ToolResult:
        installer_path = kwargs.get("installer_path") or kwargs.get("path")
        if not installer_path or not isinstance(installer_path, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'installer_path' is required.",
                output="Please specify a valid installer path to execute.",
            )
        args_raw = kwargs.get("arguments")
        arguments: List[str] = []
        if isinstance(args_raw, list):
            arguments = [str(a) for a in args_raw]
        elif isinstance(args_raw, str) and args_raw.strip():
            arguments = args_raw.strip().split()

        timeout_sec = int(kwargs.get("timeout", 1800))
        payload: Dict[str, Any] = {
            "installer_path": installer_path.strip(),
            "arguments": arguments,
            "timeout": timeout_sec,
        }
        resp = self.client.execute("pc.run_installer", payload, timeout=float(timeout_sec))
        return _format_pc_result(resp, self.name)


class PCVerifyAppRunningTool(BaseTool):
    """SAFE tool to verify that an application is actively running and responsive."""

    def __init__(self, client: PCAgentClient | MockPCAgentClient) -> None:
        super().__init__(
            name="pc.verify_app_running",
            description="Verify that an application process is active and has an interactive window.",
            parameters=[
                ToolParameter(
                    name="app_name",
                    type="string",
                    description="Friendly or executable name of the application (e.g. 'kicad')",
                    required=True,
                )
            ],
            permission=ToolPermission.SAFE,
            strict_parameters=False,
        )
        self.client = client

    def execute(self, **kwargs: Any) -> ToolResult:
        app_name = kwargs.get("app_name") or kwargs.get("app")
        if not app_name or not isinstance(app_name, str):
            return ToolResult(
                tool_name=self.name,
                success=False,
                error="Parameter 'app_name' is required.",
                output="Please specify an application name to verify.",
            )
        resp = self.client.execute("pc.verify_app_running", {"app_name": app_name.strip()})
        return _format_pc_result(resp, self.name)


def register_pc_tools(registry: ToolRegistry, client: PCAgentClient | MockPCAgentClient) -> None:
    """Register all Phase 7 & 8 PC tools into the Bimo ToolRegistry."""
    registry.register(PCGetStatusTool(client))
    registry.register(PCGetActiveWindowTool(client))
    registry.register(PCGetTargetWindowTool(client))
    registry.register(PCGetScreenSizeTool(client))
    registry.register(PCScreenshotTool(client))
    registry.register(PCFocusWindowTool(client))
    registry.register(PCFindAppTool(client))
    registry.register(PCListAppsTool(client))
    registry.register(PCOpenAppTool(client))
    registry.register(PCPlayYouTubeTool(client))
    registry.register(PCCloseAppTool(client))
    registry.register(PCOpenUrlTool(client))
    registry.register(PCTypeTextTool(client))
    registry.register(PCPressKeyTool(client))
    registry.register(PCMoveMouseTool(client))
    registry.register(PCClickTool(client))
    registry.register(PCScrollTool(client))
    # Autonomous operator and software management tools
    registry.register(PCSystemInfoTool(client))
    registry.register(PCPackageSearchTool(client))
    registry.register(PCPackageShowTool(client))
    registry.register(PCPackageInstallTool(client))
    registry.register(PCDownloadInstallerTool(client))
    registry.register(PCVerifyInstallerTool(client))
    registry.register(PCRunInstallerTool(client))
    registry.register(PCVerifyAppRunningTool(client))
