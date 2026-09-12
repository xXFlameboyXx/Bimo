"""Unit tests for logging infrastructure."""

import logging
import shutil
import tempfile
import unittest
from pathlib import Path

from bimo.core.config import LoggingConfig
from bimo.core.logging import setup_logging


class TestLogging(unittest.TestCase):
    """Test suite for logging configuration."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        # Reset logging and close open file handlers
        root = logging.getLogger()
        for handler in root.handlers:
            handler.close()
        root.handlers.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_setup_logging_console_and_file(self) -> None:
        cfg = LoggingConfig(
            level="DEBUG",
            log_to_file=True,
            log_dir=self.test_dir,
            max_bytes=10000,
            backup_count=2,
        )
        logger = setup_logging(cfg)
        self.assertEqual(logger.level, logging.DEBUG)

        # Log a test message
        test_msg = "Hello Bimo Logger"
        logger.info(test_msg)

        # Check log file was created and contains text
        log_file = Path(self.test_dir) / "bimo.log"
        self.assertTrue(log_file.exists())
        content = log_file.read_text(encoding="utf-8")
        self.assertIn(test_msg, content)


if __name__ == "__main__":
    unittest.main()
