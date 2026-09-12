#!/usr/bin/env python3
"""Convenience launcher for Bimo Physical LCD Hardware Diagnostics."""

from pathlib import Path
import sys

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.rendering.lcd_diagnostic import main

if __name__ == "__main__":
    main()
