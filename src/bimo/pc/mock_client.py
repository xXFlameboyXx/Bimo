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
        timeout: Optional[float] = None,
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

        self._handlers["pc.get_target_window"] = lambda args: PCResponse(
            version=1,
            request_id="mock-target-id",
            success=True,
            command="pc.get_target_window",
            output={
                "target": {
                    "hwnd": args.get("hwnd", 1001),
                    "process_id": args.get("pid", 1234),
                    "process_name": f"{args.get('app', 'notepad')}.exe",
                    "title": args.get("title", "Untitled - Notepad"),
                }
            },
        )

        self._handlers["pc.focus_window"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-focus-id",
                success=True,
                command="pc.focus_window",
                output={
                    "title": args.get("title"),
                    "status": "focused",
                    "hwnd": 1001,
                    "target": {
                        "hwnd": 1001,
                        "process_id": 1234,
                        "process_name": "notepad.exe",
                        "title": args.get("title"),
                    },
                },
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

        self._handlers["pc.find_app"] = lambda args: PCResponse(
            version=1,
            request_id="mock-find-app-id",
            success=True,
            command="pc.find_app",
            output={
                "query": args.get("query", ""),
                "matches": [
                    {
                        "display_name": "Discord",
                        "app_type": "desktop",
                        "source": "start_menu_user",
                        "executable_name": "Discord.exe",
                    },
                    {
                        "display_name": "Notepad",
                        "app_type": "system",
                        "source": "system_builtin",
                        "executable_name": "notepad.exe",
                    },
                ],
                "count": 2,
            },
        )

        self._handlers["pc.list_apps"] = lambda args: PCResponse(
            version=1,
            request_id="mock-list-apps-id",
            success=True,
            command="pc.list_apps",
            output={
                "apps": [
                    {"display_name": "Calculator", "app_type": "system", "source": "system_builtin"},
                    {"display_name": "Discord", "app_type": "desktop", "source": "start_menu_user"},
                    {"display_name": "Google Chrome", "app_type": "desktop", "source": "start_menu_common"},
                    {"display_name": "Microsoft Edge", "app_type": "desktop", "source": "start_menu_common"},
                    {"display_name": "Notepad", "app_type": "system", "source": "system_builtin"},
                    {"display_name": "YouTube", "app_type": "web_app", "source": "system_builtin"},
                ],
                "count": 6,
            },
        )

        def _mock_open_app(args: Dict[str, Any]) -> PCResponse:
            app = args.get("app")
            if not app or not isinstance(app, str):
                return PCResponse(
                    version=1,
                    request_id="mock-open-id",
                    success=False,
                    command="pc.open_app",
                    error=PCError(code=PCErrorCode.INVALID_ARGUMENTS.value, message="Parameter 'app' is required"),
                )
            clean_app = app.strip()
            # Reject arbitrary path
            if any(sep in clean_app for sep in ("\\", "/", ":", "..")):
                return PCResponse(
                    version=1,
                    request_id="mock-open-id",
                    success=False,
                    command="pc.open_app",
                    error=PCError(code=PCErrorCode.ARBITRARY_PATH_REJECTED.value, message="Arbitrary path execution is prohibited"),
                )
            # If allowlist mode is active, check against allowed_apps
            if getattr(self.config, "application_allowlist_mode", False):
                if clean_app.lower() not in self.config.allowed_apps:
                    return PCResponse(
                        version=1,
                        request_id="mock-open-id",
                        success=False,
                        command="pc.open_app",
                        error=PCError(code=PCErrorCode.APP_NOT_ALLOWED.value, message=f"App '{clean_app}' not allowlisted"),
                    )

            return PCResponse(
                version=1,
                request_id="mock-open-id",
                success=True,
                command="pc.open_app",
                output={
                    "app": clean_app,
                    "status": "launched",
                    "pid": 1234,
                    "target": {
                        "hwnd": 1001,
                        "process_id": 1234,
                        "process_name": f"{clean_app}.exe",
                        "title": f"Untitled - {clean_app}",
                    },
                },
            )

        self._handlers["pc.open_app"] = _mock_open_app

        def _mock_close_app(args: Dict[str, Any]) -> PCResponse:
            app = args.get("app")
            if not app or not isinstance(app, str):
                return PCResponse(
                    version=1,
                    request_id="mock-close-id",
                    success=False,
                    command="pc.close_app",
                    error=PCError(code=PCErrorCode.INVALID_ARGUMENTS.value, message="Parameter 'app' is required"),
                )
            clean_app = app.strip()
            if getattr(self.config, "application_allowlist_mode", False):
                if clean_app.lower() not in self.config.allowed_apps:
                    return PCResponse(
                        version=1,
                        request_id="mock-close-id",
                        success=False,
                        command="pc.close_app",
                        error=PCError(code=PCErrorCode.APP_NOT_ALLOWED.value, message=f"App '{clean_app}' not allowlisted"),
                    )
            return PCResponse(
                version=1,
                request_id="mock-close-id",
                success=True,
                command="pc.close_app",
                output={"app": clean_app, "status": "closed"},
            )

        self._handlers["pc.close_app"] = _mock_close_app

        self._handlers["pc.open_url"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-url-id",
                success=True,
                command="pc.open_url",
                output={"url": args.get("url"), "status": "opened"},
            )
            if str(args.get("url", "")).startswith(("http://", "https://"))
            else PCResponse(
                version=1,
                request_id="mock-url-id",
                success=False,
                command="pc.open_url",
                error=PCError(code=PCErrorCode.COMMAND_FAILED.value, message="Invalid URL"),
            )
        )

        self._handlers["pc.play_youtube"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-play-id",
                success=True,
                command="pc.play_youtube",
                output={
                    "app": "youtube",
                    "status": "playing",
                    "title": f"Top video for {args.get('query')}",
                    "url": f"https://www.youtube.com/watch?v=mock_{args.get('query')}",
                },
            )
            if args.get("query")
            else PCResponse(
                version=1,
                request_id="mock-play-id",
                success=False,
                command="pc.play_youtube",
                error=PCError(code=PCErrorCode.INVALID_ARGUMENTS.value, message="Query is required"),
            )
        )

        self._handlers["pc.type_text"] = lambda args: (
            PCResponse(
                version=1,
                request_id="mock-type-id",
                success=False,
                command="pc.type_text",
                error=PCError(code=args["fail_error"], message="Forced mock error"),
            )
            if "fail_error" in args
            else (
                PCResponse(
                    version=1,
                    request_id="mock-type-id",
                    success=True,
                    command="pc.type_text",
                    output={
                        "length": len(args.get("text", "")),
                        "status": "typed",
                        "verified_foreground": True,
                        "action": "type_text",
                        "target": args.get("target") or {
                            "hwnd": 1001,
                            "process_id": 1234,
                            "process_name": "notepad.exe",
                            "title": "Untitled - Notepad",
                        },
                        "strategy": args.get("strategy", "foreground_verified_keyboard"),
                    },
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

        self._handlers["pc.system_info"] = lambda args: PCResponse(
            version=1,
            request_id="mock-sysinfo-id",
            success=True,
            command="pc.system_info",
            output={
                "os": "Windows 11 Mock",
                "version": "10.0.22631",
                "architecture": "AMD64",
                "bitness": "64bit",
                "free_storage_gb": 128.5,
                "total_storage_gb": 512.0,
                "total_ram_gb": 16.0,
                "available_ram_gb": 8.5,
                "winget_available": True,
                "winget_path": "C:\\Windows\\System32\\winget.exe",
            },
        )

        self._handlers["pc.package_search"] = lambda args: PCResponse(
            version=1,
            request_id="mock-pkgsearch-id",
            success=True,
            command="pc.package_search",
            output={
                "success": True,
                "query": args.get("query", ""),
                "source": args.get("source", "winget"),
                "count": 2,
                "packages": [
                    {
                        "name": "KiCad",
                        "id": "KiCad.KiCad",
                        "version": "10.0.6",
                    },
                    {
                        "name": "KiCad Lite EDA",
                        "id": "KiCad.KiCad.Lite",
                        "version": "5.1.9",
                    },
                ],
            },
        )

        self._handlers["pc.package_show"] = lambda args: PCResponse(
            version=1,
            request_id="mock-pkgshow-id",
            success=True,
            command="pc.package_show",
            output={
                "success": True,
                "package": {
                    "id": args.get("package_id", "KiCad.KiCad"),
                    "name": "KiCad",
                    "version": "10.0.6",
                    "publisher": "KiCad",
                    "homepage": "https://www.kicad.org/download/windows/",
                    "license": "GPL-3.0",
                    "description": "KiCad is a free software suite for electronic design automation.",
                    "installer_type": "nullsoft",
                    "installer_url": "https://github.com/KiCad/kicad-source-mirror/releases/download/10.0.6/kicad-10.0.6-x86_64.exe",
                    "installer_sha256": "9e24dc47119f7c472128c2f293c5e3f35569274a44c905e60a7514d47c16ce48",
                    "source": "winget",
                },
            },
        )

        self._handlers["pc.package_install"] = lambda args: PCResponse(
            version=1,
            request_id="mock-pkginstall-id",
            success=True,
            command="pc.package_install",
            output={
                "success": True,
                "package_id": args.get("package_id", "KiCad.KiCad"),
                "return_code": 0,
                "elapsed_seconds": 12.4,
                "elevation_required": False,
                "already_installed": False,
                "stdout": "Successfully installed KiCad",
                "error": None,
            },
        )

        self._handlers["pc.download_installer"] = lambda args: PCResponse(
            version=1,
            request_id="mock-download-id",
            success=True,
            command="pc.download_installer",
            output={
                "success": True,
                "file_path": f"C:\\Users\\Mock\\AppData\\Local\\Temp\\bimo_installers\\{args.get('dest_filename') or args.get('filename') or 'kicad-10.0.6-x86_64.exe'}",
                "file_size": 1245000000,
                "file_size_mb": 1187.32,
                "sha256": "9e24dc47119f7c472128c2f293c5e3f35569274a44c905e60a7514d47c16ce48",
                "elapsed_seconds": 15.2,
                "source_url": args.get("url", ""),
            },
        )

        self._handlers["pc.verify_installer"] = lambda args: PCResponse(
            version=1,
            request_id="mock-verify-id",
            success=True,
            command="pc.verify_installer",
            output={
                "success": True,
                "file_path": args.get("file_path", ""),
                "file_size": 1245000000,
                "file_size_mb": 1187.32,
                "sha256": "9e24dc47119f7c472128c2f293c5e3f35569274a44c905e60a7514d47c16ce48",
                "expected_sha256": args.get("expected_sha256"),
                "sha256_matches": True,
                "signature_status": "Valid",
                "signer": "CN=KiCad Developers Team",
                "is_trusted": True,
                "valid": True,
            },
        )

        self._handlers["pc.run_installer"] = lambda args: PCResponse(
            version=1,
            request_id="mock-exec-id",
            success=True,
            command="pc.run_installer",
            output={
                "success": True,
                "installer_path": args.get("installer_path", ""),
                "return_code": 0,
                "elapsed_seconds": 25.0,
                "elevation_required": False,
                "stdout": "Installer completed successfully.",
                "error": None,
            },
        )

        self._handlers["pc.verify_app_running"] = lambda args: PCResponse(
            version=1,
            request_id="mock-apprun-id",
            success=True,
            command="pc.verify_app_running",
            output={
                "app_name": args.get("app_name", "kicad"),
                "is_running": True,
                "running_pids": [4567],
                "process_names": ["kicad.exe"],
                "window_titles": ["KiCad 10.0.6"],
                "status": "running",
            },
        )
