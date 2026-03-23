"""File watcher for real-time document ingestion.

Uses polling-based watch (cross-platform) to detect new/changed files
and trigger ingestion immediately instead of waiting for the 10-minute timer.
"""
from __future__ import annotations

import logging
import os
import pathlib
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("rag-bridge.file_watcher")

WATCHER_ENABLED = os.getenv("FILE_WATCHER_ENABLED", "true").lower() == "true"
WATCH_INTERVAL = float(os.getenv("FILE_WATCHER_INTERVAL", "10"))  # seconds
DEBOUNCE_SECONDS = float(os.getenv("FILE_WATCHER_DEBOUNCE", "5"))


class FileWatcher:
    """Polls directories for changes and triggers a callback.

    Uses mtime-based detection — works on all platforms without inotify.
    """

    def __init__(self, directories: list[pathlib.Path], callback: Callable[[], Any]):
        self._dirs = directories
        self._callback = callback
        self._running = False
        self._thread: threading.Thread | None = None
        self._file_mtimes: dict[str, float] = {}
        self._last_trigger = 0.0
        self._changes_pending = threading.Event()

    def start(self) -> None:
        """Start the file watcher in a background thread."""
        if not WATCHER_ENABLED:
            logger.info("File watcher disabled")
            return
        if self._running:
            return

        self._running = True
        # Initial scan to record current state
        self._scan_all()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True, name="file-watcher")
        self._thread.start()
        logger.info("File watcher started (interval=%ss, dirs=%d)", WATCH_INTERVAL, len(self._dirs))

    def stop(self) -> None:
        """Stop the file watcher."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=WATCH_INTERVAL + 1)

    def _scan_all(self) -> set[str]:
        """Scan all watched directories and return set of changed files."""
        changed: set[str] = set()
        current_files: set[str] = set()

        for directory in self._dirs:
            if not directory.is_dir():
                continue
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                fpath = str(path)
                current_files.add(fpath)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue

                old_mtime = self._file_mtimes.get(fpath)
                if old_mtime is None or mtime > old_mtime:
                    changed.add(fpath)
                self._file_mtimes[fpath] = mtime

        # Detect deleted files
        deleted = set(self._file_mtimes.keys()) - current_files
        for d in deleted:
            del self._file_mtimes[d]
            changed.add(d)

        return changed

    def _watch_loop(self) -> None:
        """Main watch loop running in a background thread."""
        while self._running:
            try:
                changed = self._scan_all()
                if changed:
                    now = time.monotonic()
                    if now - self._last_trigger >= DEBOUNCE_SECONDS:
                        logger.info("File watcher: %d changes detected, triggering ingest", len(changed))
                        try:
                            self._callback()
                        except Exception as exc:
                            logger.warning("File watcher callback failed: %s", exc)
                        self._last_trigger = now
                    else:
                        logger.debug("File watcher: changes detected but debouncing")
            except Exception as exc:
                logger.warning("File watcher scan error: %s", exc)

            time.sleep(WATCH_INTERVAL)

    def status(self) -> dict[str, Any]:
        """Return watcher status."""
        return {
            "enabled": WATCHER_ENABLED,
            "running": self._running,
            "watched_dirs": [str(d) for d in self._dirs],
            "tracked_files": len(self._file_mtimes),
            "interval": WATCH_INTERVAL,
            "debounce": DEBOUNCE_SECONDS,
        }
