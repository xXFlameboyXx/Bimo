"""Renderer factory for instantiating the appropriate face display backend."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from bimo.core.config import Config, DisplayConfig
from bimo.rendering.base import BaseFaceRenderer
from bimo.rendering.face_simulator import FaceSimulator, IdleSubCategoryConfig
from bimo.rendering.physical_lcd import PhysicalFaceRenderer

logger = logging.getLogger(__name__)


def create_face_renderer(
    config: DisplayConfig | Config | None = None,
    on_key_press: Callable[[str], None] | None = None,
    faces_dir: Path | str | None = None,
    mock: bool = False,
) -> BaseFaceRenderer:
    """Create and return the configured BaseFaceRenderer implementation.

    Selects PhysicalFaceRenderer if backend is 'physical_lcd', 'fb', 'ili9486',
    or 'spi_ili9486'. Otherwise falls back to FaceSimulator (Tkinter).
    """
    if config is None:
        cfg = Config.from_env().display
    elif isinstance(config, Config):
        cfg = config.display
    else:
        cfg = config

    backend = cfg.backend.lower()
    is_physical = backend in ("physical_lcd", "fb", "ili9486", "spi_ili9486")

    idle_configs = {
        name: IdleSubCategoryConfig(
            duration=float(sub.get("duration", cfg.idle_rotation_seconds)),
            frame_interval=float(sub.get("frame_interval", 0.80)),
        )
        for name, sub in cfg.idle_subcategories.items()
    }

    if is_physical:
        logger.info(
            "Creating PhysicalFaceRenderer (%dx%d, fb=%s, mock=%s)",
            cfg.width,
            cfg.height,
            cfg.fb_device,
            mock,
        )
        return PhysicalFaceRenderer(
            width=cfg.width,
            height=cfg.height,
            fb_device=cfg.fb_device,
            faces_dir=faces_dir,
            idle_rotation_time=cfg.idle_rotation_seconds,
            idle_category_configs=idle_configs,
            mock=mock,
        )
    else:
        logger.info(
            "Creating FaceSimulator (%dx%d)",
            cfg.width,
            cfg.height,
        )
        return FaceSimulator(
            width=cfg.width,
            height=cfg.height,
            title="Bimo",
            faces_dir=faces_dir,
            idle_rotation_time=cfg.idle_rotation_seconds,
            idle_category_configs=idle_configs,
            on_key_press=on_key_press,
        )
