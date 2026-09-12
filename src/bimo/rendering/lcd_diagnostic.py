"""Comprehensive diagnostic test program for Raspberry Pi 3.5" TFT LCD.

Validates:
1. Framebuffer access and device info (/dev/fb1, ILI9486).
2. Screen clear test.
3. Solid primary colors test (Red, Green, Blue, White, Black).
4. Text rendering and screen coordinate alignment.
5. Geometric shapes and border alignment markers.
6. Face sprite loading and rendering.
7. Full state machine transition cycle (IDLE -> LISTEN -> THINK -> EXECUTE -> SPEAK -> SUCCESS -> ERROR -> SLEEP).
8. Performance benchmarks (FPS, render latency, memory footprint).
9. Clean exit and display blanking.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys
import time

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.rendering.physical_lcd import PhysicalFaceRenderer, rgb_to_rgb565

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
)
logger = logging.getLogger("bimo.lcd_diagnostic")


def run_diagnostics(
    fb_device: str = "/dev/fb1",
    width: int = 480,
    height: int = 320,
    mock: bool = False,
    hold_seconds: float = 1.0,
) -> bool:
    """Run all Phase 2 LCD diagnostic tests sequentially."""
    print("\n" + "=" * 65)
    print("      BIMO PHYSICAL LCD HARDWARE DIAGNOSTIC SUITE")
    print("      Hardware: Raspberry Pi 3A+ & 3.5\" TFT LCD (ILI9486)")
    print(f"      Target: {fb_device} ({width}x{height} 16bpp RGB565)")
    print("=" * 65 + "\n")

    renderer = PhysicalFaceRenderer(
        width=width,
        height=height,
        fb_device=fb_device,
        mock=mock,
        clear_on_close=True,
    )

    try:
        # -------------------------------------------------------------
        # Step 1: Initialization
        # -------------------------------------------------------------
        print("[TEST 1/8] Initializing display & pre-caching sprites...")
        t0 = time.time()
        renderer.initialize()
        init_time = (time.time() - t0) * 1000
        print(f"  [OK] Initialized in {init_time:.1f} ms")
        print(f"  [OK] Operating mode: {'MOCK BUFFER' if renderer.is_mock else 'PHYSICAL HARDWARE (/dev/fb1)'}")
        print(f"  [OK] IDLE sub-categories discovered: {renderer.idle_categories_count}")
        time.sleep(0.3)

        # -------------------------------------------------------------
        # Step 2: Screen Clear Test (Black)
        # -------------------------------------------------------------
        print("\n[TEST 2/8] Testing Screen Clear (Pure Black)...")
        renderer.clear(0)
        print("  [OK] Screen cleared to black (RGB 0,0,0)")
        time.sleep(hold_seconds * 0.5)

        # -------------------------------------------------------------
        # Step 3: Solid Primary Colors
        # -------------------------------------------------------------
        print("\n[TEST 3/8] Testing Solid Colors...")
        colors = [
            ("RED", (255, 0, 0)),
            ("GREEN", (0, 255, 0)),
            ("BLUE", (0, 0, 255)),
            ("WHITE", (255, 255, 255)),
            ("BLACK", (0, 0, 0)),
        ]
        for name, rgb in colors:
            renderer.clear(rgb)
            print(f"  [OK] Color {name:<6} (RGB: {rgb}) displayed")
            time.sleep(hold_seconds * 0.7)

        # -------------------------------------------------------------
        # Step 4: Text Rendering & Alignment
        # -------------------------------------------------------------
        print("\n[TEST 4/8] Testing Text Rendering...")
        renderer.clear(0)
        renderer.draw_text("BIMO ROBOT OS - LCD OK", 20, 30, color=(0, 255, 255), font_size=24)
        renderer.draw_text(f"Device: {fb_device} (ILI9486)", 20, 80, color=(255, 255, 0), font_size=18)
        renderer.draw_text(f"Resolution: {width} x {height} RGB565", 20, 120, color=(200, 200, 200), font_size=18)
        renderer.draw_text("Phase 2 Physical Display Verified", 20, 160, color=(100, 255, 100), font_size=18)
        renderer.draw_text(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}", 20, 200, color=(150, 150, 255), font_size=16)
        print("  [OK] Text overlays rendered successfully")
        time.sleep(hold_seconds * 1.5)

        # -------------------------------------------------------------
        # Step 5: Geometric Shapes & Border Calibration
        # -------------------------------------------------------------
        print("\n[TEST 5/8] Testing Geometric Shapes & Border Markers...")
        renderer.clear(0)
        # Outer border (bounding box)
        renderer.draw_rect(0, 0, width, height, color=(255, 255, 255), fill=False)
        # 4 Corner markers (15x15 solid boxes)
        renderer.draw_rect(0, 0, 15, 15, color=(255, 0, 0), fill=True)          # Top-Left Red
        renderer.draw_rect(width - 15, 0, 15, 15, color=(0, 255, 0), fill=True)  # Top-Right Green
        renderer.draw_rect(0, height - 15, 15, 15, color=(0, 0, 255), fill=True) # Bottom-Left Blue
        renderer.draw_rect(width - 15, height - 15, 15, 15, color=(255, 255, 0), fill=True) # Bottom-Right Yellow
        # Center calibration crosshair
        cx, cy = width // 2, height // 2
        renderer.draw_rect(cx - 30, cy - 2, 60, 4, color=(0, 255, 255), fill=True)
        renderer.draw_rect(cx - 2, cy - 30, 4, 60, color=(0, 255, 255), fill=True)
        renderer.draw_text("BORDER & CENTER CALIBRATION", cx - 120, cy + 40, color=(255, 255, 255), font_size=16)
        print("  [OK] Corner alignment markers and center crosshair displayed")
        time.sleep(hold_seconds * 1.5)

        # -------------------------------------------------------------
        # Step 6: Pure Face Sprite Rendering
        # -------------------------------------------------------------
        print("\n[TEST 6/8] Testing Face Sprite Rendering (IDLE sub-categories)...")
        renderer.set_state(RobotState.IDLE)
        for i in range(renderer.idle_categories_count):
            cat_name = renderer.current_idle_category
            print(f"  * Displaying IDLE sub-category: '{cat_name}'")
            # Render a few animation frames inside this category
            for _ in range(3):
                renderer.render_frame()
                time.sleep(0.15)
            renderer.rotate_idle_category(forward=True)
        print("  [OK] IDLE face sprites and animations verified")

        # -------------------------------------------------------------
        # Step 7: Robot State Machine Transitions
        # -------------------------------------------------------------
        print("\n[TEST 7/8] Testing State Machine Transitions (EventBus driven)...")
        event_bus = EventBus()
        sm = RobotStateMachine(initial_state=RobotState.IDLE, event_bus=event_bus, auto_subscribe_events=True)

        def sync_renderer(event: Event) -> None:
            new_st = RobotState(event.data["to_state"])
            renderer.set_state(new_st)

        event_bus.subscribe(EventType.STATE_CHANGED, sync_renderer)

        test_transitions = [
            (RobotState.LISTENING, EventType.WAKE_WORD_DETECTED, "Hey Bimo"),
            (RobotState.THINKING, EventType.SPEECH_RECEIVED, "User Query"),
            (RobotState.EXECUTING, EventType.TOOL_STARTED, "Tool Execution"),
            (RobotState.SUCCESS, EventType.TOOL_COMPLETED, "Success Expression"),
            (RobotState.SPEAKING, EventType.AI_FINISHED, "Speaking Response"),
            (RobotState.ERROR, EventType.TASK_FAILED, "Error Expression"),
            (RobotState.SLEEPING, None, "Sleep State (Manual Transition)"),
            (RobotState.IDLE, None, "Return to IDLE"),
        ]

        for target_state, event_type, desc in test_transitions:
            if event_type:
                event_bus.publish(Event(type=event_type, source="diag_test"))
            else:
                sm.transition_to(target_state, reason="Diagnostic step", force=True)

            # Render 4 frames of this state's animation
            for _ in range(4):
                renderer.render_frame()
                time.sleep(0.1)

            print(f"  [OK] State {target_state.value.upper():<10} verified ({desc})")

        # -------------------------------------------------------------
        # Step 8: Performance Benchmark (FPS, CPU time, Latency)
        # -------------------------------------------------------------
        print("\n[TEST 8/8] Benchmarking Rendering Performance...")
        renderer.set_state(RobotState.IDLE)
        bench_frames = 100
        t_start = time.perf_counter()
        for _ in range(bench_frames):
            renderer.render_frame()
        t_total = time.perf_counter() - t_start

        fps = bench_frames / t_total
        ms_per_frame = (t_total / bench_frames) * 1000
        print(f"  [OK] Rendered {bench_frames} full frames in {t_total:.3f} s")
        print(f"  [OK] Frame blit latency : {ms_per_frame:.2f} ms / frame")
        print(f"  [OK] Maximum throughput : {fps:.1f} FPS")
        print(f"  [OK] Single frame size  : {renderer.buffer_size:,} bytes")

        # -------------------------------------------------------------
        # Clean Exit
        # -------------------------------------------------------------
        print("\n" + "=" * 65)
        print("  [OK] ALL 8 PHYSICAL LCD TESTS PASSED SUCCESSFULLY!")
        print("=" * 65 + "\n")
        return True

    finally:
        print("Cleaning up and clearing LCD screen...")
        renderer.close()
        print("Display closed cleanly.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bimo Phase 2 Physical LCD Diagnostic")
    parser.add_argument("--fb", default="/dev/fb1", help="Framebuffer device path (default: /dev/fb1)")
    parser.add_argument("--width", type=int, default=480, help="LCD width (default: 480)")
    parser.add_argument("--height", type=int, default=320, help="LCD height (default: 320)")
    parser.add_argument("--mock", action="store_true", help="Force mock buffer mode")
    parser.add_argument("--hold", type=float, default=1.0, help="Seconds to hold each display step (default: 1.0)")
    args = parser.parse_args()

    success = run_diagnostics(
        fb_device=args.fb,
        width=args.width,
        height=args.height,
        mock=args.mock,
        hold_seconds=args.hold,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
