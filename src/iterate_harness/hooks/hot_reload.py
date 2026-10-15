"""Best-effort hot reloading for settings-backed hooks."""

from __future__ import annotations

import logging
from pathlib import Path

from iterate_harness.config import load_settings
from iterate_harness.hooks.loader import HookRegistry, load_hook_registry

log = logging.getLogger(__name__)


class HookReloader:
    """Reload hook definitions when the settings file changes."""

    def __init__(self, settings_path: Path) -> None:
        self._settings_path = settings_path
        self._last_mtime_ns = -1
        self._registry = HookRegistry()

    def current_registry(self) -> HookRegistry:
        """Return the latest registry, reloading if needed."""
        try:
            stat = self._settings_path.stat()
        except FileNotFoundError:
            self._registry = HookRegistry()
            self._last_mtime_ns = -1
            return self._registry

        if stat.st_mtime_ns != self._last_mtime_ns:
            try:
                registry = load_hook_registry(load_settings(self._settings_path))
            except Exception:
                # "Best-effort" means exactly this: a broken settings file
                # must keep the previous working registry active AND must not
                # mark the file as consumed — otherwise the broken state is
                # never retried until the file changes again. The mtime is
                # only recorded after a successful load.
                log.warning(
                    "hook reload failed for %s; keeping the previous registry",
                    self._settings_path,
                    exc_info=True,
                )
                return self._registry
            self._registry = registry
            self._last_mtime_ns = stat.st_mtime_ns
        return self._registry
