#!/usr/bin/env python3
"""Main root launcher for Bimo."""

from pathlib import Path
import sys

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.simulator_app import main

if __name__ == "__main__":
    main()
