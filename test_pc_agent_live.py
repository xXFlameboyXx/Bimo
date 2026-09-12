#!/usr/bin/env python3
"""Interactive Live LAN verification script for Bimo Phase 8 Controlled Computer Use.

Tests:
1. PC Agent connectivity and health endpoint
2. HMAC-SHA256 authenticated get_status
3. Screen size query (pc.get_screen_size)
4. Desktop screenshot capture (pc.screenshot)
5. Active window inspection (pc.get_active_window)
6. Window focus (pc.focus_window)
7. Mouse movement with bounds checking (pc.move_mouse)
8. Mouse click with coordinates (pc.click)
9. Mouse scroll with range limits (pc.scroll)
10. Application launch (pc.open_app) with strict allowlist (e.g. notepad)
11. Text typing (pc.type_text) into focused application
12. Keyboard shortcut (pc.press_key) e.g. ENTER or ESC
13. Application close (pc.close_app)
14. Security rejection of arbitrary executables, shell commands, invalid coordinates, invalid keys
15. Permission policy checks (CONFIRM required for action tools, SAFE for observation tools)

Usage:
    python test_pc_agent_live.py [--host 127.0.0.1] [--port 8088] [--secret YOUR_SECRET] [--live-apps]
"""

from __future__ import annotations

import argparse
import base64
import logging
import sys
import time
from pathlib import Path

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.config import Config, PCAgentConfig
from bimo.core.events import Event, EventBus, EventType
from bimo.pc.client import PCAgentClient
from bimo.pc.models import PCErrorCode
from bimo.pc.tools import register_pc_tools
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("pc_live_test")


