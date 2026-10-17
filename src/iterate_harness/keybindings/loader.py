"""Load keybindings from config."""

from __future__ import annotations

import logging
from pathlib import Path

from iterate_harness.config.paths import get_config_dir
from iterate_harness.keybindings.parser import parse_keybindings
from iterate_harness.keybindings.resolver import resolve_keybindings

log = logging.getLogger(__name__)


def get_keybindings_path() -> Path:
    """Return the user keybindings path."""
    return get_config_dir() / "keybindings.json"


def load_keybindings() -> dict[str, str]:
    """Load and merge keybindings."""
    path = get_keybindings_path()
    if not path.exists():
        return resolve_keybindings()
    try:
        return resolve_keybindings(parse_keybindings(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        # A hand-edited keybindings.json (trailing comma, wrong type) must not
        # crash runtime construction or the per-turn sync: warn and fall back
        # to the resolved defaults, like the theme loader does for bad themes.
        log.warning("Ignoring malformed keybindings at %s: %s", path, exc)
        return resolve_keybindings()
