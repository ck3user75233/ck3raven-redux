"""
Writer Lock — Single-Writer Guarantee for QBuilder Daemon.

This module provides OS-level file locking to ensure exactly one daemon
can write to the database at any time.

See docs/SINGLE_WRITER_ARCHITECTURE.md for the canonical design.

Usage:
    from qbuilder.writer_lock import WriterLock, WriterLockError
    
    lock = WriterLock(db_path)
    if lock.acquire():
        # We are the writer
        ...
    else:
        # Another daemon holds the lock
        raise WriterLockError("Another daemon is already running")
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Exit code when another writer exists
EXIT_WRITER_EXISTS = 78  # EX_CONFIG from sysexits.h

# Process-local proof that this PID successfully acquired an OS writer lock.
_OWNED_LOCK_PATHS: set[str] = set()


class WriterLockError(Exception):
    """Raised when writer lock cannot be acquired."""
    pass


@dataclass
class WriterLockInfo:
    """Information about the current writer lock holder."""
    pid: int
    acquired_at: float
    db_path: str


class WriterLock:
    """
    OS-level writer lock for single-writer guarantee.
    
    Uses platform-specific file locking:
    - Windows: msvcrt.locking()
    - Unix: fcntl.flock()
    
    The lock file is {db_path}.writer.lock
    """
    
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.lock_path = self.db_path.parent / f"{self.db_path.name}.writer.lock"
        self._lock_file: Optional[object] = None
        self._acquired = False
    
    def acquire(self, wait_timeout: float = 0.0, poll_interval: float = 0.1) -> bool:
        """
        Attempt to acquire the writer lock.
        
        Args:
            wait_timeout: Seconds to wait for an orderly handoff before failing.
            poll_interval: Delay between retry attempts while waiting.
        
        Returns:
            True if lock acquired, False if another process still holds it.
        """
        if self._acquired:
            return True

        deadline = time.time() + max(wait_timeout, 0.0)

        while True:
            # If a stale lock file exists from a dead process, remove it.
            # The OS already released the byte lock when that process died,
            # so the file is safe to delete — it just confuses status checks.
            if self.lock_path.exists() and not self.is_holder_alive():
                try:
                    self.lock_path.unlink()
                except OSError:
                    pass

            try:
                # Create lock file if it doesn't exist
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)

                # Open without truncating live holder metadata. The winner
                # rewrites the file only after acquiring the OS byte lock.
                self._lock_file = open(self.lock_path, 'a+')

                # Attempt non-blocking lock
                if sys.platform == 'win32':
                    acquired = self._acquire_windows()
                else:
                    acquired = self._acquire_unix()
                if acquired:
                    return True
            except (IOError, OSError):
                if self._lock_file:
                    self._lock_file.close()
                    self._lock_file = None

            if time.time() >= deadline:
                return False

            time.sleep(max(poll_interval, 0.01))
    
    def _acquire_windows(self) -> bool:
        """Windows-specific lock acquisition."""
        import msvcrt
        try:
            # Lock first byte, non-blocking
            msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            self._write_lock_info()
            self._acquired = True
            _OWNED_LOCK_PATHS.add(str(self.lock_path.resolve()))
            return True
        except IOError:
            self._lock_file.close()
            self._lock_file = None
            return False
    
    def _acquire_unix(self) -> bool:
        """Unix-specific lock acquisition."""
        import fcntl
        try:
            # Exclusive lock, non-blocking
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._write_lock_info()
            self._acquired = True
            _OWNED_LOCK_PATHS.add(str(self.lock_path.resolve()))
            return True
        except IOError:
            self._lock_file.close()
            self._lock_file = None
            return False
    
    def _write_lock_info(self) -> None:
        """Write lock holder information to lock file."""
        import json
        info = {
            "pid": os.getpid(),
            "acquired_at": time.time(),
            "db_path": str(self.db_path),
        }
        self._lock_file.seek(0)
        self._lock_file.truncate()
        self._lock_file.write(json.dumps(info))
        self._lock_file.flush()
    
    def release(self) -> None:
        """Release the writer lock."""
        if not self._acquired or not self._lock_file:
            return
        
        try:
            if sys.platform == 'win32':
                import msvcrt
                try:
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except IOError:
                    pass
            else:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_file.close()
            self._lock_file = None
            self._acquired = False
            _OWNED_LOCK_PATHS.discard(str(self.lock_path.resolve()))
            # Remove lock file so status checks see no stale entry.
            try:
                self.lock_path.unlink()
            except OSError:
                pass
    
    def get_holder_info(self) -> Optional[WriterLockInfo]:
        """
        Get information about the current lock holder.
        
        Returns None if lock file doesn't exist or is invalid.
        """
        if not self.lock_path.exists():
            return None
        
        try:
            import json
            content = self.lock_path.read_text()
            data = json.loads(content)
            return WriterLockInfo(
                pid=data["pid"],
                acquired_at=data["acquired_at"],
                db_path=data["db_path"],
            )
        except (json.JSONDecodeError, KeyError, IOError):
            return None
    
    def is_holder_alive(self) -> bool:
        """
        Check if the lock holder process is still running.
        
        This is a heuristic - the process might exist but not be the daemon.
        """
        info = self.get_holder_info()
        if not info:
            return False
        
        try:
            if sys.platform == 'win32':
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, info.pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            else:
                os.kill(info.pid, 0)  # Signal 0 just checks if process exists
                return True
        except (OSError, PermissionError):
            return False
    
    @property
    def is_acquired(self) -> bool:
        """True if this instance holds the lock."""
        return self._acquired
    
    def __enter__(self) -> "WriterLock":
        if not self.acquire():
            raise WriterLockError(
                f"Cannot acquire writer lock. Another daemon (PID {self.get_holder_info().pid if self.get_holder_info() else 'unknown'}) is running."
            )
        return self
    
    def __exit__(self, *args) -> None:
        self.release()
    
    def __del__(self) -> None:
        self.release()


def current_process_holds_writer_lock(db_path: Path) -> bool:
    """Return whether this process successfully acquired the OS writer lock."""
    lock_path = Path(db_path).parent / f"{Path(db_path).name}.writer.lock"
    return str(lock_path.resolve()) in _OWNED_LOCK_PATHS


def require_current_process_writer(db_path: Path) -> None:
    """Reject database mutations outside the lock-owning daemon process."""
    if not current_process_holds_writer_lock(db_path):
        raise WriterLockError(
            "Database mutation requires the current process to own the QBuilder writer lock"
        )


def check_writer_lock(db_path: Path) -> dict:
    """
    Check the status of the writer lock without acquiring it.
    
    Returns a status dict suitable for health checks.
    """
    lock = WriterLock(db_path)
    info = lock.get_holder_info()
    
    if info is None:
        return {
            "lock_exists": False,
            "holder_pid": None,
            "holder_alive": False,
            "can_acquire": True,
        }
    
    return {
        "lock_exists": True,
        "holder_pid": info.pid,
        "holder_alive": lock.is_holder_alive(),
        "acquired_at": info.acquired_at,
        "can_acquire": not lock.is_holder_alive(),
    }