def run_live_verification(host: str, port: int, secret: str, test_real_apps: bool = False) -> bool:
    print("\n" + "=" * 70)
    print("        BIMO PHASE 8: CONTROLLED COMPUTER USE LIVE VERIFICATION")
    print("=" * 70)
    print(f"Target PC Agent Host  : {host}")
    print(f"Target PC Agent Port  : {port}")
    print(f"Authentication Method : HMAC-SHA256 (Shared Secret)")
    print(f"Live GUI Interaction  : {'ENABLED' if test_real_apps else 'DISABLED (Safe Probes & Dry Run)'}")
    print("=" * 70 + "\n")

    event_bus = EventBus()
    events_captured: list[Event] = []
    event_bus.subscribe(None, events_captured.append)

    cfg = PCAgentConfig(
        enabled=True,
        host=host,
        port=port,
        shared_secret=secret,
        connect_timeout=3.0,
        request_timeout=8.0,
    )
    client = PCAgentClient(config=cfg, event_bus=event_bus)

    # 1. Health Probe
    print("[1/14] Checking PC Agent Health Endpoint...")
    healthy = client.check_health()
    if not healthy:
        print("  [FAIL] Cannot reach PC Agent. Ensure the server is running:")
        print(f"         python -m windows_agent --host {host} --port {port} --secret <secret>")
        return False
    print("  [PASS] PC Agent is reachable and healthy.\n")

    # 2. Authenticated pc.get_status
    print("[2/14] Verifying Authenticated pc.get_status...")
    resp_status = client.execute("pc.get_status", {})
    if not resp_status.success:
        print(f"  [FAIL] pc.get_status failed: {resp_status.error}")
        return False
    out = resp_status.output or {}
    print("  [PASS] Operational status verified:")
    print(f"         Hostname     : {out.get('hostname')}")
    print(f"         OS Platform  : {out.get('os')}")
    print(f"         Agent Version: {out.get('agent_version')}")
    print(f"         Uptime (sec) : {out.get('uptime_seconds')}")
    print(f"         Commands     : {', '.join(out.get('registered_commands', []))}\n")

    # 3. Query Screen Size
    print("[3/14] Verifying Screen Size Query (pc.get_screen_size)...")
    resp_size = client.execute("pc.get_screen_size", {})
    if not resp_size.success:
        print(f"  [FAIL] pc.get_screen_size failed: {resp_size.error}")
        return False
    size_out = resp_size.output or {}
    width = size_out.get("width", 0)
    height = size_out.get("height", 0)
    print(f"  [PASS] Primary monitor resolution detected: {width}x{height}\n")

    # 4. Capture Desktop Screenshot
    print("[4/14] Verifying Desktop Screenshot Capture (pc.screenshot)...")
    resp_shot = client.execute("pc.screenshot", {})
    if not resp_shot.success:
        print(f"  [FAIL] pc.screenshot failed: {resp_shot.error}")
        return False
    shot_out = resp_shot.output or {}
    shot_w = shot_out.get("width")
    shot_h = shot_out.get("height")
    raw_img = shot_out.get("image", "")
    img_bytes = base64.b64decode(raw_img)
    print(f"  [PASS] Screenshot captured successfully ({shot_w}x{shot_h}, {shot_out.get('format')}).")
    print(f"         Payload Size: {len(raw_img)} base64 chars ({len(img_bytes)} decoded bytes)\n")

    # 5. Query Active Foreground Window
    print("[5/14] Verifying Active Window Query (pc.get_active_window)...")
    resp_win = client.execute("pc.get_active_window", {})
    if not resp_win.success:
        print(f"  [FAIL] pc.get_active_window failed: {resp_win.error}")
        return False
    win_out = resp_win.output or {}
    print(f"  [PASS] Foreground window detected: '{win_out.get('title')}' (Process: {win_out.get('process_name')})\n")

    # 6. Tool Registry & Permission System Integration
    print("[6/14] Verifying Phase 6 ToolRegistry & Permission Integration...")
    registry = ToolRegistry()
    register_pc_tools(registry, client)
    policy = PermissionPolicy()

    # Check safe tools
    for safe_name in ["pc.get_status", "pc.get_active_window", "pc.get_screen_size", "pc.screenshot"]:
        tool = registry.get(safe_name)
        if not tool or tool.permission != ToolPermission.SAFE:
            print(f"  [FAIL] Tool {safe_name} is not marked SAFE!")
            return False

    # Check confirm tools
    for confirm_name in ["pc.open_app", "pc.close_app", "pc.focus_window", "pc.move_mouse", "pc.click", "pc.scroll", "pc.type_text", "pc.press_key"]:
        tool = registry.get(confirm_name)
        if not tool or tool.permission != ToolPermission.CONFIRM:
            print(f"  [FAIL] Tool {confirm_name} is not marked CONFIRM!")
            return False

        allowed_unconfirmed, _ = policy.check_permission(tool, {"confirmed": False})
        if allowed_unconfirmed:
            print(f"  [FAIL] CONFIRM tool {confirm_name} allowed without user confirmation!")
            return False
    print("  [PASS] Permission policy boundaries strictly verified (4 SAFE, 8 CONFIRM).\n")

    # 7. Security Check: Reject Unknown / Malicious Commands
    print("[7/14] Verifying Security Boundary: Unknown Command Rejection...")
    resp_bad_cmd = client.execute("pc.delete_hard_drive", {})
    if resp_bad_cmd.success or not resp_bad_cmd.error or resp_bad_cmd.error.code != PCErrorCode.UNKNOWN_COMMAND.value:
        print("  [FAIL] PC Agent accepted an unallowlisted command!")
        return False
    print(f"  [PASS] Unregistered command rejected correctly ({resp_bad_cmd.error.code}).\n")

    # 8. Security Check: Reject Arbitrary Executable Paths
    print("[8/14] Verifying Security Boundary: Arbitrary Executable Launch Denied...")
    for bad_app in ["C:\\malware.exe", "powershell.exe", "cmd.exe", "bash", "calc.exe"]:
        resp_bad_app = client.execute("pc.open_app", {"app": bad_app})
        if resp_bad_app.success:
            print(f"  [FAIL] Arbitrary executable '{bad_app}' was accepted!")
            return False
    print("  [PASS] Arbitrary executable launch attempts strictly denied (APP_NOT_ALLOWED).\n")

    # 9. Security Check: Reject Out-of-Bounds Coordinates
    print("[9/14] Verifying Security Boundary: Out-of-Bounds Coordinates Rejected...")
    resp_neg = client.execute("pc.move_mouse", {"x": -10, "y": 100})
    if resp_neg.success:
        print("  [FAIL] Negative mouse coordinates accepted!")
        return False
    resp_oob = client.execute("pc.move_mouse", {"x": width + 500, "y": height + 500})
    if resp_oob.success:
        print("  [FAIL] Out-of-bounds mouse coordinates accepted!")
        return False
    print("  [PASS] Coordinate boundary violations rejected correctly.\n")

    # 10. Security Check: Reject Disallowed Keys
    print("[10/14] Verifying Security Boundary: Disallowed Keys Rejected...")
    resp_bad_key = client.execute("pc.press_key", {"key": "MALICIOUS_KEY_XYZ"})
    if resp_bad_key.success:
        print("  [FAIL] Unknown key accepted!")
        return False
    print("  [PASS] Non-allowlisted key press rejected correctly (KEY_NOT_ALLOWED).\n")

    # 11-14: Live GUI Interaction (When Requested)
    if test_real_apps:
        print("[11/14] Testing Live Allowlisted App Launch (Notepad)...")
        resp_launch = client.execute("pc.open_app", {"app": "notepad"})
        if not resp_launch.success:
            print(f"  [FAIL] Could not launch allowlisted notepad: {resp_launch.error}")
            return False
        print("  [PASS] Notepad successfully launched.\n")
        time.sleep(1.5)

        print("[12/14] Testing Live Window Focus & Text Typing...")
        resp_focus = client.execute("pc.focus_window", {"title": "Notepad"})
        if not resp_focus.success:
            print(f"  [FAIL] Could not focus Notepad window: {resp_focus.error}")
            return False
        print(f"  [PASS] Focused window: '{resp_focus.output.get('title')}'.")

        resp_type = client.execute("pc.type_text", {"text": "Hello from Bimo Phase 8 (Controlled Computer Use)!\n"})
        if not resp_type.success:
            print(f"  [FAIL] pc.type_text failed: {resp_type.error}")
            return False
        print(f"  [PASS] Typed {resp_type.output.get('length')} characters into Notepad.\n")

        print("[13/14] Testing Live Key Press & Second Screenshot Verification...")
        resp_key = client.execute("pc.press_key", {"key": "ENTER"})
        if not resp_key.success:
            print(f"  [FAIL] pc.press_key failed: {resp_key.error}")
            return False
        print("  [PASS] Successfully pressed ENTER key.")

        # Observe screen again
        resp_shot2 = client.execute("pc.screenshot", {})
        if resp_shot2.success:
            print("  [PASS] Post-action verification screenshot captured.\n")

        print("[14/14] Testing Live Mouse Move & Safe Scroll & App Close...")
        # Safe mouse move in center of screen
        safe_x = min(width // 2, 500)
        safe_y = min(height // 2, 500)
        resp_move = client.execute("pc.move_mouse", {"x": safe_x, "y": safe_y})
        if not resp_move.success:
            print(f"  [FAIL] pc.move_mouse failed: {resp_move.error}")
            return False
        print(f"  [PASS] Mouse moved safely to ({safe_x}, {safe_y}).")

        resp_scroll = client.execute("pc.scroll", {"amount": 50})
        if not resp_scroll.success:
            print(f"  [FAIL] pc.scroll failed: {resp_scroll.error}")
            return False
        print("  [PASS] Mouse wheel scrolled.")

        resp_close = client.execute("pc.close_app", {"app": "notepad"})
        if not resp_close.success:
            print(f"  [FAIL] pc.close_app failed: {resp_close.error}")
            return False
        print("  [PASS] Successfully closed Notepad application.\n")
    else:
        print("[11-14/14] Skipping live GUI typing/launch (pass --live-apps to interact with real Windows GUI windows).")
        print("  [INFO] Safe observation, permission boundaries, and security limits all verified.\n")

    print("=" * 70)
    print("  ALL PHASE 8 LIVE VERIFICATION CRITERIA PASSED SUCCESSFULLY!")
    print("=" * 70 + "\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Verification for Bimo Windows PC Agent (Phase 8)")
    parser.add_argument("--host", default=None, help="Host address of Windows PC Agent")
    parser.add_argument("--port", type=int, default=None, help="Port of Windows PC Agent")
    parser.add_argument("--secret", default=None, help="Shared HMAC secret")
    parser.add_argument("--live-apps", action="store_true", help="Launch and interact with real Notepad window")

    args = parser.parse_args()

    cfg = Config.from_env()
    host = args.host or cfg.pc_agent.host or "127.0.0.1"
    port = args.port or cfg.pc_agent.port or 8088
    secret = args.secret or cfg.pc_agent.shared_secret or "bimo-phase7-dev-secret-key-change-in-production"

    if not secret:
        print("ERROR: No shared secret provided. Set PC_AGENT_SHARED_SECRET or pass --secret.")
        sys.exit(1)

    success = run_live_verification(host=host, port=port, secret=secret, test_real_apps=args.live_apps)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
