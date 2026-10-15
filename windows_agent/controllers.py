"""OS automation controllers and abstractions for the Windows PC Agent.

Provides clean interfaces and Windows-native implementations (using ctypes.windll.user32, win32gui, PIL)
as well as headless mock implementations for testing. Includes targeted window interaction,
foreground verification, minimized window handling, bounded retry, and text input strategies.
"""

from __future__ import annotations

import abc
import base64
import io
import os
import platform
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from bimo.pc.models import PCErrorCode, TargetWindow
from windows_agent.discovery import (
    ApplicationDiscoveryService,
    ApplicationInfo,
    ApplicationResolver,
    ResolutionResult,
)
from windows_agent.registry import PCCommandError


# ==============================================================================
# Interfaces & Abstractions
# ==============================================================================

class ScreenController(abc.ABC):
    """Interface for querying screen dimensions and capturing screenshots."""

    @abc.abstractmethod
    def get_screen_size(self) -> Tuple[int, int]:
        """Return (width, height) of primary desktop screen."""
        pass

    @abc.abstractmethod
    def capture_screenshot(
        self,
        max_width: int = 1920,
        max_height: int = 1080,
        max_bytes: int = 1_500_000,
    ) -> Dict[str, Any]:
        """Capture primary desktop as bounded base64-encoded image."""
        pass


