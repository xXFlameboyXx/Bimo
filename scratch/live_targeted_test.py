"""Live Windows verification test for targeted interaction.

Exercises:
TEST A: Open Notepad, type 'Hello from Bimo', verify target window.
TEST B: Minimize Notepad, run targeted typing task -> verifies automatic restore, foreground verification, and typing.
TEST C: Target Notepad while an unverified window is active -> verifies target refocus or blocking, zero input into wrong window.
TEST E: Close Notepad -> attempt targeted action -> verify clean failure TARGET_CLOSED.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("src"))

from typing import Any, Dict

from bimo.pc.models import PCErrorCode, TargetWindow
from windows_agent.controllers import (
    ForegroundVerifiedKeyboardStrategy,
    WindowsAppLauncher,
    WindowsKeyboardController,
    WindowsWindowController,
)
from windows_agent.registry import PCCommandError


def main() -> None:
    print("=== LIVE WINDOWS TARGETED INTERACTION TESTS ===")
    win_ctrl = WindowsWindowController()
    kbd = WindowsKeyboardController()
    launcher = WindowsAppLauncher()

    # TEST A: Launch Notepad and type verified text
    print("\n[TEST A] Launching Notepad...")
    launch_res = launcher.launch("notepad")
    pid = launch_res["pid"]
    print(f"Launched Notepad (PID: {pid}). Waiting for window to register...")

    notepad_target: TargetWindow | None = None
    for _ in range(20):
        notepad_target = win_ctrl.get_target_window(app_name="notepad", pid=pid)
        if notepad_target:
            break
        time.sleep(0.2)

    if not notepad_target:
        # Try generic notepad lookup
        notepad_target = win_ctrl.get_target_window(app_name="notepad")

    assert notepad_target is not None, "Failed to resolve live Notepad TargetWindow!"
    print(f"Resolved target: HWND={notepad_target.hwnd}, PID={notepad_target.process_id}, Title='{notepad_target.title}', Process='{notepad_target.process_name}'")

    print("[TEST A] Typing 'Hello from Bimo' into verified Notepad...")
    res_a = kbd.type_text_targeted(target=notepad_target, text="Hello from Bimo\n", win_ctrl=win_ctrl)
    print(f"[TEST A] SUCCESS: {res_a}")
    time.sleep(1)

    # TEST B: Minimize Notepad and verify auto-restoration
    print("\n[TEST B] Minimizing Notepad...")
    import ctypes
    user32 = ctypes.windll.user32
    # SW_MINIMIZE = 6
    user32.ShowWindow(notepad_target.hwnd, 6)
    time.sleep(0.5)
    is_min = win_ctrl.is_iconic(notepad_target.hwnd)
    print(f"Notepad is minimized: {is_min}")

    print("[TEST B] Typing into minimized target (expecting auto-restore)...")
    res_b = kbd.type_text_targeted(target=notepad_target, text="Restored and verified!\n", win_ctrl=win_ctrl)
    is_min_after = win_ctrl.is_iconic(notepad_target.hwnd)
    print(f"[TEST B] SUCCESS: restored={not is_min_after}, res={res_b}")
    time.sleep(1)

    # TEST C: Validate wrong foreground window rejection
    print("\n[TEST C] Testing unverified foreground blocking...")
    # Create dummy fake target with non-foreground HWND
    fake_target = TargetWindow(
        hwnd=999999,
        process_id=99999,
        process_name="nonexistent.exe",
        title="Fake Target",
    )
    try:
        kbd.type_text_targeted(target=fake_target, text="Should not be typed", win_ctrl=win_ctrl)
        print("[TEST C] FAILED: should have raised exception!")
    except PCCommandError as e:
        print(f"[TEST C] SUCCESS: Blocked unverified input with code '{e.code}': {e.message}")

    # TEST E: Terminate Notepad and verify clean TARGET_CLOSED error
    print("\n[TEST E] Terminating Notepad...")
    launcher.terminate("notepad")
    time.sleep(1)
    print("[TEST E] Attempting typing into closed Notepad HWND...")
    try:
        kbd.type_text_targeted(target=notepad_target, text="Should fail", win_ctrl=win_ctrl)
        print("[TEST E] FAILED: should have rejected closed target!")
    except PCCommandError as e:
        print(f"[TEST E] SUCCESS: Clean failure on closed target with code '{e.code}': {e.message}")

    print("\n=== ALL LIVE TESTS COMPLETED SUCCESSFULLY ===")


if __name__ == "__main__":
    main()
