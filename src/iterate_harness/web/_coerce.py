"""Defensive value-coercion helpers for the WebUI routes.

Decision-log / checkpoint payloads are parsed from JSON and typed as
``dict[str, object]``; strict mypy rejects passing ``object`` straight into
``int()`` / ``float()`` / ``list()``. These helpers coerce with a fallback so
malformed persisted data degrades to a safe default instead of crashing the
API.
"""

from __future__ import annotations

from typing import Any


def as_int(value: object, fallback: int = 0) -> int:
    """Coerce ``value`` to int, returning ``fallback`` for non-numeric input."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return fallback
    return fallback


def as_float(value: object, fallback: float = 0.0) -> float:
    """Coerce ``value`` to float, returning ``fallback`` for non-numeric input."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return fallback
    return fallback


def as_list(value: object) -> list[Any]:
    """Return ``value`` when it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []
