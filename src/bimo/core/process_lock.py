"""Single-instance process lock for hardware display access.

Prevents multiple background or foreground processes from concurrently
writing to the physical framebuffer (/dev/fb1), which causes visual flickering
and animation overlapping.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import signal
import sys
import time

logger = logging.getLogger(__name__)

# Try importing fcntl on Unix platforms
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

LOCK_FILE = Path("/tmp/bimo_display.lock")


def acquire_display_lock(lock_path: Path = LOCK_FILE) -> int | None:
    """Acquire an exclusive lock for the physical display.

    If another process holds the lock, it is terminated gracefully to prevent
    framebuffer contention.
    """
    if not HAS_FCNTL:
        # On Windows or systems without fcntl, process contention is not an issue for /dev/fb1
        return None

    try:
        lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o666)

        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            # Another process currently owns the display lock!
            try:
                with open(lock_path, "r") as f:
                    content = f.read().strip()
                if content.isdigit():
                    old_pid = int(content)
                    if old_pid != os.getpid():
                        logger.warning(
                            "Another Bimo display process (PID %d) is running. Terminating it to take over screen...",
                            old_pid,
                        )
                        try:
                            os.kill(old_pid, signal.SIGTERM)
                            time.sleep(0.3)
                            # If still alive after 300ms, force kill
                            os.kill(old_pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            pass
            except Exception as e:
                logger.debug("Error reading/terminating previous instance: %s", e)

            # Retry acquiring lock
            time.sleep(0.2)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except Exception as e:
                logger.error("Failed to acquire display lock: %s", e)
                os.close(lock_fd)
                return None

        # Record our current PID
        os.ftruncate(lock_fd, 0)
        os.lseek(lock_fd, 0, os.SEEK_SET)
        os.write(lock_fd, f"{os.getpid()}\n".encode())
        return lock_fd
    except Exception as e:
        logger.debug("Lock error (safe to ignore): %s", e)
        return None


def release_display_lock(lock_fd: int | None, lock_path: Path = LOCK_FILE) -> None:
    """Release the display lock and clean up the lockfile."""
    if lock_fd is not None and HAS_FCNTL:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except Exception:
            pass
        try:
            if lock_path.exists():
                lock_path.unlink()
        except Exception:
            pass


def blank_physical_display(fb_device: str = "/dev/fb1", size_bytes: int = 480 * 320 * 2) -> bool:
    """Immediately blank/clear the physical framebuffer to black and kill lingering processes."""
    # 1. Terminate any previous process holding the lock
    if LOCK_FILE.exists():
        try:
            with open(LOCK_FILE, "r") as f:
                content = f.read().strip()
            if content.isdigit():
                pid = int(content)
                logger.info("Stopping previous Bimo process (PID %d)...", pid)
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.2)
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            LOCK_FILE.unlink()
        except Exception as e:
            logger.debug("Lockfile cleanup error: %s", e)

    # 2. Directly write zeroes to /dev/fb1
    fb_path = Path(fb_device)
    if fb_path.exists():
        try:
            fd = os.open(str(fb_path), os.O_WRONLY)
            os.write(fd, bytes(size_bytes))
            os.close(fd)
            logger.info("Successfully blanked display %s (pure black)", fb_device)
            return True
        except Exception as e:
            logger.error("Could not blank display %s: %s", fb_device, e)
            return False
    return False
