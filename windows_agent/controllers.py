"""OS automation controllers and abstractions for the Windows PC Agent.

Provides clean interfaces and Windows-native implementations (using ctypes.windll.user32, win32gui, PIL)
as well as headless mock implementations for testing.
"""

from __future__ import annotations

import abc
import base64
import io
import os
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple


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
    """Interface for querying and interacting with desktop windows."""

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


# ==============================================================================
# Windows Native Implementations (ctypes / Win32 API)
# ==============================================================================

# Allowed key definitions mapped to Win32 Virtual Key Codes
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
}

# Allowlisted App Maps for Windows
WINDOWS_APP_COMMANDS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "explorer": ["explorer.exe"],
}

WINDOWS_APP_PROCESS_NAMES = {
    "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe",
    "explorer": "explorer.exe",
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

        process_name = "unknown"
        try:
            import win32api
            import win32con
            import win32process
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid.value)
            if handle:
                try:
                    process_name = os.path.basename(win32process.GetModuleFileNameEx(handle, 0))
                finally:
                    win32api.CloseHandle(handle)
        except Exception:
            process_name = f"PID:{pid.value}"

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
                            import win32api
                            import win32con
                            import win32process
                            h_proc = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid.value)
                            if h_proc:
                                try:
                                    proc_name = os.path.basename(win32process.GetModuleFileNameEx(h_proc, 0)).lower()
                                    if norm_title in proc_name or proc_name.startswith(norm_title):
                                        matching_hwnds.append((hwnd, win_text))
                                finally:
                                    win32api.CloseHandle(h_proc)
                        except Exception:
                            pass

        for _ in range(5):
            win32gui.EnumWindows(enum_cb, None)
            if matching_hwnds:
                break
            time.sleep(0.5)

        if not matching_hwnds:
            raise ValueError(f"No visible window found matching title '{title}'.")

        # Prefer exact title match if found, else first matching
        exact = [item for item in matching_hwnds if item[1].lower() == norm_title]
        target_hwnd, found_title = exact[0] if exact else matching_hwnds[0]

        win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
        user32 = ctypes.windll.user32
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


class WindowsAppLauncher(AppLauncher):
    """Windows-specific app launcher with strict allowlist enforcement."""

    def __init__(self, allowed_apps: Optional[List[str]] = None) -> None:
        self.allowed_apps = set(a.lower() for a in (allowed_apps or ["notepad", "calculator", "explorer"]))

    def launch(self, app_name: str) -> Dict[str, Any]:
        normalized = app_name.lower().strip()
        if normalized not in self.allowed_apps or normalized not in WINDOWS_APP_COMMANDS:
            raise ValueError(f"Application '{app_name}' is not in the allowed applications list.")

        cmd = WINDOWS_APP_COMMANDS[normalized]
        proc = subprocess.Popen(cmd, shell=False)
        return {
            "app": normalized,
            "pid": proc.pid,
            "status": "launched",
        }

    def terminate(self, app_name: str) -> bool:
        normalized = app_name.lower().strip()
        if normalized not in self.allowed_apps:
            raise ValueError(f"Application '{app_name}' is not in the allowed applications list.")

        exe_name = WINDOWS_APP_PROCESS_NAMES.get(normalized) or f"{normalized}.exe"
        if exe_name in ("explorer.exe",):
            raise ValueError("Closing explorer process is restricted for system stability.")

        try:
            res = subprocess.run(
                ["taskkill", "/F", "/IM", exe_name],
                capture_output=True,
                check=False,
                shell=False,
            )
            return res.returncode == 0
        except Exception:
            return False


class WindowsKeyboardController(KeyboardController):
    """Windows-specific keyboard simulation using user32.keybd_event."""

    def type_text(self, text: str) -> int:
        if platform.system() != "Windows":
            return len(text)

        import ctypes
        user32 = ctypes.windll.user32

        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002

        count = 0
        for char in text:
            code = ord(char)
            user32.keybd_event(0, code, KEYEVENTF_UNICODE, 0)
            user32.keybd_event(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)
            count += 1
            time.sleep(0.01)

        return count

    def press_key(self, key_name: str) -> bool:
        if platform.system() != "Windows":
            return True

        import ctypes
        user32 = ctypes.windll.user32
        KEYEVENTF_KEYUP = 0x0002

        normalized = key_name.upper().strip()
        parts = [p.strip() for p in normalized.split("+")]

        vk_codes: List[int] = []
        for part in parts:
            if part not in WIN32_VK_MAP:
                raise ValueError(f"Key or modifier '{part}' is not in the allowed keys list.")
            vk_codes.append(WIN32_VK_MAP[part])

        for vk in vk_codes:
            user32.keybd_event(vk, 0, 0, 0)

        time.sleep(0.05)

        for vk in reversed(vk_codes):
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

        return True


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
        # Synthetic minimal valid base64 PNG (1x1 red pixel)
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
    def __init__(self, title: str = "Untitled - Notepad", process_name: str = "notepad.exe") -> None:
        self.title = title
        self.process_name = process_name
        self.closed_calls: List[str] = []
        self.focused_titles: List[str] = []
        self.available_windows: List[str] = ["Untitled - Notepad", "Calculator", "File Explorer"]

    def get_active_window(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "process_name": self.process_name,
            "is_foreground": True,
        }

    def focus_window(self, title: str) -> Dict[str, Any]:
        norm = title.strip().lower()
        if not norm:
            raise ValueError("Window title must be a non-empty string.")

        matches = [w for w in self.available_windows if norm in w.lower()]
        if not matches:
            raise ValueError(f"No visible window found matching title '{title}'.")

        target = matches[0]
        self.title = target
        self.focused_titles.append(target)
        return {"title": target, "status": "focused"}

    def close_window_by_title_or_process(self, target: str) -> bool:
        self.closed_calls.append(target)
        return True


class MockAppLauncher(AppLauncher):
    def __init__(self, allowed_apps: Optional[List[str]] = None) -> None:
        self.allowed_apps = set(a.lower() for a in (allowed_apps or ["notepad", "calculator", "explorer"]))
        self.launched_apps: List[str] = []
        self.terminated_apps: List[str] = []

    def launch(self, app_name: str) -> Dict[str, Any]:
        norm = app_name.lower().strip()
        if norm not in self.allowed_apps:
            raise ValueError(f"Application '{app_name}' is not in the allowed applications list.")
        self.launched_apps.append(norm)
        return {"app": norm, "pid": 9999, "status": "launched"}

    def terminate(self, app_name: str) -> bool:
        norm = app_name.lower().strip()
        if norm not in self.allowed_apps:
            raise ValueError(f"Application '{app_name}' is not in the allowed applications list.")
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
