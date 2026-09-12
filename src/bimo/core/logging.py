"""Logging infrastructure for Bimo.

Provides structured console output and rotating file logging tailored for
Raspberry Pi (capped file sizes to avoid SD card exhaustion).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bimo.core.config import LoggingConfig

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] (%(name)s) %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(config: LoggingConfig | None = None) -> logging.Logger:
    """Initialize root and application loggers with console and rotating file output.

    Args:
        config: Optional LoggingConfig settings. If None, uses defaults.

    Returns:
        The root logger instance.
    """
    if config is None:
        config = LoggingConfig()

    level = getattr(logging, config.level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid duplicate handlers if setup is called multiple times
    root_logger.handlers.clear()

    formatter = logging.Formatter(fmt=DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT)

    # 1. Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. Rotating file handler (prevents filling up Pi SD card)
    if config.log_to_file:
        log_dir = Path(config.log_dir)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "bimo.log"
            file_handler = RotatingFileHandler(
                filename=str(log_file),
                maxBytes=config.max_bytes,
                backupCount=config.backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            # If filesystem is read-only or error writing log directory, log to console
            root_logger.warning("Could not setup file logger at %s: %s", config.log_dir, e)

    return root_logger
