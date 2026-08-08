"""Portable per-port process locking used in addition to pyserial exclusivity."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class PortLockError(OSError):
    """The requested serial port is already owned by another process."""


class PortLock:
    """An advisory lock with a deterministic lifetime.

    The lock file lives in the current user's cache rather than beside the device
    path, which also makes Windows COM names safe filenames.  The operating
    system releases the lock if this process crashes.
    """

    def __init__(self, port: str, *, lock_directory: Path | None = None) -> None:
        digest = hashlib.sha256(os.path.normcase(os.path.abspath(port)).encode()).hexdigest()
        root = lock_directory or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        self.path = root / "cs71-protocol" / "locks" / f"{digest}.lock"
        self._file: object | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            handle = open(self.path, "a+b")
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    handle.close()
                    raise PortLockError(f"serial port is already locked: {self.path}") from exc
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    handle.close()
                    raise PortLockError(f"serial port is already locked: {self.path}") from exc
            self._file = handle
        except PortLockError:
            raise
        except OSError as exc:
            raise PortLockError(f"cannot lock serial port: {self.path}") from exc

    def release(self) -> None:
        if self._file is None:
            return
        handle = self._file
        self._file = None
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "PortLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
