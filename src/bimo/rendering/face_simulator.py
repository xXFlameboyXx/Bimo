"""Windows desktop face simulator for Bimo using Tkinter.

Loads and renders the actual robot face sprites from the 'faces' directory
with animated frame cycles for dynamic states (listening, thinking, speaking).
Supports sub-categories in IDLE state with automatic timed rotation across
different idle animations (blinking, looking around, smiling, glancing).
Renders ONLY the pure face on the screen without any HUD, text, or overlays.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
import time
import tkinter as tk
from collections.abc import Callable
from typing import Any

from bimo.core.state import RobotState
from bimo.rendering.base import BaseFaceRenderer

logger = logging.getLogger(__name__)

# Try to import PIL for high-quality resizing if needed
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass(frozen=True)
class IdleSubCategoryConfig:
    """Timing configuration for an individual IDLE sub-category.

    Attributes:
        duration: Total duration (in seconds) this sub-category remains active before rotating.
        frame_interval: Speed (in seconds) between image frames inside this sub-category.
    """

    duration: float = 300.0
    frame_interval: float = 0.80


class FaceSimulator(BaseFaceRenderer):
    """Tkinter-based face simulator implementing BaseFaceRenderer.

    Displays ONLY the robot face sprites (800x480 native).
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

    # Custom timing per IDLE sub-category:
    # duration: How long (seconds) this sub-category stays active before rotating
    # frame_interval: How fast (seconds) the frames repeat inside this sub-category
    IDLE_SUBCATEGORY_CONFIGS: dict[str, IdleSubCategoryConfig] = {
        "01_blink": IdleSubCategoryConfig(duration=24.0, frame_interval=0.80),
        "02_look": IdleSubCategoryConfig(duration=3.2, frame_interval=0.80),
        "03_sleep": IdleSubCategoryConfig(duration=240.0, frame_interval=0.80),
        "04_glance": IdleSubCategoryConfig(duration=4.8, frame_interval=0.80),
    }

    # Default fallback for any newly added sub-category not explicitly listed
    DEFAULT_IDLE_CATEGORY_CONFIG = IdleSubCategoryConfig(duration=300.0, frame_interval=0.80)

    def __init__(
        self,
        width: int = 800,
        height: int = 480,
        title: str = "Bimo",
        faces_dir: Path | str | None = None,
        idle_rotation_time: float = 300.0,
        idle_category_configs: dict[str, IdleSubCategoryConfig] | None = None,
        on_key_press: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(width, height)
        self.title = title
        self.idle_rotation_time = idle_rotation_time
        self.idle_category_configs = (
            dict(self.IDLE_SUBCATEGORY_CONFIGS)
            if idle_category_configs is None
            else dict(idle_category_configs)
        )
        self.on_key_press = on_key_press

        # Resolve faces folder path
        if faces_dir is not None:
            self.faces_dir = Path(faces_dir)
        else:
            candidates = [
                Path("faces"),
                Path(__file__).resolve().parent.parent.parent.parent / "faces",
            ]
            self.faces_dir = next((c for c in candidates if c.is_dir()), Path("faces"))

        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._frame_count = 0
        self._start_time = time.time()
        self._last_state_change = time.time()

        # Cache of loaded PhotoImage objects per state: dict[RobotState, list[tk.PhotoImage]]
        self._face_images: dict[RobotState, list[Any]] = {}
        # Keep references to prevent garbage collection in Tkinter
        self._image_refs: list[Any] = []

        # IDLE sub-categories: list of (category_name, list_of_frame_photos)
        self._idle_categories: list[tuple[str, list[Any]]] = []
        self._current_idle_category_idx: int = 0
        self._last_idle_rotation: float = time.time()

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
        """Create Tkinter window, canvas, and load face sprite frames."""
        self._root = tk.Tk()
        self._root.title(self.title)
        self._root.geometry(f"{self.width}x{self.height}")
        self._root.resizable(False, False)
        self._root.configure(bg="#000000")

        # Pure face canvas with zero borders or highlight rings
        self._canvas = tk.Canvas(
            self._root,
            width=self.width,
            height=self.height,
            bg="#000000",
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)

        # Connect keyboard events for manual control/testing
        if self.on_key_press:
            self._root.bind("<Key>", self._handle_key)

        # Load face sprites and idle sub-categories
        self._load_face_sprites()

        self._is_running = True
        self._last_state_change = time.time()
        self._last_idle_rotation = time.time()

    def _load_face_sprites(self) -> None:
        """Load all PNG frames for each state and discover IDLE sub-categories."""
        self._face_images.clear()
        self._image_refs.clear()
        self._idle_categories.clear()

        logger.info("Loading face sprites from: %s", self.faces_dir.resolve())

        # 1. Load standard state folders
        for state, folder_name in self.STATE_FOLDER_MAP.items():
            if state == RobotState.IDLE:
                continue  # Special loading below

            folder_path = self.faces_dir / folder_name
            frames: list[Any] = []

            if folder_path.is_dir():
                png_files = sorted(folder_path.glob("*.png"))
                for png_path in png_files:
                    try:
                        photo = self._load_single_image(png_path)
                        frames.append(photo)
                        self._image_refs.append(photo)
                    except Exception as e:
                        logger.error("Failed to load image %s: %s", png_path, e)

            if not frames:
                logger.warning("No face frames found for state %s in %s", state.value, folder_path)

            self._face_images[state] = frames

        # 2. Discover IDLE sub-categories
        self._load_idle_subcategories()

    def _load_idle_subcategories(self) -> None:
        """Load IDLE sub-categories from subdirectories or group flat files."""
        idle_folder = self.faces_dir / "idle"
        if not idle_folder.is_dir():
            logger.warning("IDLE folder not found at %s", idle_folder)
            return

        # Check for subdirectories in faces/idle/ (e.g. 01_blink, 02_look, etc.)
        subdirs = sorted([d for d in idle_folder.iterdir() if d.is_dir()])

        if subdirs:
            for subdir in subdirs:
                png_files = sorted(subdir.glob("*.png"))
                cat_frames: list[Any] = []
                for png_path in png_files:
                    try:
                        photo = self._load_single_image(png_path)
                        cat_frames.append(photo)
                        self._image_refs.append(photo)
                    except Exception as e:
                        logger.error("Failed to load %s: %s", png_path, e)

                if cat_frames:
                    self._idle_categories.append((subdir.name, cat_frames))
                    logger.info("Loaded IDLE sub-category '%s' with %d frames", subdir.name, len(cat_frames))

        else:
            # Fallback for flat files in faces/idle/
            png_files = sorted(idle_folder.glob("*.png"))
            if png_files:
                # Group files in chunks of 3 if multiple of 3, otherwise 1 per group
                chunk_size = 3 if len(png_files) >= 6 and len(png_files) % 3 == 0 else len(png_files)
                for i in range(0, len(png_files), chunk_size):
                    chunk = png_files[i:i + chunk_size]
                    cat_name = f"idle_group_{i // chunk_size + 1}"
                    cat_frames = []
                    for png_path in chunk:
                        try:
                            photo = self._load_single_image(png_path)
                            cat_frames.append(photo)
                            self._image_refs.append(photo)
                        except Exception as e:
                            logger.error("Failed to load %s: %s", png_path, e)
                    if cat_frames:
                        self._idle_categories.append((cat_name, cat_frames))

        # Set default idle frames to the first category's frames
        if self._idle_categories:
            self._face_images[RobotState.IDLE] = self._idle_categories[0][1]
        else:
            self._face_images[RobotState.IDLE] = []

    def _load_single_image(self, path: Path) -> Any:
        """Load and optionally resize a single PNG image."""
        if HAS_PIL and (self.width != 800 or self.height != 480):
            pil_img = Image.open(path)
            if pil_img.size != (self.width, self.height):
                pil_img = pil_img.resize((self.width, self.height), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(pil_img)
        else:
            return tk.PhotoImage(file=str(path))

    def _handle_key(self, event: tk.Event) -> None:
        """Forward raw keyboard events to the registered callback."""
        if self.on_key_press:
            key_char = event.char.lower() if event.char else ""
            key_sym = event.keysym.lower()
            key = key_char if key_char else key_sym
            self.on_key_press(key)

    def set_state(self, state: RobotState) -> None:
        """Update active face state and reset timers on transition."""
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
        """Return the frame animation interval (in seconds) for a given IDLE sub-category."""
        if cat_name in self.idle_category_configs:
            return self.idle_category_configs[cat_name].frame_interval
        return self.FRAME_INTERVALS.get(RobotState.IDLE, 0.80)

    def rotate_idle_category(self, forward: bool = True) -> str:
        """Manually rotate to the next/previous IDLE sub-category."""
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
            "IDLE sub-category rotated to: %s (%d/%d) [duration: %.1fs, frame_interval: %.2fs]",
            cat_name,
            self._current_idle_category_idx + 1,
            len(self._idle_categories),
            duration,
            interval,
        )
        return cat_name

    def display_status(self, text: str) -> None:
        """Log status internally without displaying any text on the face canvas."""
        self._status_text = text
        logger.debug("Face status: %s", text)

    def render_frame(self) -> None:
        """Draw ONLY the face on the canvas with sub-category rotation in IDLE."""
        if not self._canvas or not self._is_running:
            return

        self._frame_count += 1
        canvas = self._canvas
        now = time.time()

        # Clear canvas
        canvas.delete("all")

        # -------------------------------------------------------------
        # IDLE State with Sub-Category Rotation System
        # -------------------------------------------------------------
        if self._current_state == RobotState.IDLE and self._idle_categories:
            cat_name, frames = self._idle_categories[self._current_idle_category_idx]
            cat_duration = self.get_idle_category_duration(cat_name)
            cat_interval = self.get_idle_category_frame_interval(cat_name)

            # Check if this specific category's duration has elapsed
            if now - self._last_idle_rotation >= cat_duration:
                old_idx = self._current_idle_category_idx
                self._current_idle_category_idx = (
                    self._current_idle_category_idx + 1
                ) % len(self._idle_categories)
                self._last_idle_rotation = now
                cat_name, frames = self._idle_categories[self._current_idle_category_idx]
                cat_duration = self.get_idle_category_duration(cat_name)
                cat_interval = self.get_idle_category_frame_interval(cat_name)
                logger.info(
                    "IDLE rotated: %s -> %s [duration: %.1fs, frame_interval: %.2fs]",
                    self._idle_categories[old_idx][0],
                    cat_name,
                    cat_duration,
                    cat_interval,
                )

            num_frames = len(frames)
            if num_frames == 1:
                current_frame = frames[0]
            else:
                category_elapsed = now - self._last_idle_rotation
                frame_idx = int(category_elapsed / cat_interval) % num_frames
                current_frame = frames[frame_idx]

            canvas.create_image(
                self.width // 2,
                self.height // 2,
                image=current_frame,
            )
            return

        # -------------------------------------------------------------
        # Other States (listening, thinking, speaking, capturing, etc.)
        # -------------------------------------------------------------
        frames = self._face_images.get(self._current_state, [])
        if not frames:
            # Fallback to first idle frames if state frames are missing
            frames = (
                self._idle_categories[0][1]
                if self._idle_categories
                else self._face_images.get(RobotState.IDLE, [])
            )

        if frames:
            num_frames = len(frames)
            if num_frames == 1:
                frame_idx = 0
            else:
                interval = self.FRAME_INTERVALS.get(self._current_state, 0.20)
                state_elapsed = now - self._last_state_change
                frame_idx = int(state_elapsed / interval) % num_frames

            current_frame = frames[frame_idx]
            canvas.create_image(
                self.width // 2,
                self.height // 2,
                image=current_frame,
            )
        else:
            # Empty black screen if no sprites loaded
            canvas.create_rectangle(
                0, 0, self.width, self.height, fill="#000000", outline=""
            )

    def run_loop(self, fps: int = 40) -> None:
        """Start the Tkinter rendering loop with continuous frame updates."""
        if not self._root:
            raise RuntimeError("Renderer not initialized. Call initialize() first.")

        delay_ms = max(10, int(1000 / fps))

        def _tick() -> None:
            if self._is_running and self._root:
                self.render_frame()
                self._root.after(delay_ms, _tick)

        _tick()
        self._root.mainloop()

    def close(self) -> None:
        """Shut down the simulator window."""
        self._is_running = False
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None
            self._canvas = None
            self._face_images.clear()
            self._idle_categories.clear()
            self._image_refs.clear()
