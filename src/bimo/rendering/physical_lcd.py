"""Physical LCD face renderer for Raspberry Pi 3.5" TFT display.

Writes directly to Linux framebuffer /dev/fb1 (ILI9486 over SPI via fbtft)
using zero-copy / zero-CPU memory-mapped I/O (mmap).

All robot face sprites are pre-scaled to the native LCD resolution (480x320)
and pre-converted into 16-bit RGB565 byte buffers at initialization.
During runtime animation cycles, rendering a frame is a single memory blit
(fb[:] = cached_bytes), consuming < 0.5% CPU on Raspberry Pi 3A+.

Fully implements BaseFaceRenderer, preserving complete decoupling from the
robot state machine and event bus.
"""

from __future__ import annotations

import logging
import mmap
import os
from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any

from bimo.core.process_lock import acquire_display_lock, release_display_lock
from bimo.core.state import RobotState
from bimo.rendering.base import BaseFaceRenderer

logger = logging.getLogger(__name__)

# Try to import PIL for image pre-processing and diagnostic rendering
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Try to import NumPy for vectorised RGB565 conversion
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


@dataclass(frozen=True)
class IdleSubCategoryConfig:
    """Timing configuration for an individual IDLE sub-category.

    Attributes:
        duration: Total duration (in seconds) this sub-category remains active before rotating.
        frame_interval: Speed (in seconds) between image frames inside this sub-category.
    """

    duration: float = 300.0
    frame_interval: float = 0.80


