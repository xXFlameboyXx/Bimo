"""Performance benchmarking script for Bimo physical LCD rendering."""

from pathlib import Path
import resource
import sys
import time

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.rendering.physical_lcd import PhysicalFaceRenderer


def benchmark() -> None:
    print("Initializing PhysicalFaceRenderer for performance measurement...")
    renderer = PhysicalFaceRenderer(fb_device="/dev/fb1")
    renderer.initialize()

    rss_init_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print(f"Memory after sprite pre-caching: {rss_init_mb:.2f} MB")

    # Benchmark continuous rendering at 30 FPS for 6 seconds (180 frames)
    frames = 180
    target_interval = 1.0 / 30.0

    t_wall_start = time.perf_counter()
    t_cpu_start = time.process_time()

    for _ in range(frames):
        t_frame_start = time.perf_counter()
        renderer.render_frame()
        t_frame_elapsed = time.perf_counter() - t_frame_start
        sleep_dur = max(0.0, target_interval - t_frame_elapsed)
        if sleep_dur > 0:
            time.sleep(sleep_dur)

    t_wall_total = time.perf_counter() - t_wall_start
    t_cpu_total = time.process_time() - t_cpu_start

    cpu_usage_pct = (t_cpu_total / t_wall_total) * 100.0
    actual_fps = frames / t_wall_total
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    print("\n--- BIMO PHYSICAL LCD BENCHMARK RESULTS ---")
    print(f"Hardware            : Raspberry Pi 3 Model A+ (Quad-core Cortex-A53)")
    print(f"Display Node        : /dev/fb1 (ILI9486 over SPI)")
    print(f"Test Duration       : {t_wall_total:.2f} s ({frames} frames)")
    print(f"Target FPS          : 30.0 FPS")
    print(f"Actual FPS          : {actual_fps:.2f} FPS")
    print(f"Total CPU Time      : {t_cpu_total:.4f} s across {frames} frames")
    print(f"Average CPU Usage   : {cpu_usage_pct:.2f}% (Out of 400% quad-core / 100% single core)")
    print(f"Peak RAM Footprint  : {peak_rss_mb:.2f} MB (Only ~{peak_rss_mb / 512 * 100:.1f}% of 512MB RAM)")
    print("-------------------------------------------\n")

    renderer.close()


if __name__ == "__main__":
    benchmark()
