"""Kernel-backed file locking for browser sessions.

The PID text is diagnostic only; ``flock`` is the ownership mechanism and is
released by the kernel when the owning process exits.
"""

import errno
import fcntl
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_LOCK_PATH = os.path.join(
    os.environ.get("TMPDIR", os.path.expanduser("~")),
    ".tbp_browser.lock",
)


class SessionLock:
    """Ensure one owner for a lock path using non-blocking ``flock``."""

    def __init__(self, lock_path=DEFAULT_LOCK_PATH):
        self.lock_path = os.fspath(lock_path)
        self._acquired = False
        self._fd = None

    def _is_pid_alive(self, pid):
        """Check if a process with given PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True  # Process exists but owned by another user
        except (OSError, ProcessLookupError):
            return False

    def acquire(self):
        """Acquire immediately or raise ``RuntimeError`` if held."""
        if self._acquired:
            return
        parent = os.path.dirname(os.path.abspath(self.lock_path))
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
        fd = None
        try:
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise RuntimeError(
                        f"Another browser session is running (lock: {self.lock_path})"
                    ) from exc
                raise
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.fsync(fd)
            self._fd = fd
            self._acquired = True
        except Exception:
            if fd is not None and not self._acquired:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise

    def release(self):
        """Release the kernel lock and close the descriptor."""
        if not self._acquired:
            return
        fd, self._fd = self._fd, None
        self._acquired = False
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass
