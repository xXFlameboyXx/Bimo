"""Safe PC Command implementations for Phase 7 and Phase 8 (Controlled Computer Use).

Implements strictly bounded, allowlisted PC commands:
- Observation: pc.get_status, pc.get_active_window, pc.get_screen_size, pc.screenshot, pc.get_target_window
- Window control: pc.focus_window, pc.open_app, pc.close_app
- Mouse: pc.move_mouse, pc.click, pc.scroll (with targeted & relative support)
- Keyboard: pc.type_text, pc.press_key (with verified foreground targeting)

Prohibits any shell execution, PowerShell, eval, or arbitrary system calls.
"""

from __future__ import annotations

import logging
import platform
import socket
import time
from typing import Any, Dict, List, Optional

from bimo.pc.models import PCErrorCode, TargetWindow
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
from windows_agent.discovery import ApplicationDiscoveryService, ApplicationResolver
from windows_agent.installer import InstallerController
from windows_agent.registry import PCCommandError, PCCommandRegistry, PCCommandSpec

logger = logging.getLogger(__name__)

AGENT_VERSION = "1.0.0"
_START_TIME = time.time()
MAX_TYPE_TEXT_LENGTH = 1000
RESTORE_PREVIOUS_WINDOW: bool = False


def build_default_command_registry(
    screen_ctrl: Optional[ScreenController] = None,
    window_ctrl: Optional[WindowController] = None,
    app_launcher: Optional[AppLauncher] = None,
    kbd_ctrl: Optional[KeyboardController] = None,
    mouse_ctrl: Optional[MouseController] = None,
    allowed_apps: Optional[List[str]] = None,
    discovery_service: Optional[ApplicationDiscoveryService] = None,
    resolver: Optional[ApplicationResolver] = None,
    allowlist_mode: bool = False,
    installer_ctrl: Optional[InstallerController] = None,
) -> PCCommandRegistry:
    """Instantiate and populate the PCCommandRegistry with all Phase 7, 8, & autonomous installer commands."""
    registry = PCCommandRegistry()

    # Default to platform-appropriate controllers if not supplied
    scr_ctrl = screen_ctrl or WindowsScreenController()
    win_ctrl = window_ctrl or WindowsWindowController()
    disc_service = discovery_service or ApplicationDiscoveryService()
    app_resolver = resolver or ApplicationResolver(disc_service)
    launcher = app_launcher or WindowsAppLauncher(
        allowed_apps=allowed_apps,
        discovery_service=disc_service,
        resolver=app_resolver,
        allowlist_mode=allowlist_mode,
    )
    kbd = kbd_ctrl or WindowsKeyboardController()
    mouse = mouse_ctrl or WindowsMouseController(screen_ctrl=scr_ctrl)
    installer = installer_ctrl or InstallerController()

    # Session target tracking
    current_target: Optional[TargetWindow] = None

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
            "hwnd": info.get("hwnd", 0),
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

    # 5. pc.get_target_window (SAFE)
    def handle_get_target_window(args: Dict[str, Any]) -> Dict[str, Any]:
        app = args.get("app")
        title = args.get("title")
        hwnd = args.get("hwnd")
        pid = args.get("pid")

        target = win_ctrl.get_target_window(query=title, hwnd=hwnd, app_name=app, pid=pid)
        if not target and current_target:
            target = current_target
        return {"target": target.to_dict() if target else None}

    registry.register(
        PCCommandSpec(
            name="pc.get_target_window",
            description="Query an explicit target window by app name, title, or HWND.",
            handler=handle_get_target_window,
            required_args=[],
            optional_args=["app", "title", "hwnd", "pid"],
        )
    )

    # 6. pc.focus_window (CONFIRM)
    def handle_focus_window(args: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal current_target
        title = args.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Argument 'title' must be a non-empty string.")
        res = win_ctrl.focus_window(title.strip())
        target = win_ctrl.get_target_window(query=title.strip(), hwnd=res.get("hwnd"))
        if target:
            current_target = target
            res["target"] = target.to_dict()
        return res

    registry.register(
        PCCommandSpec(
            name="pc.focus_window",
            description="Finds and brings a visible window matching title to the foreground.",
            handler=handle_focus_window,
            required_args=["title"],
            optional_args=[],
        )
    )

    # 7. pc.find_app (SAFE)
    def handle_find_app(args: Dict[str, Any]) -> Dict[str, Any]:
        query = args.get("query", "")
        matches = disc_service.search(str(query), limit=10)
        return {
            "success": True,
            "query": query,
            "matches": [m.to_safe_dict() for m in matches],
            "count": len(matches),
        }

    registry.register(
        PCCommandSpec(
            name="pc.find_app",
            description="Search installed applications on the Windows PC by friendly name.",
            handler=handle_find_app,
            required_args=["query"],
            optional_args=[],
        )
    )

    # 8. pc.list_apps (SAFE)
    def handle_list_apps(args: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(args.get("limit", 50))
        query = args.get("query")
        apps = disc_service.list_apps(limit=limit, query=str(query) if query else None)
        return {
            "success": True,
            "apps": [a.to_safe_dict() for a in apps],
            "count": len(apps),
        }

    registry.register(
        PCCommandSpec(
            name="pc.list_apps",
            description="List installed applications on the Windows PC.",
            handler=handle_list_apps,
            required_args=[],
            optional_args=["limit", "query"],
        )
    )

    # 9. pc.open_app (CONFIRM)
    def handle_open_app(args: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal current_target
        app_name = args.get("app")
        if not isinstance(app_name, str) or not app_name.strip():
            raise ValueError("Argument 'app' must be a non-empty string.")
        query = args.get("query")
        url = args.get("url")
        play = args.get("play", True)
        res = launcher.launch(app_name.strip(), query=query, url=url, play=play)
        pid = res.get("pid")
        app_display = res.get("app", app_name.strip())
        exe_name = res.get("executable", "")
        exe_base = exe_name[:-4] if exe_name.lower().endswith(".exe") else exe_name

        target = None
        # Give the newly launched app ample time to create, initialize, and render its window (up to 3.0s)
        for _ in range(20):
            if pid:
                target = win_ctrl.get_target_window(app_name=app_name.strip(), pid=pid)
                if target:
                    break
            # Try matching by friendly name, display name, and executable base
            for cand in (app_name.strip(), app_display, exe_base):
                if cand:
                    target = win_ctrl.get_target_window(app_name=cand)
                    if target:
                        break
            if target:
                break
            time.sleep(0.15)

        if target:
            current_target = target
            res["target"] = target.to_dict()
            win_ctrl.focus_target(target)
        else:
            logger.warning("Application '%s' was launched (PID %s) but window could not be resolved yet.", app_name, pid)
        return res

    registry.register(
        PCCommandSpec(
            name="pc.open_app",
            description="Launch an installed application by friendly name.",
            handler=handle_open_app,
            required_args=["app"],
            optional_args=["query", "url", "play"],
        )
    )

    # 8. pc.play_youtube (CONFIRM)
    def handle_play_youtube(args: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal current_target
        query = args.get("query") or args.get("song") or args.get("video") or args.get("search")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Argument 'query' must be a non-empty string specifying the song or video to play.")

        clean_query = query.strip()
        res = launcher.launch("youtube", query=clean_query, play=True)
        pid = res.get("pid")

        target = None
        for _ in range(20):
            if pid:
                target = win_ctrl.get_target_window(app_name="youtube", pid=pid)
                if target:
                    break
            target = win_ctrl.get_target_window(app_name="youtube")
            if target:
                break
            time.sleep(0.15)

        if target:
            current_target = target
            res["target"] = target.to_dict()
            win_ctrl.focus_target(target)

        return res

    registry.register(
        PCCommandSpec(
            name="pc.play_youtube",
            description="Search YouTube for the most relevant video matching the query and play it immediately in the YouTube app.",
            handler=handle_play_youtube,
            required_args=["query"],
            optional_args=[],
        )
    )

    # 8. pc.close_app (CONFIRM)
    def handle_close_app(args: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal current_target
        app_name = args.get("app")
        if not isinstance(app_name, str) or not app_name.strip():
            raise ValueError("Argument 'app' must be a non-empty string.")
        success = launcher.terminate(app_name.strip())
        if current_target and current_target.app_name == app_name.strip():
            current_target = None
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

    # 9. pc.open_url (CONFIRM)
    def handle_open_url(args: Dict[str, Any]) -> Dict[str, Any]:
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("Argument 'url' must be a non-empty string.")
        clean_url = url.strip()
        import urllib.parse
        parsed = urllib.parse.urlparse(clean_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only http and https URLs are allowed.")
        if platform.system() == "Windows":
            import os
            os.startfile(clean_url)
        else:
            import webbrowser
            webbrowser.open(clean_url)
        return {"url": clean_url, "status": "opened"}

    registry.register(
        PCCommandSpec(
            name="pc.open_url",
            description="Open a validated HTTP or HTTPS URL in the default web browser.",
            handler=handle_open_url,
            required_args=["url"],
            optional_args=[],
        )
    )

    # 9. pc.type_text (CONFIRM)
    def handle_type_text(args: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal current_target
        text = args.get("text")
        if not isinstance(text, str) or len(text) == 0:
            raise ValueError("Argument 'text' must be a non-empty string.")
        if len(text) > MAX_TYPE_TEXT_LENGTH:
            raise ValueError(f"Argument 'text' exceeds maximum allowed length of {MAX_TYPE_TEXT_LENGTH} characters.")

        # Resolve explicit or session target
        target: Optional[TargetWindow] = None
        target_arg = args.get("target")
        if target_arg:
            if isinstance(target_arg, dict):
                target = TargetWindow.from_dict(target_arg)
            elif isinstance(target_arg, TargetWindow):
                target = target_arg

        if not target and "hwnd" in args and args["hwnd"] is not None:
            target = win_ctrl.get_target_window(hwnd=args["hwnd"])

        if not target and "app" in args and args["app"]:
            target = win_ctrl.get_target_window(app_name=args["app"])

        if not target and current_target:
            valid, _ = win_ctrl.validate_target(current_target)
            if valid:
                target = current_target
            elif current_target.app_name:
                re_target = win_ctrl.get_target_window(app_name=current_target.app_name)
                if re_target:
                    current_target = re_target
                    target = re_target

        # If still no target, search if any allowlisted application window is open
        if not target:
            for app_cand in ("notepad", "calculator"):
                cand_target = win_ctrl.get_target_window(app_name=app_cand)
                if cand_target:
                    target = cand_target
                    current_target = cand_target
                    break

        # Fallback to active foreground ONLY if it is an allowlisted target window
        if not target:
            fg_hwnd = win_ctrl.get_foreground_hwnd()
            if fg_hwnd:
                active_target = win_ctrl.get_target_window(hwnd=fg_hwnd)
                # Strict security boundary: NEVER type into unallowlisted arbitrary user windows (Chrome, VS Code, etc.)
                if active_target and active_target.app_name in ("notepad", "calculator", "explorer"):
                    target = active_target
                    current_target = active_target

        if not target:
            raise PCCommandError(
                PCErrorCode.TARGET_NOT_FOREGROUND.value,
                "No valid target window available or foreground window verified for keyboard input.",
            )

        restore_previous = bool(args.get("restore_previous", RESTORE_PREVIOUS_WINDOW))
        prev_hwnd = win_ctrl.get_foreground_hwnd() if restore_previous else None

        try:
            res = kbd.type_text_targeted(
                target=target,
                text=text,
                win_ctrl=win_ctrl,
                max_retries=3,
                retry_delay=0.1,
            )
            output = {
                "status": "typed",
                "length": res["characters"],
                "target": target.to_dict(),
                "verified_foreground": True,
                "action": "type_text",
                "strategy": res.get("strategy", "foreground_verified_keyboard"),
            }
        finally:
            if restore_previous and prev_hwnd and prev_hwnd != target.hwnd:
                prev_target = win_ctrl.get_target_window(hwnd=prev_hwnd)
                if prev_target:
                    win_ctrl.focus_target(prev_target, max_retries=2, retry_delay=0.05)
        return output

    registry.register(
        PCCommandSpec(
            name="pc.type_text",
            description="Type text into the verified target window with strict foreground verification.",
            handler=handle_type_text,
            required_args=["text"],
            optional_args=["target", "hwnd", "app", "restore_previous", "strategy"],
        )
    )

    # 10. pc.press_key (CONFIRM)
    def handle_press_key(args: Dict[str, Any]) -> Dict[str, Any]:
        key = args.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Argument 'key' must be a non-empty string.")
        normalized = key.strip().upper()

        target: Optional[TargetWindow] = None
        if "target" in args and args["target"]:
            target = TargetWindow.from_dict(args["target"]) if isinstance(args["target"], dict) else args["target"]
        elif "hwnd" in args and args["hwnd"] is not None:
            target = win_ctrl.get_target_window(hwnd=args["hwnd"])
        elif "app" in args and args["app"]:
            target = win_ctrl.get_target_window(app_name=args["app"])
        elif current_target:
            target = current_target

        if target:
            kbd.press_key_targeted(target=target, key_name=normalized, win_ctrl=win_ctrl)
            return {"key": normalized, "status": "pressed", "target": target.to_dict(), "verified_foreground": True}
        else:
            kbd.press_key(normalized)
            return {"key": normalized, "status": "pressed"}

    registry.register(
        PCCommandSpec(
            name="pc.press_key",
            description="Press an allowlisted key or shortcut combination in verified target.",
            handler=handle_press_key,
            required_args=["key"],
            optional_args=["target", "hwnd", "app"],
        )
    )

    # 11. pc.move_mouse (CONFIRM)
    def handle_move_mouse(args: Dict[str, Any]) -> Dict[str, Any]:
        x = args.get("x")
        y = args.get("y")
        rel_x = args.get("rel_x")
        rel_y = args.get("rel_y")
        is_relative = bool(args.get("relative", False)) or (rel_x is not None and rel_y is not None)

        target: Optional[TargetWindow] = None
        if "target" in args and args["target"]:
            target = TargetWindow.from_dict(args["target"]) if isinstance(args["target"], dict) else args["target"]
        elif "hwnd" in args and args["hwnd"] is not None:
            target = win_ctrl.get_target_window(hwnd=args["hwnd"])
        elif "app" in args and args["app"]:
            target = win_ctrl.get_target_window(app_name=args["app"])
        elif current_target and is_relative:
            target = current_target

        if target and is_relative:
            target_rel_x = rel_x if rel_x is not None else x
            target_rel_y = rel_y if rel_y is not None else y
            if target_rel_x is None or target_rel_y is None:
                raise ValueError("Must provide coordinates for relative move.")
            res = mouse.move_mouse_targeted(
                target=target,
                win_ctrl=win_ctrl,
                rel_x=target_rel_x,
                rel_y=target_rel_y,
            )
            return {
                "x": res["coords"]["screen_x"],
                "y": res["coords"]["screen_y"],
                "status": "moved",
                "target": target.to_dict(),
                "relative": True,
            }
        else:
            if not isinstance(x, int) or isinstance(x, bool):
                raise ValueError("Argument 'x' must be an integer.")
            if not isinstance(y, int) or isinstance(y, bool):
                raise ValueError("Argument 'y' must be an integer.")
            mouse.move_mouse(x, y)
            return {"x": x, "y": y, "status": "moved"}

    registry.register(
        PCCommandSpec(
            name="pc.move_mouse",
            description="Move mouse cursor to screen or target-relative coordinates.",
            handler=handle_move_mouse,
            required_args=["x", "y"],
            optional_args=["target", "hwnd", "app", "relative", "rel_x", "rel_y"],
        )
    )

    # 12. pc.click (CONFIRM)
    def handle_click(args: Dict[str, Any]) -> Dict[str, Any]:
        button = args.get("button", "left")
        clicks = args.get("clicks", 1)
        x = args.get("x")
        y = args.get("y")
        rel_x = args.get("rel_x")
        rel_y = args.get("rel_y")
        is_relative = bool(args.get("relative", False)) or (rel_x is not None and rel_y is not None)

        if not isinstance(button, str) or button.lower() not in ("left", "right"):
            raise ValueError("Argument 'button' must be either 'left' or 'right'.")
        if not isinstance(clicks, int) or clicks not in (1, 2):
            raise ValueError("Argument 'clicks' must be 1 or 2.")
        if x is not None and (not isinstance(x, int) or isinstance(x, bool)):
            raise ValueError("Argument 'x' must be an integer.")
        if y is not None and (not isinstance(y, int) or isinstance(y, bool)):
            raise ValueError("Argument 'y' must be an integer.")
        if rel_x is not None and (not isinstance(rel_x, int) or isinstance(rel_x, bool)):
            raise ValueError("Argument 'rel_x' must be an integer.")
        if rel_y is not None and (not isinstance(rel_y, int) or isinstance(rel_y, bool)):
            raise ValueError("Argument 'rel_y' must be an integer.")

        target: Optional[TargetWindow] = None
        if "target" in args and args["target"]:
            target = TargetWindow.from_dict(args["target"]) if isinstance(args["target"], dict) else args["target"]
        elif "hwnd" in args and args["hwnd"] is not None:
            target = win_ctrl.get_target_window(hwnd=args["hwnd"])
        elif "app" in args and args["app"]:
            target = win_ctrl.get_target_window(app_name=args["app"])
        elif current_target and is_relative:
            target = current_target

        if target:
            target_rel_x = rel_x if rel_x is not None else (x if is_relative else None)
            target_rel_y = rel_y if rel_y is not None else (y if is_relative else None)
            screen_x = x if not is_relative else None
            screen_y = y if not is_relative else None

            res = mouse.click_targeted(
                target=target,
                win_ctrl=win_ctrl,
                button=button.lower(),
                clicks=clicks,
                rel_x=target_rel_x,
                rel_y=target_rel_y,
                screen_x=screen_x,
                screen_y=screen_y,
            )
            return {
                "button": button.lower(),
                "clicks": clicks,
                "status": "clicked",
                "target": target.to_dict(),
                "verified_foreground": True,
                "coords": res.get("coords"),
            }
        else:
            mouse.click(button=button.lower(), clicks=clicks, x=x, y=y)
            res_dict: Dict[str, Any] = {"button": button.lower(), "clicks": clicks, "status": "clicked"}
            if x is not None and y is not None:
                res_dict["x"] = x
                res_dict["y"] = y
            return res_dict

    registry.register(
        PCCommandSpec(
            name="pc.click",
            description="Click mouse button at current position, screen coordinates, or target-relative coordinates.",
            handler=handle_click,
            required_args=[],
            optional_args=["button", "clicks", "x", "y", "target", "hwnd", "app", "relative", "rel_x", "rel_y"],
        )
    )

    # 13. pc.scroll (CONFIRM)
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

    # 14. pc.system_info (SAFE)
    def handle_system_info(args: Dict[str, Any]) -> Dict[str, Any]:
        return installer.get_system_info()

    registry.register(
        PCCommandSpec(
            name="pc.system_info",
            description="Query host architecture, OS version, disk storage on system drive, and memory.",
            handler=handle_system_info,
            required_args=[],
            optional_args=[],
        )
    )

    # 15. pc.package_search (SAFE)
    def handle_package_search(args: Dict[str, Any]) -> Dict[str, Any]:
        query = args.get("query")
        if not query or not isinstance(query, str):
            raise ValueError("Argument 'query' must be a non-empty string.")
        source = args.get("source", "winget")
        return installer.package_search(query.strip(), source=source)

    registry.register(
        PCCommandSpec(
            name="pc.package_search",
            description="Search Windows Package Manager (winget) repositories for software packages matching query.",
            handler=handle_package_search,
            required_args=["query"],
            optional_args=["source"],
        )
    )

    # 16. pc.package_show (SAFE)
    def handle_package_show(args: Dict[str, Any]) -> Dict[str, Any]:
        package_id = args.get("package_id") or args.get("id")
        if not package_id or not isinstance(package_id, str):
            raise ValueError("Argument 'package_id' must be a non-empty string.")
        source = args.get("source", "winget")
        return installer.package_show(package_id.strip(), source=source)

    registry.register(
        PCCommandSpec(
            name="pc.package_show",
            description="Query detailed metadata, publisher, homepage, license, and installer URL for a package ID.",
            handler=handle_package_show,
            required_args=["package_id"],
            optional_args=["source", "id"],
        )
    )

    # 17. pc.package_install (CONFIRM)
    def handle_package_install(args: Dict[str, Any]) -> Dict[str, Any]:
        package_id = args.get("package_id") or args.get("id")
        if not package_id or not isinstance(package_id, str):
            raise ValueError("Argument 'package_id' must be a non-empty string.")
        silent = bool(args.get("silent", True))
        timeout = int(args.get("timeout", 600))
        return installer.package_install(package_id.strip(), silent=silent, timeout=timeout)

    registry.register(
        PCCommandSpec(
            name="pc.package_install",
            description="Install a verified package by package ID using Windows Package Manager (winget) unattended.",
            handler=handle_package_install,
            required_args=["package_id"],
            optional_args=["silent", "timeout", "id"],
        )
    )

    # 18. pc.download_installer (CONFIRM)
    def handle_download_installer(args: Dict[str, Any]) -> Dict[str, Any]:
        url = args.get("url")
        if not url or not isinstance(url, str):
            raise ValueError("Argument 'url' must be a valid HTTP or HTTPS string.")
        dest_filename = args.get("dest_filename")
        timeout = int(args.get("timeout", 1800))
        return installer.download_installer(url.strip(), dest_filename=dest_filename, timeout=timeout)

    registry.register(
        PCCommandSpec(
            name="pc.download_installer",
            description="Download an installer from a verified HTTPS URL to the local staging directory.",
            handler=handle_download_installer,
            required_args=["url"],
            optional_args=["dest_filename", "timeout"],
        )
    )

    # 19. pc.verify_installer (SAFE)
    def handle_verify_installer(args: Dict[str, Any]) -> Dict[str, Any]:
        file_path = args.get("file_path") or args.get("path")
        if not file_path or not isinstance(file_path, str):
            raise ValueError("Argument 'file_path' must be a non-empty string.")
        expected_sha256 = args.get("expected_sha256") or args.get("sha256")
        return installer.verify_installer(file_path.strip(), expected_sha256=expected_sha256)

    registry.register(
        PCCommandSpec(
            name="pc.verify_installer",
            description="Verify an installer file's SHA256 checksum and Windows Authenticode digital signature.",
            handler=handle_verify_installer,
            required_args=["file_path"],
            optional_args=["expected_sha256", "sha256", "path"],
        )
    )

    # 20. pc.run_installer (CONFIRM)
    def handle_run_installer(args: Dict[str, Any]) -> Dict[str, Any]:
        installer_path = args.get("installer_path") or args.get("path")
        if not installer_path or not isinstance(installer_path, str):
            raise ValueError("Argument 'installer_path' must be a non-empty string.")
        arguments = args.get("arguments")
        if arguments is not None and not isinstance(arguments, list):
            if isinstance(arguments, str):
                arguments = arguments.split()
            else:
                arguments = []
        timeout = int(args.get("timeout", 300))
        return installer.execute_installer(installer_path.strip(), arguments=arguments, timeout=timeout)

    registry.register(
        PCCommandSpec(
            name="pc.run_installer",
            description="Execute a verified installer executable or MSI package with silent/unattended arguments.",
            handler=handle_run_installer,
            required_args=["installer_path"],
            optional_args=["arguments", "timeout", "path"],
        )
    )

    # 21. pc.verify_app_running (SAFE)
    def handle_verify_app_running(args: Dict[str, Any]) -> Dict[str, Any]:
        app_name = args.get("app_name") or args.get("app")
        if not app_name or not isinstance(app_name, str):
            raise ValueError("Argument 'app_name' must be a non-empty string.")
        return installer.verify_app_running(app_name.strip())

    registry.register(
        PCCommandSpec(
            name="pc.verify_app_running",
            description="Verify that an application is actively running, responsive, and has interactive windows.",
            handler=handle_verify_app_running,
            required_args=["app_name"],
            optional_args=["app"],
        )
    )

    return registry
