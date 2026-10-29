"""
QBuilder File System Watcher — Thread 3 in daemon.

Watches mod directories for file changes and enqueues flash rebuilds.
Runs inside the daemon process (which owns the write connection).

Two paths to the same outcome:
  1. External editor → watchdog detects → enqueue_file → daemon rebuilds
  2. MCP ck3_file   → notify_file_changed IPC → enqueue_file → daemon rebuilds
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

logger = logging.getLogger(__name__)

# Extensions worth processing (Router assigns non-SKIP envelopes to these)
WATCHED_EXTENSIONS = frozenset({'.txt', '.yml'})

# Debounce window: ignore duplicate events for the same file within this period
DEBOUNCE_SECONDS = 1.0


class ChangeHandler(FileSystemEventHandler):
    """Maps OS filesystem events to QBuilder enqueue_file() calls."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        mod_root: Path,
        mod_name: str,
        mutation_lock,
    ):
        super().__init__()
        self.conn = conn
        self.mod_root = mod_root
        self.mod_name = mod_name
        self.mutation_lock = mutation_lock
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._process(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._process(event.src_path)

    def _process(self, abs_path_str: str) -> None:
        path = Path(abs_path_str)

        # Extension filter
        if path.suffix.lower() not in WATCHED_EXTENSIONS:
            return

        # Debounce: skip if we saw this path recently
        now = time.monotonic()
        with self._lock:
            last = self._last_seen.get(abs_path_str, 0.0)
            if now - last < DEBOUNCE_SECONDS:
                return
            self._last_seen[abs_path_str] = now

        # Compute relative path within mod
        try:
            rel_path = str(path.relative_to(self.mod_root)).replace("\\", "/")
        except ValueError:
            return

        # Enqueue for flash rebuild
        try:
            from qbuilder.api import enqueue_file, PRIORITY_FLASH
            with self.mutation_lock:
                result = enqueue_file(
                    mod_name=self.mod_name,
                    rel_path=rel_path,
                    priority=PRIORITY_FLASH,
                )
            if result.success:
                logger.info("Watcher enqueued: %s/%s", self.mod_name, rel_path)
        except Exception:
            logger.exception("Watcher failed to enqueue: %s/%s", self.mod_name, rel_path)


class WatcherService:
    """
    Manages the watchdog Observer thread.

    Queries the DB for active playset mods and schedules filesystem watches
    on each mod's source_path directory.
    """

    def __init__(self, conn: sqlite3.Connection, mutation_lock=None):
        self.conn = conn
        self.mutation_lock = mutation_lock or threading.RLock()
        self._observer: Optional[Observer] = None
        self._watch_count = 0

    def start(self) -> int:
        """
        Build mod_map from DB and start watching.

        Returns the number of directories being watched.
        """
        mod_map = self._build_mod_map()
        if not mod_map:
            logger.warning("No mod directories to watch")
            return 0

        self._observer = Observer()

        for source_path, mod_name in mod_map.items():
            root = Path(source_path)
            if not root.is_dir():
                continue
            handler = ChangeHandler(self.conn, root, mod_name, self.mutation_lock)
            self._observer.schedule(handler, str(root), recursive=True)
            self._watch_count += 1
            logger.info("Watching: %s (%s)", mod_name, source_path)

        self._observer.daemon = True
        self._observer.start()
        logger.info("Watcher started: %d directories", self._watch_count)
        return self._watch_count

    def stop(self) -> None:
        """Stop the observer thread."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None
            logger.info("Watcher stopped")

    def _build_mod_map(self) -> dict[str, str]:
        """Return watch roots from the canonical active Session.mods."""
        from ck3lens.workspace import load_session

        session = load_session()
        return {
            str(mod.path): mod.name
            for mod in session.mods
            if mod.load_order > 0 and mod.path.is_dir()
        }
