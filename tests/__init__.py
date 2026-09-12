"""Unit test package initialization for Bimo.

Ensures src/ is present in sys.path so tests can be run via:
    python -m unittest
without requiring environment variable adjustments.
"""

from pathlib import Path
import sys

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
