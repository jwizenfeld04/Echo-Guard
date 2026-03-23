"""File watcher for post-save redundancy checking.

Watches the repo for Python/JS/TS/Go/etc file changes and runs
Echo Guard checks automatically when files are saved.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from echo_guard.config import EchoGuardConfig
from echo_guard.languages import supported_extensions


class _ChangeHandler(FileSystemEventHandler):
    """Handles file change events with debouncing."""

    def __init__(
        self,
        callback: Callable[[str], None],
        config: EchoGuardConfig,
    ):
        self.callback = callback
        self.config = config
        self._last_event: dict[str, float] = {}
        self._debounce_s = config.watch_debounce_ms / 1000.0
        self._extensions = supported_extensions()

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle(str(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle(str(event.src_path))

    def _handle(self, filepath: str) -> None:
        path = Path(filepath)

        # Check extension
        if path.suffix.lower() not in self._extensions:
            return

        # Check excluded dirs
        for part in path.parts:
            if part in self.config.exclude_dirs:
                return

        # Debounce
        now = time.time()
        last = self._last_event.get(filepath, 0)
        if now - last < self._debounce_s:
            return
        self._last_event[filepath] = now

        self.callback(filepath)


def watch_repo(
    repo_root: str | Path,
    on_change: Callable[[str], None],
    config: EchoGuardConfig | None = None,
) -> Observer:  # type: ignore[valid-type]
    """Start watching a repo for file changes.

    Args:
        repo_root: Path to the repository root
        on_change: Callback called with the changed filepath
        config: Optional config (auto-loaded if not provided)

    Returns:
        The Observer instance (call .stop() to shut down)
    """
    if config is None:
        config = EchoGuardConfig.load(repo_root)

    handler = _ChangeHandler(on_change, config)
    observer = Observer()
    observer.schedule(handler, str(repo_root), recursive=True)
    observer.start()
    return observer
