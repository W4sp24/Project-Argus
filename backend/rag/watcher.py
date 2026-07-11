"""Vault watcher: debounce filesystem events into index updates.

The debounce queue is separated from the watchdog Observer so it can be unit
tested without touching the real filesystem event stream.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from backend.rag.index import VaultIndex
from backend.rag.paths import is_indexable

DEBOUNCE_SECONDS = 2.0


class DebounceQueue:
    """Collects changed paths and flushes those quiet for DEBOUNCE_SECONDS."""

    def __init__(self, debounce: float = DEBOUNCE_SECONDS) -> None:
        self._debounce = debounce
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()

    def push(self, rel_path: str) -> None:
        if not is_indexable(rel_path):
            return
        with self._lock:
            self._pending[rel_path] = time.monotonic()

    def pop_ready(self, now: float | None = None) -> list[str]:
        """Paths whose last event is at least the debounce window old."""
        now = time.monotonic() if now is None else now
        with self._lock:
            ready = [path for path, stamp in self._pending.items() if now - stamp >= self._debounce]
            for path in ready:
                del self._pending[path]
        return ready


def watch_vault(
    vault_path: Path,
    index: VaultIndex,
    on_update: Callable[[str, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Blocking watch loop. Ctrl+C (or ``stop_event``) exits."""
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    queue = DebounceQueue()

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event: FileSystemEvent) -> None:
            if event.is_directory:
                return
            for raw in filter(None, [event.src_path, getattr(event, "dest_path", None)]):
                try:
                    rel = Path(str(raw)).resolve().relative_to(vault_path.resolve()).as_posix()
                except ValueError:
                    continue
                queue.push(rel)

    observer = Observer()
    observer.schedule(Handler(), str(vault_path), recursive=True)
    observer.start()
    try:
        while stop_event is None or not stop_event.is_set():
            time.sleep(0.5)
            for rel_path in queue.pop_ready():
                count = index.upsert_file(vault_path, rel_path)
                if on_update:
                    on_update(rel_path, count)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