def rgb_to_rgb565(r: int, g: int, b: int) -> int:
    """Convert 24-bit RGB (8-8-8) to 16-bit RGB565 integer."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def pil_to_rgb565_bytes(image: Any, width: int, height: int) -> bytes:
    """Convert a PIL Image to 16-bit little-endian RGB565 raw bytes.

    Resizes image to (width, height) and composites onto black background
    if transparency is present.
    """
    if not HAS_PIL:
        raise RuntimeError("Pillow is required for image pre-processing")

    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)

    # Ensure RGB mode on black background
    if image.mode != "RGB":
        bg = Image.new("RGB", (width, height), (0, 0, 0))
        if image.mode == "RGBA":
            bg.paste(image, mask=image.split()[3])
        else:
            bg.paste(image.convert("RGB"))
        image = bg

    if HAS_NUMPY:
        arr = np.array(image, dtype=np.uint16)
        r = arr[:, :, 0]
        g = arr[:, :, 1]
        b = arr[:, :, 2]
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        # ILI9486 framebuffer on Linux expects little-endian uint16 (<u2)
        return rgb565.astype("<u2").tobytes()
    else:
        # Fallback pure-Python byte encoding
        pixels = list(image.getdata())
        out = bytearray(width * height * 2)
        idx = 0
        for r, g, b in pixels:
            val = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out[idx] = val & 0xFF
            out[idx + 1] = (val >> 8) & 0xFF
            idx += 2
        return bytes(out)


class PhysicalFaceRenderer(BaseFaceRenderer):
    """Physical 3.5" TFT LCD renderer for Raspberry Pi ILI9486 framebuffer (/dev/fb1).

    Maintains full compatibility with BaseFaceRenderer.
    Can also operate in mock mode for unit testing and development off-Pi.
    """

    # Mapping from RobotState to subfolder in faces/
    STATE_FOLDER_MAP: dict[RobotState, str] = {
        RobotState.IDLE: "idle",
        RobotState.LISTENING: "listening",
        RobotState.THINKING: "thinking",
        RobotState.EXECUTING: "capturing",
        RobotState.SPEAKING: "speaking",
        RobotState.SUCCESS: "speaking",
        RobotState.ERROR: "error",
        RobotState.SLEEPING: "warmup",
    }

    # Frame cycle intervals (in seconds) for standard states
    FRAME_INTERVALS: dict[RobotState, float] = {
        RobotState.IDLE: 0.80,
        RobotState.THINKING: 0.35,
        RobotState.SPEAKING: 0.12,
        RobotState.LISTENING: 0.35,
        RobotState.SUCCESS: 0.20,
    }

    # Default timing per IDLE sub-category
    IDLE_SUBCATEGORY_CONFIGS: dict[str, IdleSubCategoryConfig] = {
        "01_blink": IdleSubCategoryConfig(duration=300.0, frame_interval=0.80),
        "02_look": IdleSubCategoryConfig(duration=300.0, frame_interval=0.80),
        "03_sleep": IdleSubCategoryConfig(duration=300.0, frame_interval=0.80),
        "04_glance": IdleSubCategoryConfig(duration=300.0, frame_interval=0.80),
    }

    DEFAULT_IDLE_CATEGORY_CONFIG = IdleSubCategoryConfig(duration=300.0, frame_interval=0.80)

    def __init__(
        self,
        width: int = 480,
        height: int = 320,
        fb_device: str = "/dev/fb1",
        faces_dir: Path | str | None = None,
        idle_rotation_time: float = 300.0,
        idle_category_configs: dict[str, IdleSubCategoryConfig] | None = None,
        mock: bool = False,
        clear_on_close: bool = True,
    ) -> None:
        super().__init__(width=width, height=height)
        self.fb_device = fb_device
        self.idle_rotation_time = idle_rotation_time
        self.idle_category_configs = (
            dict(self.IDLE_SUBCATEGORY_CONFIGS)
            if idle_category_configs is None
            else dict(idle_category_configs)
        )
        self.mock = mock
        self.clear_on_close = clear_on_close

        # Framebuffer sizing (16 bpp RGB565 -> 2 bytes per pixel)
        self.bytes_per_pixel = 2
        self.buffer_size = self.width * self.height * self.bytes_per_pixel

        # Resolve faces folder path
        if faces_dir is not None:
            self.faces_dir = Path(faces_dir)
        else:
            candidates = [
                Path("faces"),
                Path(__file__).resolve().parent.parent.parent.parent / "faces",
            ]
            self.faces_dir = next((c for c in candidates if c.is_dir()), Path("faces"))

        # Hardware handles
        self._fb_fd: int | None = None
        self._fb_mmap: mmap.mmap | None = None
        self._mock_buffer: bytearray | None = None
        self._lock_fd: int | None = None

        # Pre-encoded sprite buffers: dict[RobotState, list[bytes]]
        self._face_buffers: dict[RobotState, list[bytes]] = {}
        # IDLE sub-categories: list of (category_name, list_of_frame_bytes)
        self._idle_categories: list[tuple[str, list[bytes]]] = []
        self._current_idle_category_idx: int = 0
        self._last_idle_rotation: float = time.time()
        self._last_state_change: float = time.time()
        self._frame_count: int = 0

        # Background rendering thread control
        self._render_thread: threading.Thread | None = None
        self._stop_render_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def is_mock(self) -> bool:
        """Return whether renderer is operating on mock buffer or real hardware."""
        return self._mock_buffer is not None

    @property
    def current_idle_category(self) -> str:
        """Return the name of the currently active IDLE sub-category."""
        if self._idle_categories:
            return self._idle_categories[self._current_idle_category_idx][0]
        return "none"

    @property
    def idle_categories_count(self) -> int:
        """Return the number of discovered IDLE sub-categories."""
        return len(self._idle_categories)

    def initialize(self) -> None:
        """Initialize framebuffer connection and pre-cache sprite buffers."""
        with self._lock:
            # 1. Initialize Framebuffer or Mock Buffer
            self._init_framebuffer()

            # 2. Pre-cache all face frames to RGB565 byte buffers
            self._cache_face_sprites()

            self._is_running = True
            self._last_state_change = time.time()
            self._last_idle_rotation = time.time()
            logger.info(
                "PhysicalFaceRenderer initialized (%dx%d, mock=%s, fb=%s)",
                self.width,
                self.height,
                self.is_mock,
                self.fb_device,
            )

    def _init_framebuffer(self) -> None:
        """Open and memory-map the framebuffer device, or fall back to mock."""
        if self.mock:
            logger.info("Operating in explicit mock mode (RAM buffer allocated)")
            self._mock_buffer = bytearray(self.buffer_size)
            return

        fb_path = Path(self.fb_device)
        if not fb_path.exists():
            logger.warning(
                "Framebuffer device %s not found. Falling back to mock RAM buffer.",
                self.fb_device,
            )
            self._mock_buffer = bytearray(self.buffer_size)
            return

        # Acquire exclusive display lock to prevent multiple processes from contending for LCD
        self._lock_fd = acquire_display_lock()

        try:
            fd = os.open(str(fb_path), os.O_RDWR)
            # Memory map the framebuffer
            mapped = mmap.mmap(fd, self.buffer_size, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
            self._fb_fd = fd
            self._fb_mmap = mapped
            logger.info("Successfully memory-mapped framebuffer %s (%d bytes)", self.fb_device, self.buffer_size)
        except Exception as e:
            logger.warning(
                "Could not open framebuffer %s (%s). Falling back to mock RAM buffer.",
                self.fb_device,
                e,
            )
            if self._fb_fd is not None:
                try:
                    os.close(self._fb_fd)
                except Exception:
                    pass
                self._fb_fd = None
            self._mock_buffer = bytearray(self.buffer_size)

    def _write_fb(self, data: bytes | bytearray) -> None:
        """Low-level write of raw RGB565 byte sequence to the active buffer."""
        if self._fb_mmap is not None:
            self._fb_mmap.seek(0)
            self._fb_mmap.write(data)
        elif self._mock_buffer is not None:
            self._mock_buffer[: len(data)] = data

    def _cache_face_sprites(self) -> None:
        """Pre-scale and convert all PNG face sprites to RGB565 buffers."""
        self._face_buffers.clear()
        self._idle_categories.clear()

        if not self.faces_dir.is_dir():
            logger.warning("Faces directory not found: %s", self.faces_dir)
            return

        logger.info("Pre-caching face sprites from: %s", self.faces_dir.resolve())

        # 1. Standard state folders
        for state, folder_name in self.STATE_FOLDER_MAP.items():
            if state == RobotState.IDLE:
                continue

            folder_path = self.faces_dir / folder_name
            buffers: list[bytes] = []

            if folder_path.is_dir():
                png_files = sorted(folder_path.glob("*.png"))
                for png_path in png_files:
                    try:
                        img = Image.open(png_path)
                        buf = pil_to_rgb565_bytes(img, self.width, self.height)
                        buffers.append(buf)
                    except Exception as e:
                        logger.error("Failed to pre-cache sprite %s: %s", png_path, e)

            self._face_buffers[state] = buffers
            logger.debug("State %s: cached %d frames", state.value, len(buffers))

        # 2. Discover IDLE sub-categories
        idle_folder = self.faces_dir / "idle"
        if idle_folder.is_dir():
            subdirs = sorted([d for d in idle_folder.iterdir() if d.is_dir()])
            if subdirs:
                for subdir in subdirs:
                    png_files = sorted(subdir.glob("*.png"))
                    cat_buffers: list[bytes] = []
                    for png_path in png_files:
                        try:
                            img = Image.open(png_path)
                            buf = pil_to_rgb565_bytes(img, self.width, self.height)
                            cat_buffers.append(buf)
                        except Exception as e:
                            logger.error("Failed to pre-cache %s: %s", png_path, e)
                    if cat_buffers:
                        self._idle_categories.append((subdir.name, cat_buffers))
                        logger.info(
                            "Cached IDLE sub-category '%s' (%d frames)",
                            subdir.name,
                            len(cat_buffers),
                        )
            else:
                # Flat files in faces/idle/
                png_files = sorted(idle_folder.glob("*.png"))
                if png_files:
                    chunk_size = 3 if len(png_files) >= 6 and len(png_files) % 3 == 0 else len(png_files)
                    for i in range(0, len(png_files), chunk_size):
                        chunk = png_files[i : i + chunk_size]
                        cat_name = f"idle_group_{i // chunk_size + 1}"
                        cat_buffers = []
                        for png_path in chunk:
                            try:
                                img = Image.open(png_path)
                                buf = pil_to_rgb565_bytes(img, self.width, self.height)
                                cat_buffers.append(buf)
                            except Exception as e:
                                logger.error("Failed to pre-cache %s: %s", png_path, e)
                        if cat_buffers:
                            self._idle_categories.append((cat_name, cat_buffers))

        # Set default IDLE frames to first category
        if self._idle_categories:
            self._face_buffers[RobotState.IDLE] = self._idle_categories[0][1]
        else:
            self._face_buffers[RobotState.IDLE] = []

    def set_state(self, state: RobotState) -> None:
        """Update active face state and reset state-specific timers."""
        with self._lock:
            if state != self._current_state:
                self._current_state = state
                self._last_state_change = time.time()
                if state == RobotState.IDLE:
                    self._last_idle_rotation = time.time()

    def get_idle_category_duration(self, cat_name: str) -> float:
        """Return the active duration (in seconds) for a given IDLE sub-category."""
        if cat_name in self.idle_category_configs:
            return self.idle_category_configs[cat_name].duration
        return self.idle_rotation_time

    def get_idle_category_frame_interval(self, cat_name: str) -> float:
        """Return the frame animation interval (in seconds) for an IDLE sub-category."""
        if cat_name in self.idle_category_configs:
            return self.idle_category_configs[cat_name].frame_interval
        return self.FRAME_INTERVALS.get(RobotState.IDLE, 0.80)

    def rotate_idle_category(self, forward: bool = True) -> str:
        """Rotate to the next/previous IDLE sub-category."""
        with self._lock:
            if not self._idle_categories:
                return "none"

            step = 1 if forward else -1
            self._current_idle_category_idx = (
                self._current_idle_category_idx + step
            ) % len(self._idle_categories)
            self._last_idle_rotation = time.time()

            cat_name = self.current_idle_category
            duration = self.get_idle_category_duration(cat_name)
            interval = self.get_idle_category_frame_interval(cat_name)
            logger.info(
                "IDLE rotated to: %s (%d/%d) [duration: %.1fs, frame_interval: %.2fs]",
                cat_name,
                self._current_idle_category_idx + 1,
                len(self._idle_categories),
                duration,
                interval,
            )
            return cat_name

    def display_status(self, text: str) -> None:
        """Log status internally without polluting the physical face display."""
        self._status_text = text
        logger.debug("Physical face status: %s", text)

    def render_frame(self) -> None:
        """Blit the current state's active animation frame to the LCD framebuffer."""
        if not self._is_running:
            return

        with self._lock:
            self._frame_count += 1
            now = time.time()

            # 1. IDLE State with Sub-Category Rotation System
            if self._current_state == RobotState.IDLE and self._idle_categories:
                cat_name, frames = self._idle_categories[self._current_idle_category_idx]
                cat_duration = self.get_idle_category_duration(cat_name)
                cat_interval = self.get_idle_category_frame_interval(cat_name)

                # Check if this sub-category duration has expired
                if now - self._last_idle_rotation >= cat_duration:
                    self._current_idle_category_idx = (
                        self._current_idle_category_idx + 1
                    ) % len(self._idle_categories)
                    self._last_idle_rotation = now
                    cat_name, frames = self._idle_categories[self._current_idle_category_idx]
                    cat_interval = self.get_idle_category_frame_interval(cat_name)

                num_frames = len(frames)
                if num_frames == 1:
                    target_buffer = frames[0]
                else:
                    category_elapsed = now - self._last_idle_rotation
                    frame_idx = int(category_elapsed / cat_interval) % num_frames
                    target_buffer = frames[frame_idx]

                self._write_fb(target_buffer)
                return

            # 2. Dynamic Robot States (listening, thinking, speaking, capturing, etc.)
            frames = self._face_buffers.get(self._current_state, [])
            if not frames:
                frames = (
                    self._idle_categories[0][1]
                    if self._idle_categories
                    else self._face_buffers.get(RobotState.IDLE, [])
                )

            if frames:
                num_frames = len(frames)
                if num_frames == 1:
                    target_buffer = frames[0]
                else:
                    interval = self.FRAME_INTERVALS.get(self._current_state, 0.20)
                    state_elapsed = now - self._last_state_change
                    frame_idx = int(state_elapsed / interval) % num_frames
                    target_buffer = frames[frame_idx]

                self._write_fb(target_buffer)
            else:
                # Black screen if no frames available
                self.clear(0)

    # -------------------------------------------------------------------------
    # Hardware Diagnostics and Graphical Utilities
    # -------------------------------------------------------------------------

    def clear(self, color: tuple[int, int, int] | int = 0) -> None:
        """Clear the entire LCD screen to a solid color."""
        if isinstance(color, int):
            c565 = color & 0xFFFF
        else:
            c565 = rgb_to_rgb565(color[0], color[1], color[2])

        low = c565 & 0xFF
        high = (c565 >> 8) & 0xFF
        # Create full screen pattern
        pattern = bytes([low, high]) * (self.width * self.height)
        self._write_fb(pattern)
        if self._fb_mmap is not None:
            try:
                self._fb_mmap.flush()
            except Exception:
                pass

    def draw_rect(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        color: tuple[int, int, int] | int,
        fill: bool = True,
    ) -> None:
        """Draw a colored rectangle directly onto the framebuffer."""
        if isinstance(color, int):
            c565 = color & 0xFFFF
        else:
            c565 = rgb_to_rgb565(color[0], color[1], color[2])

        low = c565 & 0xFF
        high = (c565 >> 8) & 0xFF
        pixel_bytes = bytes([low, high])

        # Clamp bounds
        x1 = max(0, min(x, self.width))
        y1 = max(0, min(y, self.height))
        x2 = max(0, min(x + w, self.width))
        y2 = max(0, min(y + h, self.height))

        line_len = x2 - x1
        if line_len <= 0 or y2 <= y1:
            return

        row_data = pixel_bytes * line_len

        # Read active buffer
        cur_buf = bytearray(self.get_buffer())
        for row in range(y1, y2):
            if not fill and y1 < row < y2 - 1:
                # Outline only: draw first and last pixels of the row
                offset1 = (row * self.width + x1) * 2
                offset2 = (row * self.width + (x2 - 1)) * 2
                cur_buf[offset1 : offset1 + 2] = pixel_bytes
                cur_buf[offset2 : offset2 + 2] = pixel_bytes
            else:
                offset = (row * self.width + x1) * 2
                cur_buf[offset : offset + len(row_data)] = row_data

        self._write_fb(cur_buf)

    def draw_text(
        self,
        text: str,
        x: int,
        y: int,
        color: tuple[int, int, int] = (255, 255, 255),
        font_size: int = 20,
    ) -> None:
        """Render text onto the physical screen using PIL."""
        if not HAS_PIL:
            logger.warning("Pillow not installed; skipping draw_text")
            return

        img = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        try:
            # Default or truetype font
            font = ImageFont.load_default(size=font_size)
        except TypeError:
            font = ImageFont.load_default()

        draw.text((x, y), text, fill=color, font=font)
        buf = pil_to_rgb565_bytes(img, self.width, self.height)
        self._write_fb(buf)

    def render_pil_image(self, image: Any) -> None:
        """Render any arbitrary PIL Image to the screen."""
        buf = pil_to_rgb565_bytes(image, self.width, self.height)
        self._write_fb(buf)

    def get_buffer(self) -> bytes:
        """Return the current active raw RGB565 framebuffer content."""
        if self._fb_mmap is not None:
            self._fb_mmap.seek(0)
            return self._fb_mmap.read(self.buffer_size)
        elif self._mock_buffer is not None:
            return bytes(self._mock_buffer)
        return bytes(self.buffer_size)

    # -------------------------------------------------------------------------
    # Threaded and Event Loop Execution
    # -------------------------------------------------------------------------

    def run_loop(self, fps: int = 30, stop_event: threading.Event | None = None) -> None:
        """Execute continuous rendering loop on the calling thread."""
        frame_interval = 1.0 / max(1, fps)
        while self._is_running and (stop_event is None or not stop_event.is_set()):
            t0 = time.monotonic()
            self.render_frame()
            elapsed = time.monotonic() - t0
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)

    def start_render_thread(self, fps: int = 30) -> threading.Thread:
        """Start a background daemon thread executing continuous face rendering."""
        if self._render_thread and self._render_thread.is_alive():
            return self._render_thread

        self._stop_render_event.clear()

        def _thread_target() -> None:
            self.run_loop(fps=fps, stop_event=self._stop_render_event)

        self._render_thread = threading.Thread(
            target=_thread_target, name="PhysicalFaceRendererThread", daemon=True
        )
        self._render_thread.start()
        logger.info("Started background face rendering thread at %d FPS", fps)
        return self._render_thread

    def stop_render_thread(self) -> None:
        """Stop the background rendering thread."""
        self._stop_render_event.set()
        if self._render_thread and self._render_thread.is_alive():
            self._render_thread.join(timeout=1.0)
            self._render_thread = None

    def close(self) -> None:
        """Shut down the renderer, unmap framebuffer, and clean up hardware resources."""
        self.stop_render_thread()
        with self._lock:
            self._is_running = False

            if self.clear_on_close:
                try:
                    self.clear(0)
                    time.sleep(0.05)
                except Exception:
                    pass

            if self._fb_mmap is not None:
                try:
                    self._fb_mmap.flush()
                    self._fb_mmap.close()
                except Exception as e:
                    logger.debug("Error closing mmap: %s", e)
                self._fb_mmap = None

            if self._fb_fd is not None:
                try:
                    if self.clear_on_close:
                        try:
                            os.lseek(self._fb_fd, 0, os.SEEK_SET)
                            os.write(self._fb_fd, bytes(self.buffer_size))
                        except Exception:
                            pass
                    os.close(self._fb_fd)
                except Exception as e:
                    logger.debug("Error closing fd: %s", e)
                self._fb_fd = None

            if self._lock_fd is not None:
                release_display_lock(self._lock_fd)
                self._lock_fd = None

            self._face_buffers.clear()
            self._idle_categories.clear()
            self._mock_buffer = None
            logger.info("PhysicalFaceRenderer closed.")
