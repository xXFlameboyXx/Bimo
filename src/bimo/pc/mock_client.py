"""Deterministic Mock PCAgentClient for Phase 7 testing.

Simulates Windows PC Agent responses, disconnections, latency, and authentication
failures without network sockets or Windows OS GUI dependencies.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from bimo.core.config import PCAgentConfig
from bimo.core.events import Event, EventBus, EventType
from bimo.pc.models import PCError, PCErrorCode, PCResponse


class MockPCAgentClient:
    """Mock client implementing the PCAgentClient interface for automated tests."""

    def __init__(
        self,
        config: Optional[PCAgentConfig] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.config = config or PCAgentConfig(enabled=True)
        self.event_bus = event_bus
        self._connected: bool = True
        self.history: List[Dict[str, Any]] = []
        self._handlers: Dict[str, Callable[[Dict[str, Any]], PCResponse]] = {}
        self.fail_with_auth: bool = False
        self.fail_with_timeout: bool = False
        self.fail_with_unavailable: bool = False
        self._register_default_handlers()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, connected: bool) -> None:
        was = self._connected
        self._connected = connected
        if self.event_bus:
            if connected and not was:
                self.event_bus.publish(
                    Event(type=EventType.PC_CONNECTED, data={"host": self.config.host, "port": self.config.port}, source="mock_pc_client")
                )
            elif not connected and was:
                self.event_bus.publish(
                    Event(type=EventType.PC_DISCONNECTED, data={"host": self.config.host, "port": self.config.port}, source="mock_pc_client")
                )

    def check_health(self) -> bool:
        return self._connected

    def set_handler(self, command: str, handler: Callable[[Dict[str, Any]], PCResponse]) -> None:
        """Override response handler for a specific command."""
        self._handlers[command] = handler

    def execute(
        self,
        command: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> PCResponse:
        """Simulate sending a command to the PC agent."""
        args = arguments or {}
        self.history.append({"command": command, "arguments": args})

        if not self.config.enabled:
            return PCResponse(
                version=1,
                request_id="mock-id",
                success=False,
                command=command,
                error=PCError(code=PCErrorCode.UNAVAILABLE.value, message="PC Agent is disabled in config."),
            )

        if self.fail_with_timeout:
            return PCResponse(
                version=1,
                request_id="mock-id",
                success=False,
                command=command,
                error=PCError(code=PCErrorCode.TIMEOUT.value, message="Mock request timed out"),
            )

        if self.fail_with_auth:
            return PCResponse(
                version=1,
                request_id="mock-id",
                success=False,
                command=command,
                error=PCError(code=PCErrorCode.AUTH_FAILED.value, message="Mock authentication failed"),
            )

        if self.fail_with_unavailable or not self._connected:
            return PCResponse(
                version=1,
                request_id="mock-id",
                success=False,
                command=command,
                error=PCError(code=PCErrorCode.UNAVAILABLE.value, message="Cannot reach Windows PC Agent (offline)"),
            )

        handler = self._handlers.get(command)
        if handler:
            return handler(args)

        return PCResponse(
            version=1,
            request_id="mock-id",
            success=False,
            command=command,
            error=PCError(code=PCErrorCode.UNKNOWN_COMMAND.value, message=f"Unknown command '{command}'"),
        )

    def _register_default_handlers(self) -> None:
        self._handlers["pc.get_status"] = lambda args: PCResponse(
            version=1,
            request_id="mock-status-id",
            success=True,
            command="pc.get_status",
            output={
                "hostname": "MOCK-WIN-PC",
                "os": "Windows 11 Mock",
                "agent_version": "1.0.0",
                "status": "online",
                "uptime_seconds": 3600,
                "registered_commands": [
                    "pc.click",
                    "pc.close_app",
                    "pc.focus_window",
                    "pc.get_active_window",
                    "pc.get_screen_size",
                    "pc.get_status",
                    "pc.move_mouse",
                    "pc.open_app",
                    "pc.press_key",
                    "pc.screenshot",
                    "pc.scroll",
                    "pc.type_text",
                ],
            },
        )

        self._handlers["pc.get_active_window"] = lambda args: PCResponse(
            version=1,
            request_id="mock-win-id",
            success=True,
            command="pc.get_active_window",
            output={
                "title": "Untitled - Notepad",
                "process_name": "notepad.exe",
                "is_foreground": True,
            },
        )

        self._handlers["pc.get_screen_size"] = lambda args: PCResponse(
            version=1,
            request_id="mock-scr-id",
            success=True,
            command="pc.get_screen_size",
            output={"width": 1920, "height": 1080},
        )

        self._handlers["pc.screenshot"] = lambda args: PCResponse(
            version=1,
            request_id="mock-shot-id",
            success=True,
            command="pc.screenshot",
            output={
                "width": 1920,
                "height": 1080,
                "format": "png",
                "image": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
            },
        )

        self._handlers["pc.focus_window"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-focus-id",
                success=True,
                command="pc.focus_window",
                output={"title": args.get("title"), "status": "focused"},
            )
            if isinstance(args.get("title"), str) and len(args.get("title", "").strip()) > 0
            else PCResponse(
                version=1,
                request_id="mock-focus-id",
                success=False,
                command="pc.focus_window",
                error=PCError(code=PCErrorCode.INVALID_ARGUMENTS.value, message="Invalid title argument"),
            )
        )

        self._handlers["pc.open_app"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-open-id",
                success=True,
                command="pc.open_app",
                output={"app": args.get("app"), "status": "launched", "pid": 1234},
            )
            if args.get("app") in ("notepad", "calculator", "explorer")
            else PCResponse(
                version=1,
                request_id="mock-open-id",
                success=False,
                command="pc.open_app",
                error=PCError(code=PCErrorCode.APP_NOT_ALLOWED.value, message=f"App '{args.get('app')}' not allowlisted"),
            )
        )

        self._handlers["pc.close_app"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-close-id",
                success=True,
                command="pc.close_app",
                output={"app": args.get("app"), "status": "closed"},
            )
            if args.get("app") in ("notepad", "calculator", "explorer")
            else PCResponse(
                version=1,
                request_id="mock-close-id",
                success=False,
                command="pc.close_app",
                error=PCError(code=PCErrorCode.APP_NOT_ALLOWED.value, message=f"App '{args.get('app')}' not allowlisted"),
            )
        )

        self._handlers["pc.type_text"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-type-id",
                success=True,
                command="pc.type_text",
                output={"length": len(args.get("text", "")), "status": "typed"},
            )
            if isinstance(args.get("text"), str) and len(args.get("text", "")) > 0
            else PCResponse(
                version=1,
                request_id="mock-type-id",
                success=False,
                command="pc.type_text",
                error=PCError(code=PCErrorCode.INVALID_ARGUMENTS.value, message="Invalid text argument"),
            )
        )

        self._handlers["pc.press_key"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-key-id",
                success=True,
                command="pc.press_key",
                output={"key": args.get("key"), "status": "pressed"},
            )
            if args.get("key") in ("ENTER", "ESC", "TAB", "CTRL+C", "CTRL+V", "ALT+TAB", "WIN", "CTRL+A", "CTRL+S")
            else PCResponse(
                version=1,
                request_id="mock-key-id",
                success=False,
                command="pc.press_key",
                error=PCError(code=PCErrorCode.KEY_NOT_ALLOWED.value, message=f"Key '{args.get('key')}' not allowed"),
            )
        )

        self._handlers["pc.move_mouse"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-move-id",
                success=True,
                command="pc.move_mouse",
                output={"x": args.get("x"), "y": args.get("y"), "status": "moved"},
            )
            if isinstance(args.get("x"), int) and not isinstance(args.get("x"), bool)
            and isinstance(args.get("y"), int) and not isinstance(args.get("y"), bool)
            and 0 <= args.get("x") < 1920 and 0 <= args.get("y") < 1080
            else PCResponse(
                version=1,
                request_id="mock-move-id",
                success=False,
                command="pc.move_mouse",
                error=PCError(code=PCErrorCode.INVALID_ARGUMENTS.value, message="Coordinates out of bounds or invalid"),
            )
        )

        self._handlers["pc.click"] = lambda args: PCResponse(
            version=1,
            request_id="mock-click-id",
            success=True,
            command="pc.click",
            output={
                "button": args.get("button", "left"),
                "clicks": args.get("clicks", 1),
                "status": "clicked",
                **({"x": args["x"], "y": args["y"]} if "x" in args and "y" in args else {}),
            },
        )

        self._handlers["pc.scroll"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-scroll-id",
                success=True,
                command="pc.scroll",
                output={"amount": args.get("amount"), "status": "scrolled"},
            )
            if isinstance(args.get("amount"), int) and not isinstance(args.get("amount"), bool)
            and -1000 <= args.get("amount") <= 1000
            else PCResponse(
                version=1,
                request_id="mock-scroll-id",
                success=False,
                command="pc.scroll",
                error=PCError(code=PCErrorCode.INVALID_ARGUMENTS.value, message="Scroll amount out of range"),
            )
        )