class WindowController(abc.ABC):
    """Interface for querying, finding, verifying, and controlling desktop windows."""

    @abc.abstractmethod
    def get_active_window(self) -> Dict[str, Any]:
        """Return information about the currently active foreground window."""
        pass

    @abc.abstractmethod
    def focus_window(self, title: str) -> Dict[str, Any]:
        """Find and bring a window matching title to the foreground."""
        pass

    @abc.abstractmethod
    def close_window_by_title_or_process(self, target: str) -> bool:
        """Close an application window matching the target title/process."""
        pass

    @abc.abstractmethod
    def get_target_window(
        self,
        query: Optional[str] = None,
        hwnd: Optional[int] = None,
        app_name: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> Optional[TargetWindow]:
        """Resolve a TargetWindow matching query title, HWND, app_name, or PID."""
        pass

    @abc.abstractmethod
    def validate_target(self, target: TargetWindow) -> Tuple[bool, Optional[str]]:
        """Validate if target HWND still exists and process identity matches. Return (valid, error_code)."""
        pass

    @abc.abstractmethod
    def is_iconic(self, hwnd: int) -> bool:
        """Return True if the window is currently minimized."""
        pass

    @abc.abstractmethod
    def restore_window(self, hwnd: int) -> bool:
        """Restore a minimized window to normal/maximized state."""
        pass

    @abc.abstractmethod
    def focus_target(
        self,
        target: TargetWindow,
        max_retries: int = 3,
        retry_delay: float = 0.1,
    ) -> bool:
        """Bring target to foreground, restoring if minimized, and verify foreground."""
        pass

    @abc.abstractmethod
    def is_foreground(self, hwnd: int) -> bool:
        """Check if hwnd is the current foreground window."""
        pass

    @abc.abstractmethod
    def get_foreground_hwnd(self) -> int:
        """Return the HWND of the current foreground window."""
        pass

    @abc.abstractmethod
    def client_to_screen(self, hwnd: int, rel_x: int, rel_y: int) -> Tuple[int, int]:
        """Convert client-area relative coordinates (rel_x, rel_y) to absolute screen coordinates."""
        pass


class AppLauncher(abc.ABC):
    """Interface for launching and managing allowlisted applications."""

    @abc.abstractmethod
    def launch(self, app_name: str) -> Dict[str, Any]:
        """Launch an allowlisted application."""
        pass

    @abc.abstractmethod
    def terminate(self, app_name: str) -> bool:
        """Terminate an allowlisted application."""
        pass


class UIAutomationController(abc.ABC):
    """Interface for direct UI Automation control interaction."""

    @abc.abstractmethod
    def is_supported(self, target: TargetWindow) -> bool:
        """Return True if target supports reliable direct UI Automation without foreground keyboard injection."""
        pass

    @abc.abstractmethod
    def set_text(self, target: TargetWindow, text: str) -> Dict[str, Any]:
        """Set text directly on target via UI Automation."""
        pass


class TextInputStrategy(abc.ABC):
    """Strategy interface for text input into target windows."""

    @property
    @abc.abstractmethod
    def strategy_name(self) -> str:
        """Name of the strategy for diagnostics and audit logging."""
        pass

    @abc.abstractmethod
    def type_text(
        self,
        target: TargetWindow,
        text: str,
        win_ctrl: WindowController,
        kbd_ctrl: KeyboardController,
        max_retries: int = 3,
        retry_delay: float = 0.1,
        interruption_checker: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Execute text input into target using this strategy."""
        pass


class KeyboardController(abc.ABC):
    """Interface for simulated keyboard input."""

    @abc.abstractmethod
    def type_text(self, text: str) -> int:
        """Type a sequence of characters into the active window."""
        pass

    @abc.abstractmethod
    def press_key(self, key_name: str) -> bool:
        """Press an allowlisted key or shortcut."""
        pass

    @abc.abstractmethod
    def type_text_targeted(
        self,
        target: TargetWindow,
        text: str,
        win_ctrl: WindowController,
        strategy: Optional[TextInputStrategy] = None,
        max_retries: int = 3,
        retry_delay: float = 0.1,
        interruption_checker: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Type text targeted into a verified window."""
        pass

    @abc.abstractmethod
    def press_key_targeted(
        self,
        target: TargetWindow,
        key_name: str,
        win_ctrl: WindowController,
        max_retries: int = 3,
        retry_delay: float = 0.1,
    ) -> Dict[str, Any]:
        """Press an allowlisted key targeted into a verified window."""
        pass


class MouseController(abc.ABC):
    """Interface for simulated mouse movement, clicks, and scroll."""

    @abc.abstractmethod
    def move_mouse(self, x: int, y: int) -> bool:
        """Move mouse cursor to coordinates within primary screen bounds."""
        pass

    @abc.abstractmethod
    def click(
        self,
        button: str = "left",
        clicks: int = 1,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> bool:
        """Perform a mouse click at current or specified coordinates."""
        pass

    @abc.abstractmethod
    def scroll(self, amount: int) -> bool:
        """Scroll vertical mouse wheel by amount (-1000..1000)."""
        pass

    @abc.abstractmethod
    def click_targeted(
        self,
        target: TargetWindow,
        win_ctrl: WindowController,
        button: str = "left",
        clicks: int = 1,
        rel_x: Optional[int] = None,
        rel_y: Optional[int] = None,
        screen_x: Optional[int] = None,
        screen_y: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Perform targeted mouse click with foreground and coordinate verification."""
        pass

    @abc.abstractmethod
    def move_mouse_targeted(
        self,
        target: TargetWindow,
        win_ctrl: WindowController,
        rel_x: Optional[int] = None,
        rel_y: Optional[int] = None,
        screen_x: Optional[int] = None,
        screen_y: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Perform targeted mouse move with coordinate translation."""
        pass


class ApplicationAdapter(abc.ABC):
    """Application-specific reliability adapter for window targeting."""

    @property
    @abc.abstractmethod
    def app_name(self) -> str:
        pass

    @abc.abstractmethod
    def resolve_target(self, win_ctrl: WindowController) -> Optional[TargetWindow]:
        pass

    @abc.abstractmethod
    def get_preferred_strategy(self, uia_ctrl: Optional[UIAutomationController] = None) -> TextInputStrategy:
        pass


# ==============================================================================
# Windows Native Implementations (ctypes / Win32 API)
# ==============================================================================

WIN32_VK_MAP = {
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "TAB": 0x09,
    "SPACE": 0x20,
    "BACKSPACE": 0x08,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "CTRL": 0x11,
    "CONTROL": 0x11,
    "LCTRL": 0xA2,
    "RCTRL": 0xA3,
    "ALT": 0x12,
    "SHIFT": 0x10,
    "WIN": 0x5B,
    "WINDOWS": 0x5B,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "HOME": 0x24,
    "END": 0x23,
    "DELETE": 0x2E,
    "INSERT": 0x2D,
    "C": 0x43,
    "V": 0x56,
    "A": 0x41,
    "S": 0x53,
    "Z": 0x5A,
    "W": 0x57,
    "F": 0x46,
    "X": 0x58,
    "Y": 0x59,
    "K": 0x4B,
    "J": 0x4A,
    "L": 0x4C,
    "M": 0x4D,
    "/": 0xBF,
    "SLASH": 0xBF,
    "OEM_2": 0xBF,
}

WINDOWS_APP_COMMANDS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "youtube": ["chrome.exe", "--app=https://www.youtube.com"],
}

WINDOWS_APP_PROCESS_NAMES = {
    "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe",
    "explorer": "explorer.exe",
    "youtube": "chrome.exe",
}


class WindowsScreenController(ScreenController):
    """Windows-native screen controller for size query and screenshot capture."""

    def get_screen_size(self) -> Tuple[int, int]:
        if platform.system() != "Windows":
            return (1920, 1080)
        import ctypes
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
        return (w, h)

    def capture_screenshot(
        self,
        max_width: int = 1920,
        max_height: int = 1080,
        max_bytes: int = 1_500_000,
    ) -> Dict[str, Any]:
        if platform.system() != "Windows":
            return {
                "width": 100,
                "height": 100,
                "format": "png",
                "image": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                "timestamp": time.time(),
            }

        import ctypes
        from PIL import Image, ImageGrab

        user32 = ctypes.windll.user32
        try:
            hinput = user32.OpenInputDesktop(0, False, 0x0100)
            if hinput:
                user32.SetThreadDesktop(hinput)
                user32.CloseDesktop(hinput)
        except Exception:
            pass

        try:
            img = ImageGrab.grab()
        except Exception as e:
            raise RuntimeError(f"Desktop screenshot capture failed: {e}")

        orig_w, orig_h = img.size
        target_w, target_h = orig_w, orig_h
        if orig_w > max_width or orig_h > max_height:
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            target_w, target_h = img.size

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        raw_bytes = buf.getvalue()

        # If PNG payload exceeds size limit, compress to JPEG
        if len(raw_bytes) > max_bytes:
            buf_jpg = io.BytesIO()
            img.convert("RGB").save(buf_jpg, format="JPEG", quality=75)
            if len(buf_jpg.getvalue()) <= max_bytes:
                raw_bytes = buf_jpg.getvalue()
                fmt = "jpeg"
            else:
                raise ValueError(f"Screenshot payload exceeds maximum limit of {max_bytes} bytes.")
        else:
            fmt = "png"

        b64_str = base64.b64encode(raw_bytes).decode("ascii")
        return {
            "width": target_w,
            "height": target_h,
            "format": fmt,
            "image": b64_str,
            "timestamp": time.time(),
        }


class WindowsWindowController(WindowController):
    """Windows-specific window controller using ctypes.windll.user32 and win32gui."""

    def _get_process_name_by_pid(self, pid: int) -> str:
        if platform.system() != "Windows":
            return "unknown"
        # First attempt: QueryFullProcessImageNameW with PROCESS_QUERY_LIMITED_INFORMATION (0x1000)
        # This succeeds for WindowsApps / UWP packages without requiring full VM_READ rights.
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            hproc = kernel32.OpenProcess(0x1000, False, pid)
            if hproc:
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
                        name = os.path.basename(buf.value)
                        if name:
                            return name
                finally:
                    kernel32.CloseHandle(hproc)
        except Exception:
            pass

        # Second attempt: win32process.GetModuleFileNameEx
        try:
            import win32api
            import win32con
            import win32process
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
            )
            if handle:
                try:
                    return os.path.basename(win32process.GetModuleFileNameEx(handle, 0))
                finally:
                    win32api.CloseHandle(handle)
        except Exception:
            pass
        return f"PID:{pid}"

    def get_active_window(self) -> Dict[str, Any]:
        if platform.system() != "Windows":
            return {"title": "Unknown (Non-Windows)", "process_name": "unknown", "is_foreground": False}

        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"title": "", "process_name": "", "is_foreground": False}

        length = user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = self._get_process_name_by_pid(pid.value)

        return {
            "title": title,
            "process_name": process_name,
            "is_foreground": True,
            "hwnd": hwnd,
        }

    def focus_window(self, title: str) -> Dict[str, Any]:
        if platform.system() != "Windows":
            return {"title": title, "status": "focused"}

        norm_title = title.lower().strip()
        if not norm_title:
            raise ValueError("Window title must be a non-empty string.")

        import ctypes
        import win32con
        import win32gui

        user32 = ctypes.windll.user32
        matching_hwnds: List[Tuple[int, str]] = []

        def enum_cb(hwnd: int, extra: Any) -> None:
            if win32gui.IsWindowVisible(hwnd):
                win_text = win32gui.GetWindowText(hwnd).strip()
                if win_text:
                    if norm_title in win_text.lower():
                        matching_hwnds.append((hwnd, win_text))
                    else:
                        try:
                            pid = ctypes.c_ulong()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            proc_name = self._get_process_name_by_pid(pid.value).lower()
                            if norm_title in proc_name or proc_name.startswith(norm_title):
                                matching_hwnds.append((hwnd, win_text))
                        except Exception:
                            pass

        for _ in range(5):
            win32gui.EnumWindows(enum_cb, None)
            if matching_hwnds:
                break
            time.sleep(0.5)

        if not matching_hwnds:
            raise ValueError(f"No visible window found matching title '{title}'.")

        exact = [item for item in matching_hwnds if item[1].lower() == norm_title]
        target_hwnd, found_title = exact[0] if exact else matching_hwnds[0]

        win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
        user32.BringWindowToTop(target_hwnd)
        user32.SetForegroundWindow(target_hwnd)

        return {
            "title": found_title,
            "hwnd": target_hwnd,
            "status": "focused",
        }

    def close_window_by_title_or_process(self, target: str) -> bool:
        if platform.system() != "Windows":
            return False

        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        WM_CLOSE = 0x0010
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return True

    def _ensure_input_desktop(self) -> None:
        if platform.system() != "Windows":
            return
        import ctypes
        user32 = ctypes.windll.user32
        try:
            hinput = user32.OpenInputDesktop(0, False, 0x0100)
            if hinput:
                user32.SetThreadDesktop(hinput)
        except Exception:
            pass

    def get_foreground_hwnd(self) -> int:
        if platform.system() != "Windows":
            return 0
        self._ensure_input_desktop()
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow())

    def is_foreground(self, hwnd: int) -> bool:
        return self.get_foreground_hwnd() == hwnd

    def is_iconic(self, hwnd: int) -> bool:
        if platform.system() != "Windows":
            return False
        import ctypes
        import win32gui
        user32 = ctypes.windll.user32
        if bool(user32.IsIconic(hwnd)):
            return True
        try:
            placement = win32gui.GetWindowPlacement(hwnd)
            if placement[1] == 2:  # SW_SHOWMINIMIZED
                return True
            rect = win32gui.GetWindowRect(hwnd)
            if rect[0] <= -20000 or rect[1] <= -20000:
                return True
        except Exception:
            pass
        return False

    def restore_window(self, hwnd: int) -> bool:
        if platform.system() != "Windows":
            return True
        import ctypes
        user32 = ctypes.windll.user32
        # SW_RESTORE = 9, SW_SHOWNORMAL = 1
        user32.ShowWindow(hwnd, 9)
        time.sleep(0.05)
        if self.is_iconic(hwnd):
            user32.ShowWindow(hwnd, 1)
            time.sleep(0.05)
        return not self.is_iconic(hwnd)

    def focus_target(
        self,
        target: TargetWindow,
        max_retries: int = 3,
        retry_delay: float = 0.1,
    ) -> bool:
        if platform.system() != "Windows":
            return True

        self._ensure_input_desktop()
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        valid, _ = self.validate_target(target)
        if not valid:
            return False

        my_tid = kernel32.GetCurrentThreadId()

        for _ in range(max(1, max_retries)):
            if self.is_iconic(target.hwnd):
                self.restore_window(target.hwnd)

            curr_fg = user32.GetForegroundWindow()
            if curr_fg == target.hwnd and not self.is_iconic(target.hwnd):
                return True

            curr_thread = user32.GetWindowThreadProcessId(curr_fg, None)
            target_thread = user32.GetWindowThreadProcessId(target.hwnd, None)

            attached_fg = False
            attached_target = False
            if curr_thread != 0 and curr_thread != my_tid:
                attached_fg = bool(user32.AttachThreadInput(my_tid, curr_thread, True))
            if target_thread != 0 and target_thread != my_tid:
                attached_target = bool(user32.AttachThreadInput(my_tid, target_thread, True))

            try:
                user32.AllowSetForegroundWindow(-1)
                # Alt key tap to unlock foreground restrictions
                user32.keybd_event(0x12, 0, 0, 0)
                user32.keybd_event(0x12, 0, 2, 0)

                user32.ShowWindow(target.hwnd, 9)  # SW_RESTORE
                user32.SetWindowPos.argtypes = [
                    wintypes.HWND,
                    wintypes.HWND,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_uint,
                ]
                user32.SetWindowPos.restype = wintypes.BOOL
                # Toggle HWND_TOPMOST then HWND_NOTOPMOST to force DWM z-order promotion
                user32.SetWindowPos(target.hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                user32.SetWindowPos(target.hwnd, wintypes.HWND(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
                user32.BringWindowToTop(target.hwnd)
                user32.SetForegroundWindow(target.hwnd)
            finally:
                if attached_fg:
                    user32.AttachThreadInput(my_tid, curr_thread, False)
                if attached_target:
                    user32.AttachThreadInput(my_tid, target_thread, False)

            time.sleep(retry_delay)
            if self.is_foreground(target.hwnd) and not self.is_iconic(target.hwnd):
                return True

        return self.is_foreground(target.hwnd) and not self.is_iconic(target.hwnd)

    def validate_target(self, target: TargetWindow) -> Tuple[bool, Optional[str]]:
        if platform.system() != "Windows":
            return (True, None)

        import ctypes
        user32 = ctypes.windll.user32

        if not user32.IsWindow(target.hwnd):
            return (False, PCErrorCode.TARGET_CLOSED.value)

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(target.hwnd, ctypes.byref(pid))
        if pid.value == 0:
            return (False, PCErrorCode.TARGET_CLOSED.value)

        if target.process_id and pid.value != target.process_id:
            return (False, PCErrorCode.TARGET_PROCESS_CHANGED.value)

        proc_name = self._get_process_name_by_pid(pid.value)
        if target.process_name and proc_name and not proc_name.startswith("PID:"):
            if target.process_name.lower() != proc_name.lower():
                return (False, PCErrorCode.TARGET_PROCESS_CHANGED.value)

        return (True, None)

    def get_target_window(
        self,
        query: Optional[str] = None,
        hwnd: Optional[int] = None,
        app_name: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> Optional[TargetWindow]:
        if platform.system() != "Windows":
            return TargetWindow(
                hwnd=hwnd or 1001,
                process_id=pid or 5001,
                process_name=f"{app_name or 'app'}.exe",
                title=query or "Mock Window",
                app_name=app_name,
                is_minimized=False,
            )

        self._ensure_input_desktop()
        import ctypes
        import win32gui
        user32 = ctypes.windll.user32

        if hwnd is not None and user32.IsWindow(hwnd):
            proc_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            proc_name = self._get_process_name_by_pid(proc_id.value)
            title = win32gui.GetWindowText(hwnd).strip()
            minimized = bool(user32.IsIconic(hwnd))
            return TargetWindow(
                hwnd=hwnd,
                process_id=proc_id.value,
                process_name=proc_name,
                title=title,
                app_name=app_name,
                is_minimized=minimized,
            )

        expected_proc = WINDOWS_APP_PROCESS_NAMES.get(app_name.lower().strip()) if app_name else None
        matches: List[TargetWindow] = []
        visited_hwnds: Set[int] = set()

        def enum_cb(h: int, extra: Any) -> bool:
            try:
                if h not in visited_hwnds and user32.IsWindow(h):
                    visited_hwnds.add(h)
                    is_vis = win32gui.IsWindowVisible(h)
                    is_ico = self.is_iconic(h)
                    if is_vis or is_ico:
                        w_title = win32gui.GetWindowText(h).strip()
                        proc_id = ctypes.c_ulong()
                        user32.GetWindowThreadProcessId(h, ctypes.byref(proc_id))
                        p_id = proc_id.value
                        if current_pid is not None and p_id != current_pid:
                            return True
                        p_name = self._get_process_name_by_pid(p_id)

                        cls_name = win32gui.GetClassName(h).strip().lower()
                        matched = False
                        if expected_proc and expected_proc.lower() in p_name.lower():
                            matched = True
                        elif app_name and app_name.lower() in p_name.lower():
                            matched = True
                        elif app_name and app_name.lower() in w_title.lower():
                            matched = True
                        elif app_name and app_name.lower() in cls_name:
                            matched = True
                        elif query and query.lower() in w_title.lower():
                            matched = True
                        elif not query and not app_name and current_pid is None:
                            matched = True

                        if matched and (w_title or cls_name != "static"):
                            matches.append(
                                TargetWindow(
                                    hwnd=h,
                                    process_id=p_id,
                                    process_name=p_name,
                                    title=w_title or app_name or "Active Window",
                                    app_name=app_name,
                                    is_minimized=is_ico,
                                )
                            )
            except Exception:
                pass
            return True

        def run_enum(target_pid: Optional[int]) -> None:
            nonlocal current_pid
            current_pid = target_pid
            visited_hwnds.clear()
            hdesk = user32.OpenInputDesktop(0, False, 0x0100)
            if hdesk:
                try:
                    user32.SetThreadDesktop(hdesk)
                    win32gui.EnumDesktopWindows(hdesk, enum_cb, None)
                except Exception:
                    pass
            if not matches:
                win32gui.EnumWindows(enum_cb, None)

        current_pid = pid
        run_enum(pid)

        # Fallback: if matching with pid yielded nothing, match without pid (stub process fallback)
        if not matches and pid is not None and (app_name or query):
            run_enum(None)

        if matches:
            # 1. Prefer non-minimized windows with title
            non_min_title = [tw for tw in matches if tw.title and not tw.is_minimized]
            if non_min_title:
                return non_min_title[0]
            # 2. Prefer windows with title
            with_title = [tw for tw in matches if tw.title]
            if with_title:
                return with_title[0]
            return matches[0]

        # If no filter passed, return current foreground window as TargetWindow
        if not query and not app_name and not pid:
            fg = user32.GetForegroundWindow()
            if fg and user32.IsWindow(fg):
                proc_id = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(fg, ctypes.byref(proc_id))
                p_name = self._get_process_name_by_pid(proc_id.value)
                w_title = win32gui.GetWindowText(fg).strip()
                return TargetWindow(
                    hwnd=fg,
                    process_id=proc_id.value,
                    process_name=p_name,
                    title=w_title,
                    is_minimized=self.is_iconic(fg),
                )

        return None

    def client_to_screen(self, hwnd: int, rel_x: int, rel_y: int) -> Tuple[int, int]:
        if platform.system() != "Windows":
            return (rel_x, rel_y)

        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT(rel_x, rel_y)
        if ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (int(rect.left + rel_x), int(rect.top + rel_y))

        return (rel_x, rel_y)


def resolve_top_youtube_video(query: str) -> Tuple[Optional[str], Optional[str], str]:
    """Resolves the most relevant video ID, title, and URL from YouTube search.

    Returns (video_id, title, watch_url).
    Falls back cleanly to the search results page on network error or missing results.
    """
    import urllib.parse
    import urllib.request
    import re

    clean_query = query.strip()
    encoded = urllib.parse.quote_plus(clean_query)
    fallback_url = f"https://www.youtube.com/results?search_query={encoded}"

    try:
        url = f"https://www.youtube.com/results?search_query={encoded}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        if matches:
            video_id = matches[0]
            # Try to extract video title
            title_pattern = rf'"videoId":"{video_id}".*?"title":\{{"runs":\[\{{"text":"(.*?)"\}}'
            t_match = re.search(title_pattern, html, re.DOTALL)
            if not t_match:
                t_match = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', html)
            title = t_match.group(1) if t_match else clean_query
            try:
                title = title.encode("utf-8").decode("unicode_escape", errors="ignore")
            except Exception:
                pass
            return video_id, title, f"https://www.youtube.com/watch?v={video_id}"
    except Exception as exc:
        logger.warning("YouTube video search resolution failed: %s", exc)

    return None, None, fallback_url


class WindowsAppLauncher(AppLauncher):
    """Windows-specific app launcher with dynamic application discovery and resolution."""

    def __init__(
        self,
        allowed_apps: Optional[List[str]] = None,
        discovery_service: Optional[ApplicationDiscoveryService] = None,
        resolver: Optional[ApplicationResolver] = None,
        allowlist_mode: Optional[bool] = None,
    ) -> None:
        self.allowed_apps = set(a.lower() for a in (allowed_apps or ["notepad", "calculator", "explorer", "youtube"]))
        self.discovery_service = discovery_service or ApplicationDiscoveryService()
        self.resolver = resolver or ApplicationResolver(self.discovery_service)
        if allowlist_mode is None:
            self.allowlist_mode = allowed_apps is not None
        else:
            self.allowlist_mode = allowlist_mode

    def _resolve_youtube_command(self, target_url: str = "https://www.youtube.com") -> List[str]:
        """Resolves the best command to launch YouTube in dedicated app window mode."""
        import shutil
        chrome = (
            shutil.which("chrome")
            or (os.path.exists(r"C:\Program Files\Google\Chrome\Application\chrome.exe") and r"C:\Program Files\Google\Chrome\Application\chrome.exe")
            or (os.path.exists(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe") and r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")
        )
        if chrome:
            return [chrome, f"--app={target_url}"]

        edge = (
            shutil.which("msedge")
            or (os.path.exists(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe") and r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
            or (os.path.exists(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") and r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
        )
        if edge:
            return [edge, f"--app={target_url}"]

        return ["explorer.exe", target_url]

    def launch(
        self,
        app_name: str,
        query: Optional[str] = None,
        url: Optional[str] = None,
        play: bool = True,
    ) -> Dict[str, Any]:
        clean_name = app_name.strip()
        normalized = clean_name.lower()

        # Enforce allowlist only if explicitly configured in allowlist mode
        if self.allowlist_mode and normalized not in self.allowed_apps:
            raise ValueError(f"Application '{app_name}' is not in the allowed applications list.")

        # Resolve application identity dynamically
        res = self.resolver.resolve(clean_name)
        if res.status == "ARBITRARY_PATH_REJECTED":
            raise ValueError(f"Arbitrary path execution is prohibited: {res.error_message}")
        elif res.status == "AMBIGUOUS":
            raise ValueError(f"Ambiguous application name '{clean_name}'. Matches: {', '.join(res.candidates)}")
        elif res.status == "NOT_FOUND" or not res.application:
            raise ValueError(f"Application '{clean_name}' is not installed or could not be found.")

        app_info = res.application
        video_title: Optional[str] = None
        video_url: Optional[str] = None
        pid: Optional[int] = None

        if app_info.name == "youtube":
            if url:
                target_url = url.strip()
            elif query:
                if play:
                    _, video_title, target_url = resolve_top_youtube_video(query)
                else:
                    import urllib.parse
                    target_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query.strip())}"
            else:
                target_url = "https://www.youtube.com"

            video_url = target_url
            cmd = self._resolve_youtube_command(target_url)
            proc = subprocess.Popen(cmd, shell=False)
            pid = proc.pid
        elif app_info.name in WINDOWS_APP_COMMANDS and app_info.source == "system_builtin":
            cmd = WINDOWS_APP_COMMANDS[app_info.name]
            proc = subprocess.Popen(cmd, shell=False)
            pid = proc.pid
        else:
            launch_tgt = app_info.launch_target
            if launch_tgt.lower().endswith(".lnk") and os.path.exists(launch_tgt):
                # Safely open shortcut via os.startfile on Windows
                os.startfile(launch_tgt)
                pid = None
            elif os.path.exists(app_info.executable_path):
                cmd = [app_info.executable_path, *(app_info.arguments or [])]
                proc = subprocess.Popen(cmd, shell=False)
                pid = proc.pid
            else:
                # Try launching target directly if found in PATH
                proc = subprocess.Popen([launch_tgt, *(app_info.arguments or [])], shell=False)
                pid = proc.pid

        result: Dict[str, Any] = {
            "app": app_info.display_name,
            "executable": app_info.executable_name,
            "source": app_info.source,
            "pid": pid,
            "status": "launched",
        }
        if video_title:
            result["title"] = video_title
        if video_url:
            result["url"] = video_url
        return result

    def terminate(self, app_name: str) -> bool:
        clean_name = app_name.strip()
        normalized = clean_name.lower()

        if self.allowlist_mode and normalized not in self.allowed_apps:
            raise ValueError(f"Application '{app_name}' is not in the allowed applications list.")

        res = self.resolver.resolve(clean_name)
        if res.status != "SUCCESS" or not res.application:
            raise ValueError(f"Application '{app_name}' is not installed or could not be resolved.")

        app_info = res.application
        if app_info.name == "youtube":
            # Gracefully close YouTube window via WM_CLOSE without killing entire browser
            if platform.system() == "Windows":
                try:
                    import ctypes
                    import win32gui
                    user32 = ctypes.windll.user32
                    closed = False

                    def enum_cb(h: int, _: Any) -> bool:
                        nonlocal closed
                        if win32gui.IsWindowVisible(h):
                            title = win32gui.GetWindowText(h)
                            if "youtube" in title.lower():
                                user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
                                closed = True
                        return True

                    win32gui.EnumWindows(enum_cb, None)
                    return closed
                except Exception:
                    pass

        exe_name = app_info.executable_name or WINDOWS_APP_PROCESS_NAMES.get(app_info.name) or f"{app_info.name}.exe"
        if exe_name.lower() in ("explorer.exe", "svchost.exe", "csrss.exe", "winlogon.exe"):
            raise ValueError("Closing critical system process is restricted for system stability.")

        # Post WM_CLOSE to windows of this process first for graceful shutdown
        if platform.system() == "Windows":
            try:
                import ctypes
                import win32gui
                user32 = ctypes.windll.user32
                posted = False

                def enum_close_cb(h: int, _: Any) -> bool:
                    nonlocal posted
                    if user32.IsWindow(h):
                        proc_id = ctypes.c_ulong()
                        user32.GetWindowThreadProcessId(h, ctypes.byref(proc_id))
                        p_name = WindowsWindowController._get_process_name_by_pid(proc_id.value)
                        if p_name.lower() == exe_name.lower():
                            user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
                            posted = True
                    return True

                win32gui.EnumWindows(enum_close_cb, None)
                if posted:
                    return True
            except Exception:
                pass

        try:
            res = subprocess.run(
                ["taskkill", "/IM", exe_name],
                capture_output=True,
                check=False,
                shell=False,
            )
            return res.returncode == 0
        except Exception:
            return False


# ==============================================================================
# Text Input Strategies & UI Automation
# ==============================================================================

class WindowsUIAutomationController(UIAutomationController):
    """Windows UI Automation controller.

    Explicitly reports whether direct UI automation is supported vs requiring
    verified foreground keyboard injection.
    """

    def is_supported(self, target: TargetWindow) -> bool:
        # Generic Windows applications default to False so verified foreground
        # keyboard injection is used deterministically.
        return False

    def set_text(self, target: TargetWindow, text: str) -> Dict[str, Any]:
        raise PCCommandError(
            PCErrorCode.COMMAND_FAILED.value,
            f"Direct UI automation not supported for {target.process_name}. Use foreground verified keyboard injection.",
        )


class ForegroundVerifiedKeyboardStrategy(TextInputStrategy):
    """Executes text typing with pre-flight and in-flight foreground verification.

    Never types into an unverified or mismatched window.
    Handles minimized windows automatically with bounded retry.
    Safely pauses and aborts if the user switches active applications mid-typing.
    """

    @property
    def strategy_name(self) -> str:
        return "foreground_verified_keyboard"

    def _find_child_edit_control(self, parent_hwnd: int) -> Optional[int]:
        if platform.system() != "Windows" or not parent_hwnd:
            return None
        import ctypes
        import win32gui
        user32 = ctypes.windll.user32
        try:
            hinput = user32.OpenInputDesktop(0, False, 0x0100)
            if hinput:
                user32.SetThreadDesktop(hinput)
        except Exception:
            pass

        edits: List[int] = []
        def enum_cb(h: int, _: Any) -> bool:
            try:
                cls = win32gui.GetClassName(h)
                if cls in ("RichEditD2DPT", "Edit", "RICHEDIT50W", "RichEdit20W", "RichEdit20A"):
                    edits.append(h)
            except Exception:
                pass
            return True
        try:
            win32gui.EnumChildWindows(parent_hwnd, enum_cb, None)
        except Exception:
            pass
        for e in edits:
            try:
                if win32gui.GetClassName(e) in ("RichEditD2DPT", "Edit"):
                    return e
            except Exception:
                pass
        return edits[0] if edits else None

    def type_text(
        self,
        target: TargetWindow,
        text: str,
        win_ctrl: WindowController,
        kbd_ctrl: KeyboardController,
        max_retries: int = 3,
        retry_delay: float = 0.1,
        interruption_checker: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        # 1. Target validation
        valid, err_code = win_ctrl.validate_target(target)
        if not valid:
            code = err_code or PCErrorCode.WINDOW_VALIDATION_FAILED.value
            raise PCCommandError(code, f"Target window validation failed: {code}")

        # 2. If minimized, restore target
        if win_ctrl.is_iconic(target.hwnd):
            if not win_ctrl.restore_window(target.hwnd):
                raise PCCommandError(
                    PCErrorCode.WINDOW_RESTORE_FAILED.value,
                    f"Failed to restore minimized target window (HWND: {target.hwnd}).",
                )

        # 3. Bring target to foreground and verify
        if not win_ctrl.is_foreground(target.hwnd):
            focused = win_ctrl.focus_target(target, max_retries=max_retries, retry_delay=retry_delay)
            if not focused or not win_ctrl.is_foreground(target.hwnd):
                curr = win_ctrl.get_foreground_hwnd()
                raise PCCommandError(
                    PCErrorCode.TARGET_NOT_FOREGROUND.value,
                    f"Target window '{target.title}' (HWND: {target.hwnd}) could not be brought to foreground. Current foreground HWND: {curr}.",
                )

        # 4. Typing execution
        import ctypes
        user32 = ctypes.windll.user32
        try:
            hinput = user32.OpenInputDesktop(0, False, 0x0100)
            if hinput:
                user32.SetThreadDesktop(hinput)
        except Exception:
            pass

        child_edit = self._find_child_edit_control(target.hwnd)
        if child_edit:
            if interruption_checker and interruption_checker():
                raise PCCommandError(
                    PCErrorCode.USER_INTERACTION_INTERRUPTED.value,
                    "User interaction interrupted typing.",
                    details={"typed_chars": 0, "total_chars": len(text), "target": target.to_dict()},
                )
            EM_REPLACESEL = 0x00C2
            EM_SCROLLCARET = 0x00B7
            user32.SendMessageW(child_edit, EM_REPLACESEL, 1, text)
            user32.SendMessageW(child_edit, EM_SCROLLCARET, 0, 0)
            typed_chars = len(text)
        else:
            typed_chars = 0
            for char in text:
                # Check for external interruption callback
                if interruption_checker and interruption_checker():
                    raise PCCommandError(
                        PCErrorCode.USER_INTERACTION_INTERRUPTED.value,
                        f"User interaction interrupted typing after {typed_chars} of {len(text)} characters.",
                        details={"typed_chars": typed_chars, "total_chars": len(text), "target": target.to_dict()},
                    )

                # Strict Invariant: verify target is STILL foreground before sending next character
                if not win_ctrl.is_foreground(target.hwnd):
                    # Target lost foreground! Check if minimized by user
                    if win_ctrl.is_iconic(target.hwnd):
                        restored = win_ctrl.restore_window(target.hwnd)
                        if not restored:
                            raise PCCommandError(
                                PCErrorCode.USER_INTERACTION_INTERRUPTED.value,
                                f"Target window was minimized during typing after {typed_chars} characters.",
                                details={"typed_chars": typed_chars, "total_chars": len(text), "target": target.to_dict()},
                            )

                    # Attempt bounded refocus (do not aggressively fight the user)
                    refocused = False
                    for _ in range(max(1, max_retries)):
                        time.sleep(retry_delay)
                        if win_ctrl.focus_target(target, max_retries=1, retry_delay=retry_delay):
                            if win_ctrl.is_foreground(target.hwnd):
                                refocused = True
                                break

                    if not refocused or not win_ctrl.is_foreground(target.hwnd):
                        # User actively engaged another window. Safely halt input immediately.
                        raise PCCommandError(
                            PCErrorCode.USER_INTERACTION_INTERRUPTED.value,
                            f"User switched foreground during typing after {typed_chars} of {len(text)} characters. Input safely halted.",
                            details={"typed_chars": typed_chars, "total_chars": len(text), "target": target.to_dict()},
                        )

                # Target is verified foreground -> send character
                kbd_ctrl.type_text(char)
                typed_chars += 1

        return {
            "success": True,
            "target": target.to_dict(),
            "verified_foreground": True,
            "action": "type_text",
            "characters": typed_chars,
            "strategy": self.strategy_name,
        }


class DirectUIAutomationStrategy(TextInputStrategy):
    """Sets text directly on target via UI automation without simulated keyboard events."""

    def __init__(self, uia_ctrl: UIAutomationController) -> None:
        self.uia_ctrl = uia_ctrl

    @property
    def strategy_name(self) -> str:
        return "direct_ui_automation"

    def type_text(
        self,
        target: TargetWindow,
        text: str,
        win_ctrl: WindowController,
        kbd_ctrl: KeyboardController,
        max_retries: int = 3,
        retry_delay: float = 0.1,
        interruption_checker: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        valid, err = win_ctrl.validate_target(target)
        if not valid:
            code = err or PCErrorCode.WINDOW_VALIDATION_FAILED.value
            raise PCCommandError(code, f"Target window validation failed: {code}")

        if not self.uia_ctrl.is_supported(target):
            raise PCCommandError(
                PCErrorCode.COMMAND_FAILED.value,
                f"Direct UI Automation is not supported for target '{target.title}' ({target.process_name}). Fallback to verified foreground keyboard.",
            )

        res = self.uia_ctrl.set_text(target, text)
        return {
            "success": True,
            "target": target.to_dict(),
            "verified_foreground": False,
            "action": "type_text",
            "characters": len(text),
            "strategy": self.strategy_name,
            "details": res,
        }


class NotepadAdapter(ApplicationAdapter):
    """Reliability adapter for Windows Notepad."""

    @property
    def app_name(self) -> str:
        return "notepad"

    def resolve_target(self, win_ctrl: WindowController) -> Optional[TargetWindow]:
        return win_ctrl.get_target_window(app_name="notepad")

    def get_preferred_strategy(self, uia_ctrl: Optional[UIAutomationController] = None) -> TextInputStrategy:
        if uia_ctrl and uia_ctrl.is_supported(TargetWindow(0, 0, "notepad.exe")):
            return DirectUIAutomationStrategy(uia_ctrl)
        return ForegroundVerifiedKeyboardStrategy()


class GenericApplicationAdapter(ApplicationAdapter):
    """Generic adapter for desktop applications."""

    def __init__(self, name: str = "generic") -> None:
        self._name = name

    @property
    def app_name(self) -> str:
        return self._name

    def resolve_target(self, win_ctrl: WindowController) -> Optional[TargetWindow]:
        return win_ctrl.get_target_window(query=self._name)

    def get_preferred_strategy(self, uia_ctrl: Optional[UIAutomationController] = None) -> TextInputStrategy:
        return ForegroundVerifiedKeyboardStrategy()


# ==============================================================================
# Windows Keyboard & Mouse Implementation
# ==============================================================================

class WindowsKeyboardController(KeyboardController):
    """Windows-specific keyboard simulation using user32.SendInput."""

    def _ensure_input_desktop(self) -> None:
        if platform.system() != "Windows":
            return
        import ctypes
        user32 = ctypes.windll.user32
        try:
            hinput = user32.OpenInputDesktop(0, False, 0x0100)
            if hinput:
                user32.SetThreadDesktop(hinput)
        except Exception:
            pass

    def type_text(self, text: str) -> int:
        if platform.system() != "Windows":
            return len(text)

        self._ensure_input_desktop()
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_ulonglong),
            ]

        class INPUT(ctypes.Structure):
            class _INPUT(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]
            _anonymous_ = ("_input",)
            _fields_ = [
                ("type", wintypes.DWORD),
                ("_input", _INPUT),
            ]

        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002

        count = 0
        for char in text:
            code = ord(char)
            if char == "\n":
                inp_down = INPUT(type=1)
                inp_down.ki = KEYBDINPUT(wVk=0x0D, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
                inp_up = INPUT(type=1)
                inp_up.ki = KEYBDINPUT(wVk=0x0D, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
                user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
                user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
            else:
                inp_down = INPUT(type=1)
                inp_down.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
                inp_up = INPUT(type=1)
                inp_up.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
                user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
                user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
            count += 1
            time.sleep(0.015)

        return count

    def press_key(self, key_name: str) -> bool:
        if platform.system() != "Windows":
            return True

        self._ensure_input_desktop()
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_ulonglong),
            ]

        class INPUT(ctypes.Structure):
            class _INPUT(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]
            _anonymous_ = ("_input",)
            _fields_ = [
                ("type", wintypes.DWORD),
                ("_input", _INPUT),
            ]

        KEYEVENTF_KEYUP = 0x0002

        normalized = key_name.upper().strip()
        parts = [p.strip() for p in normalized.split("+")]

        vk_codes: List[int] = []
        for part in parts:
            if part not in WIN32_VK_MAP:
                raise ValueError(f"Key or modifier '{part}' is not in the allowed keys list.")
            vk_codes.append(WIN32_VK_MAP[part])

        for vk in vk_codes:
            inp = INPUT(type=1)
            inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        time.sleep(0.05)

        for vk in reversed(vk_codes):
            inp = INPUT(type=1)
            inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        return True

    def type_text_targeted(
        self,
        target: TargetWindow,
        text: str,
        win_ctrl: WindowController,
        strategy: Optional[TextInputStrategy] = None,
        max_retries: int = 3,
        retry_delay: float = 0.1,
        interruption_checker: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        self._ensure_input_desktop()
        strat = strategy or ForegroundVerifiedKeyboardStrategy()
        return strat.type_text(
            target=target,
            text=text,
            win_ctrl=win_ctrl,
            kbd_ctrl=self,
            max_retries=max_retries,
            retry_delay=retry_delay,
            interruption_checker=interruption_checker,
        )

    def press_key_targeted(
        self,
        target: TargetWindow,
        key_name: str,
        win_ctrl: WindowController,
        max_retries: int = 3,
        retry_delay: float = 0.1,
    ) -> Dict[str, Any]:
        valid, err = win_ctrl.validate_target(target)
        if not valid:
            code = err or PCErrorCode.WINDOW_VALIDATION_FAILED.value
            raise PCCommandError(code, f"Target window validation failed: {code}")

        if win_ctrl.is_iconic(target.hwnd):
            if not win_ctrl.restore_window(target.hwnd):
                raise PCCommandError(
                    PCErrorCode.WINDOW_RESTORE_FAILED.value,
                    f"Failed to restore minimized target window (HWND: {target.hwnd}).",
                )

        if not win_ctrl.is_foreground(target.hwnd):
            if not win_ctrl.focus_target(target, max_retries=max_retries, retry_delay=retry_delay):
                raise PCCommandError(
                    PCErrorCode.TARGET_FOCUS_FAILED.value,
                    f"Failed to bring target window '{target.title}' to foreground.",
                )

        if not win_ctrl.is_foreground(target.hwnd):
            raise PCCommandError(
                PCErrorCode.TARGET_NOT_FOREGROUND.value,
                f"Target window '{target.title}' is not foreground.",
            )

        self.press_key(key_name)
        return {
            "success": True,
            "target": target.to_dict(),
            "action": "press_key",
            "key": key_name,
            "verified_foreground": True,
        }


class WindowsMouseController(MouseController):
    """Windows-specific mouse simulation using user32.SetCursorPos and mouse_event."""

    def __init__(self, screen_ctrl: Optional[ScreenController] = None) -> None:
        self.screen_ctrl = screen_ctrl or WindowsScreenController()

    def move_mouse(self, x: int, y: int) -> bool:
        if platform.system() != "Windows":
            return True

        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            raise ValueError("Mouse coordinates must be integers.")

        screen_w, screen_h = self.screen_ctrl.get_screen_size()
        if x < 0 or x >= screen_w or y < 0 or y >= screen_h:
            raise ValueError(
                f"Coordinates ({x}, {y}) out of primary screen bounds (0..{screen_w - 1}, 0..{screen_h - 1})."
            )

        import ctypes
        user32 = ctypes.windll.user32
        ret = user32.SetCursorPos(x, y)
        return bool(ret)

    def click(
        self,
        button: str = "left",
        clicks: int = 1,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> bool:
        if platform.system() != "Windows":
            return True

        if x is not None and y is not None:
            self.move_mouse(x, y)

        import ctypes
        user32 = ctypes.windll.user32

        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        MOUSEEVENTF_RIGHTDOWN = 0x0008
        MOUSEEVENTF_RIGHTUP = 0x0010

        btn = button.lower().strip()
        if btn == "left":
            down_flag, up_flag = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
        elif btn == "right":
            down_flag, up_flag = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
        else:
            raise ValueError(f"Unsupported mouse button: '{button}'. Must be 'left' or 'right'.")

        for _ in range(max(1, min(clicks, 2))):
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            time.sleep(0.02)
            user32.mouse_event(up_flag, 0, 0, 0, 0)
            time.sleep(0.05)

        return True

    def scroll(self, amount: int) -> bool:
        if platform.system() != "Windows":
            return True

        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ValueError("Scroll amount must be an integer.")

        if amount < -1000 or amount > 1000:
            raise ValueError(f"Scroll amount {amount} exceeds allowable range (-1000..1000).")

        import ctypes
        user32 = ctypes.windll.user32
        MOUSEEVENTF_WHEEL = 0x0800
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, amount, 0)
        return True

    def click_targeted(
        self,
        target: TargetWindow,
        win_ctrl: WindowController,
        button: str = "left",
        clicks: int = 1,
        rel_x: Optional[int] = None,
        rel_y: Optional[int] = None,
        screen_x: Optional[int] = None,
        screen_y: Optional[int] = None,
    ) -> Dict[str, Any]:
        valid, err = win_ctrl.validate_target(target)
        if not valid:
            code = err or PCErrorCode.WINDOW_VALIDATION_FAILED.value
            raise PCCommandError(code, f"Target window validation failed: {code}")

        if win_ctrl.is_iconic(target.hwnd):
            if not win_ctrl.restore_window(target.hwnd):
                raise PCCommandError(
                    PCErrorCode.WINDOW_RESTORE_FAILED.value,
                    f"Failed to restore minimized target window (HWND: {target.hwnd}).",
                )

        if not win_ctrl.is_foreground(target.hwnd):
            if not win_ctrl.focus_target(target):
                raise PCCommandError(
                    PCErrorCode.TARGET_FOCUS_FAILED.value,
                    f"Failed to bring target window '{target.title}' to foreground.",
                )

        if not win_ctrl.is_foreground(target.hwnd):
            raise PCCommandError(
                PCErrorCode.TARGET_NOT_FOREGROUND.value,
                f"Target window '{target.title}' is not foreground.",
            )

        final_x, final_y = None, None
        if rel_x is not None and rel_y is not None:
            final_x, final_y = win_ctrl.client_to_screen(target.hwnd, rel_x, rel_y)
        elif screen_x is not None and screen_y is not None:
            final_x, final_y = screen_x, screen_y

        self.click(button=button, clicks=clicks, x=final_x, y=final_y)
        return {
            "success": True,
            "target": target.to_dict(),
            "action": "click",
            "button": button,
            "clicks": clicks,
            "verified_foreground": True,
            "coords": {
                "screen_x": final_x,
                "screen_y": final_y,
                "rel_x": rel_x,
                "rel_y": rel_y,
            },
        }

    def move_mouse_targeted(
        self,
        target: TargetWindow,
        win_ctrl: WindowController,
        rel_x: Optional[int] = None,
        rel_y: Optional[int] = None,
        screen_x: Optional[int] = None,
        screen_y: Optional[int] = None,
    ) -> Dict[str, Any]:
        valid, err = win_ctrl.validate_target(target)
        if not valid:
            code = err or PCErrorCode.WINDOW_VALIDATION_FAILED.value
            raise PCCommandError(code, f"Target window validation failed: {code}")

        final_x, final_y = 0, 0
        if rel_x is not None and rel_y is not None:
            final_x, final_y = win_ctrl.client_to_screen(target.hwnd, rel_x, rel_y)
        elif screen_x is not None and screen_y is not None:
            final_x, final_y = screen_x, screen_y
        else:
            raise ValueError("Must provide either relative or screen coordinates.")

        self.move_mouse(final_x, final_y)
        return {
            "success": True,
            "target": target.to_dict(),
            "action": "move_mouse",
            "coords": {
                "screen_x": final_x,
                "screen_y": final_y,
                "rel_x": rel_x,
                "rel_y": rel_y,
            },
        }


# ==============================================================================
# Mock Implementations for Tests
# ==============================================================================

class MockScreenController(ScreenController):
    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self.width = width
        self.height = height
        self.screenshot_calls: int = 0
        self.fail_capture: bool = False

    def get_screen_size(self) -> Tuple[int, int]:
        return (self.width, self.height)

    def capture_screenshot(
        self,
        max_width: int = 1920,
        max_height: int = 1080,
        max_bytes: int = 1_500_000,
    ) -> Dict[str, Any]:
        self.screenshot_calls += 1
        if self.fail_capture:
            raise RuntimeError("Mock screenshot capture failed")

        w = min(self.width, max_width)
        h = min(self.height, max_height)
        sample_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        if len(sample_b64) > max_bytes:
            raise ValueError(
                f"Compressed screenshot size ({len(sample_b64)} bytes) exceeds allowable maximum ({max_bytes} bytes)."
            )
        return {
            "width": w,
            "height": h,
            "format": "png",
            "image": sample_b64,
            "timestamp": time.time(),
        }


class MockWindowController(WindowController):
    """Deterministic mock window controller simulating foreground state, iconicity, closures, and interruptions."""

    def __init__(self, title: str = "Untitled - Notepad", process_name: str = "notepad.exe") -> None:
        self.title = title
        self.process_name = process_name
        self.closed_calls: List[str] = []
        self.focused_titles: List[str] = []
        self.available_windows: List[str] = ["Untitled - Notepad", "Calculator", "File Explorer"]
        self.visible_windows: List[Dict[str, Any]] = []

        # Targeted windows state
        self.default_hwnd: int = 1001
        self.foreground_hwnd: int = 1001
        self.windows: Dict[int, Dict[str, Any]] = {
            1001: {
                "hwnd": 1001,
                "process_id": 5001,
                "process_name": process_name,
                "title": title,
                "app_name": "notepad" if "notepad" in process_name.lower() else "app",
            }
        }
        self.minimized_hwnds: Set[int] = set()
        self.closed_hwnds: Set[int] = set()
        self.fail_focus: bool = False
        self.fail_restore: bool = False

        # Interruption simulation: loses foreground after N characters typed
        self.interrupt_after_chars: int = -1
        self.chars_typed_during_simulation: int = 0
        self.switch_to_hwnd_on_interrupt: int = 9999

    def get_active_window(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "process_name": self.process_name,
            "is_foreground": True,
            "hwnd": self.foreground_hwnd,
        }

    def focus_window(self, title: str) -> Dict[str, Any]:
        norm = title.strip().lower()
        if not norm:
            raise ValueError("Window title must be a non-empty string.")

        if self.visible_windows:
            matches = [w["title"] for w in self.visible_windows if norm in str(w.get("title", "")).lower()]
        else:
            matches = [w for w in self.available_windows if norm in w.lower()]

        if not matches:
            raise ValueError(f"No visible window found matching title '{title}'.")

        target = matches[0]
        self.title = target
        self.focused_titles.append(target)
        return {"title": target, "hwnd": self.foreground_hwnd, "status": "focused"}

    def close_window_by_title_or_process(self, target: str) -> bool:
        self.closed_calls.append(target)
        return True

    def get_foreground_hwnd(self) -> int:
        if self.interrupt_after_chars >= 0 and self.chars_typed_during_simulation >= self.interrupt_after_chars:
            return self.switch_to_hwnd_on_interrupt
        return self.foreground_hwnd

    def is_foreground(self, hwnd: int) -> bool:
        return self.get_foreground_hwnd() == hwnd

    def is_iconic(self, hwnd: int) -> bool:
        return hwnd in self.minimized_hwnds

    def restore_window(self, hwnd: int) -> bool:
        if self.fail_restore:
            return False
        self.minimized_hwnds.discard(hwnd)
        return True

    def focus_target(
        self,
        target: TargetWindow,
        max_retries: int = 3,
        retry_delay: float = 0.1,
    ) -> bool:
        if self.fail_focus:
            return False
        if target.hwnd in self.closed_hwnds:
            return False
        if target.hwnd in self.minimized_hwnds:
            self.restore_window(target.hwnd)
        self.foreground_hwnd = target.hwnd
        self.title = target.title
        return True

    def validate_target(self, target: TargetWindow) -> Tuple[bool, Optional[str]]:
        if target.hwnd in self.closed_hwnds:
            return (False, PCErrorCode.TARGET_CLOSED.value)
        win = self.windows.get(target.hwnd)
        if not win:
            return (False, PCErrorCode.TARGET_NOT_FOUND.value)
        if target.process_id and win.get("process_id") != target.process_id:
            return (False, PCErrorCode.TARGET_PROCESS_CHANGED.value)
        if target.process_name and win.get("process_name") != target.process_name:
            return (False, PCErrorCode.TARGET_PROCESS_CHANGED.value)
        return (True, None)

    def get_target_window(
        self,
        query: Optional[str] = None,
        hwnd: Optional[int] = None,
        app_name: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> Optional[TargetWindow]:
        if hwnd is not None:
            if hwnd in self.closed_hwnds:
                return None
            w = self.windows.get(hwnd)
            if w:
                return TargetWindow(
                    hwnd=hwnd,
                    process_id=w.get("process_id", 5001),
                    process_name=w.get("process_name", "unknown"),
                    title=w.get("title", ""),
                    app_name=w.get("app_name"),
                    is_minimized=hwnd in self.minimized_hwnds,
                )
            return None

        # Match by app_name, query, or pid
        for h, w in self.windows.items():
            if h in self.closed_hwnds:
                continue
            if pid is not None and w.get("process_id") == pid:
                return TargetWindow(
                    hwnd=h,
                    process_id=w.get("process_id", 5001),
                    process_name=w.get("process_name", "unknown"),
                    title=w.get("title", ""),
                    app_name=w.get("app_name"),
                    is_minimized=h in self.minimized_hwnds,
                )
            if app_name and app_name.lower() in str(w.get("app_name", "")).lower():
                return TargetWindow(
                    hwnd=h,
                    process_id=w.get("process_id", 5001),
                    process_name=w.get("process_name", "unknown"),
                    title=w.get("title", ""),
                    app_name=w.get("app_name"),
                    is_minimized=h in self.minimized_hwnds,
                )
            if query and query.lower() in str(w.get("title", "")).lower():
                return TargetWindow(
                    hwnd=h,
                    process_id=w.get("process_id", 5001),
                    process_name=w.get("process_name", "unknown"),
                    title=w.get("title", ""),
                    app_name=w.get("app_name"),
                    is_minimized=h in self.minimized_hwnds,
                )

        # Fallback to foreground window
        fg = self.foreground_hwnd
        w = self.windows.get(fg)
        if w:
            return TargetWindow(
                hwnd=fg,
                process_id=w.get("process_id", 5001),
                process_name=w.get("process_name", "unknown"),
                title=w.get("title", self.title),
                app_name=w.get("app_name"),
                is_minimized=fg in self.minimized_hwnds,
            )

        return TargetWindow(
            hwnd=self.default_hwnd,
            process_id=5001,
            process_name=self.process_name,
            title=self.title,
            is_minimized=False,
        )

    def client_to_screen(self, hwnd: int, rel_x: int, rel_y: int) -> Tuple[int, int]:
        # Emulate 100px client area offset for testing relative translations
        return (rel_x + 100, rel_y + 100)


class MockAppLauncher(AppLauncher):
    def __init__(
        self,
        allowed_apps: Optional[List[str]] = None,
        discovery_service: Optional[ApplicationDiscoveryService] = None,
        resolver: Optional[ApplicationResolver] = None,
        allowlist_mode: Optional[bool] = None,
    ) -> None:
        self.allowed_apps = set(a.lower() for a in (allowed_apps or ["notepad", "calculator", "explorer", "youtube"]))
        self.discovery_service = discovery_service
        self.resolver = resolver
        if allowlist_mode is None:
            self.allowlist_mode = True
        else:
            self.allowlist_mode = allowlist_mode
        self.launched_apps: List[str] = []
        self.terminated_apps: List[str] = []

    def launch(
        self,
        app_name: str,
        query: Optional[str] = None,
        url: Optional[str] = None,
        play: bool = True,
    ) -> Dict[str, Any]:
        clean_name = app_name.strip()
        norm = clean_name.lower()
        # Security check: Prohibit arbitrary paths, traversal, direct executable paths, and shell metacharacters
        if any(sep in clean_name for sep in ("\\", "/", ":", "..", ";", "&", "|", "`", "$", "<", ">")) or clean_name.startswith("."):
            raise ValueError(f"Arbitrary path execution is prohibited: '{clean_name}'")

        if self.allowlist_mode and norm not in self.allowed_apps:
            raise ValueError(f"Application '{app_name}' is not in the allowed applications list.")

        if self.resolver is not None:
            res = self.resolver.resolve(clean_name)
            if res.status == "ARBITRARY_PATH_REJECTED":
                raise ValueError(f"Arbitrary path execution is prohibited: {res.error_message}")
            elif res.status == "AMBIGUOUS":
                raise ValueError(f"Ambiguous application name '{clean_name}'. Matches: {', '.join(res.candidates)}")
            elif res.status == "NOT_FOUND" or not res.application:
                if not self.allowlist_mode and norm not in self.allowed_apps:
                    raise ValueError(f"Application '{clean_name}' is not installed or could not be found.")

            app_title = res.application.display_name if res.application else norm
        else:
            if self.allowlist_mode and norm not in self.allowed_apps:
                raise ValueError(f"Application '{app_name}' is not in the allowed applications list.")
            app_title = norm

        self.launched_apps.append(norm)
        res = {"app": app_title, "pid": 9999, "status": "launched"}
        if norm == "youtube" and query:
            res["status"] = "playing" if play else "searched"
            res["title"] = f"Top video for {query}"
            res["url"] = f"https://www.youtube.com/watch?v=mock_{query}"
        return res

    def terminate(self, app_name: str) -> bool:
        clean_name = app_name.strip()
        norm = clean_name.lower()

        if self.allowlist_mode and norm not in self.allowed_apps:
            raise ValueError(f"Application '{app_name}' is not in the allowed applications list.")

        if self.resolver is not None:
            res = self.resolver.resolve(clean_name)
            if res.status != "SUCCESS" or not res.application:
                if not self.allowlist_mode and norm not in self.allowed_apps:
                    raise ValueError(f"Application '{clean_name}' is not installed or could not be resolved.")

        self.terminated_apps.append(norm)
        return True


class MockKeyboardController(KeyboardController):
    def __init__(self) -> None:
        self.typed_text: List[str] = []
        self.pressed_keys: List[str] = []

    def type_text(self, text: str) -> int:
        self.typed_text.append(text)
        return len(text)

    def press_key(self, key_name: str) -> bool:
        norm = key_name.upper().strip()
        parts = [p.strip() for p in norm.split("+")]
        allowed = set(WIN32_VK_MAP.keys())
        for p in parts:
            if p not in allowed:
                raise ValueError(f"Key '{p}' is not in the allowed keys list.")
        self.pressed_keys.append(norm)
        return True

    def type_text_targeted(
        self,
        target: TargetWindow,
        text: str,
        win_ctrl: WindowController,
        strategy: Optional[TextInputStrategy] = None,
        max_retries: int = 3,
        retry_delay: float = 0.1,
        interruption_checker: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        strat = strategy or ForegroundVerifiedKeyboardStrategy()
        # Custom wrapper to update simulation counters on mock win_ctrl
        class TrackedMockKbd(KeyboardController):
            def __init__(self, parent: MockKeyboardController) -> None:
                self.parent = parent
            def type_text(self, t: str) -> int:
                if isinstance(win_ctrl, MockWindowController):
                    win_ctrl.chars_typed_during_simulation += len(t)
                return self.parent.type_text(t)
            def press_key(self, k: str) -> bool:
                return self.parent.press_key(k)
            def type_text_targeted(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
                return {}
            def press_key_targeted(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
                return {}

        res = strat.type_text(
            target=target,
            text=text,
            win_ctrl=win_ctrl,
            kbd_ctrl=TrackedMockKbd(self),
            max_retries=max_retries,
            retry_delay=retry_delay,
            interruption_checker=interruption_checker,
        )
        self.typed_text.append(text)
        return res

    def press_key_targeted(
        self,
        target: TargetWindow,
        key_name: str,
        win_ctrl: WindowController,
        max_retries: int = 3,
        retry_delay: float = 0.1,
    ) -> Dict[str, Any]:
        valid, err = win_ctrl.validate_target(target)
        if not valid:
            code = err or PCErrorCode.WINDOW_VALIDATION_FAILED.value
            raise PCCommandError(code, f"Target window validation failed: {code}")

        if win_ctrl.is_iconic(target.hwnd):
            if not win_ctrl.restore_window(target.hwnd):
                raise PCCommandError(
                    PCErrorCode.WINDOW_RESTORE_FAILED.value,
                    f"Failed to restore minimized target window (HWND: {target.hwnd}).",
                )

        if not win_ctrl.is_foreground(target.hwnd):
            if not win_ctrl.focus_target(target, max_retries=max_retries, retry_delay=retry_delay):
                raise PCCommandError(
                    PCErrorCode.TARGET_FOCUS_FAILED.value,
                    f"Failed to bring target window '{target.title}' to foreground.",
                )

        if not win_ctrl.is_foreground(target.hwnd):
            raise PCCommandError(
                PCErrorCode.TARGET_NOT_FOREGROUND.value,
                f"Target window '{target.title}' is not foreground.",
            )

        self.press_key(key_name)
        return {
            "success": True,
            "target": target.to_dict(),
            "action": "press_key",
            "key": key_name,
            "verified_foreground": True,
        }


class MockMouseController(MouseController):
    def __init__(self, screen_ctrl: Optional[ScreenController] = None) -> None:
        self.screen_ctrl = screen_ctrl or MockScreenController()
        self.clicks: List[Tuple[str, int]] = []
        self.clicks_with_coords: List[Tuple[str, int, Optional[int], Optional[int]]] = []
        self.mouse_moves: List[Tuple[int, int]] = []
        self.scroll_calls: List[int] = []

    def move_mouse(self, x: int, y: int) -> bool:
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            raise ValueError("Mouse coordinates must be integers.")

        screen_w, screen_h = self.screen_ctrl.get_screen_size()
        if x < 0 or x >= screen_w or y < 0 or y >= screen_h:
            raise ValueError(
                f"Coordinates ({x}, {y}) out of primary screen bounds (0..{screen_w - 1}, 0..{screen_h - 1})."
            )

        self.mouse_moves.append((x, y))
        return True

    def click(
        self,
        button: str = "left",
        clicks: int = 1,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> bool:
        btn = button.lower().strip()
        if btn not in ("left", "right"):
            raise ValueError(f"Invalid button: {button}")
        if not isinstance(clicks, int) or clicks not in (1, 2):
            raise ValueError(f"Invalid clicks: {clicks}")

        if x is not None and y is not None:
            self.move_mouse(x, y)

        self.clicks.append((btn, clicks))
        self.clicks_with_coords.append((btn, clicks, x, y))
        return True

    def scroll(self, amount: int) -> bool:
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ValueError("Scroll amount must be an integer.")
        if amount < -1000 or amount > 1000:
            raise ValueError(f"Scroll amount {amount} exceeds allowable range (-1000..1000).")
        self.scroll_calls.append(amount)
        return True

    def click_targeted(
        self,
        target: TargetWindow,
        win_ctrl: WindowController,
        button: str = "left",
        clicks: int = 1,
        rel_x: Optional[int] = None,
        rel_y: Optional[int] = None,
        screen_x: Optional[int] = None,
        screen_y: Optional[int] = None,
    ) -> Dict[str, Any]:
        valid, err = win_ctrl.validate_target(target)
        if not valid:
            code = err or PCErrorCode.WINDOW_VALIDATION_FAILED.value
            raise PCCommandError(code, f"Target window validation failed: {code}")

        if win_ctrl.is_iconic(target.hwnd):
            if not win_ctrl.restore_window(target.hwnd):
                raise PCCommandError(
                    PCErrorCode.WINDOW_RESTORE_FAILED.value,
                    f"Failed to restore minimized target window (HWND: {target.hwnd}).",
                )

        if not win_ctrl.is_foreground(target.hwnd):
            if not win_ctrl.focus_target(target):
                raise PCCommandError(
                    PCErrorCode.TARGET_FOCUS_FAILED.value,
                    f"Failed to bring target window '{target.title}' to foreground.",
                )

        if not win_ctrl.is_foreground(target.hwnd):
            raise PCCommandError(
                PCErrorCode.TARGET_NOT_FOREGROUND.value,
                f"Target window '{target.title}' is not foreground.",
            )

        final_x, final_y = None, None
        if rel_x is not None and rel_y is not None:
            final_x, final_y = win_ctrl.client_to_screen(target.hwnd, rel_x, rel_y)
        elif screen_x is not None and screen_y is not None:
            final_x, final_y = screen_x, screen_y

        self.click(button=button, clicks=clicks, x=final_x, y=final_y)
        return {
            "success": True,
            "target": target.to_dict(),
            "action": "click",
            "button": button,
            "clicks": clicks,
            "verified_foreground": True,
            "coords": {
                "screen_x": final_x,
                "screen_y": final_y,
                "rel_x": rel_x,
                "rel_y": rel_y,
            },
        }

    def move_mouse_targeted(
        self,
        target: TargetWindow,
        win_ctrl: WindowController,
        rel_x: Optional[int] = None,
        rel_y: Optional[int] = None,
        screen_x: Optional[int] = None,
        screen_y: Optional[int] = None,
    ) -> Dict[str, Any]:
        valid, err = win_ctrl.validate_target(target)
        if not valid:
            code = err or PCErrorCode.WINDOW_VALIDATION_FAILED.value
            raise PCCommandError(code, f"Target window validation failed: {code}")

        final_x, final_y = 0, 0
        if rel_x is not None and rel_y is not None:
            final_x, final_y = win_ctrl.client_to_screen(target.hwnd, rel_x, rel_y)
        elif screen_x is not None and screen_y is not None:
            final_x, final_y = screen_x, screen_y
        else:
            raise ValueError("Must provide either relative or screen coordinates.")

        self.move_mouse(final_x, final_y)
        return {
            "success": True,
            "target": target.to_dict(),
            "action": "move_mouse",
            "coords": {
                "screen_x": final_x,
                "screen_y": final_y,
                "rel_x": rel_x,
                "rel_y": rel_y,
            },
        }


class MockUIAutomationController(UIAutomationController):
    """Mock UI Automation controller allowing tests to toggle direct control support."""

    def __init__(self, supported: bool = False) -> None:
        self.supported = supported
        self.set_text_calls: List[Tuple[int, str]] = []

    def is_supported(self, target: TargetWindow) -> bool:
        return self.supported

    def set_text(self, target: TargetWindow, text: str) -> Dict[str, Any]:
        self.set_text_calls.append((target.hwnd, text))
        return {"status": "set", "length": len(text)}
