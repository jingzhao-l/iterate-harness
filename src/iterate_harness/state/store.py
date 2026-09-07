"""Observable application state store."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace

# ``typing.Unpack`` only exists on Python 3.11+; the package supports 3.10
# (requires-python = ">=3.10", npm wrapper resolves any interpreter >= 3.10),
# so fall back to ``typing_extensions`` (guaranteed present via pydantic).
if sys.version_info >= (3, 11):
    from typing import Unpack
else:  # pragma: no cover - exercised on Python 3.10 only
    from typing_extensions import Unpack

from iterate_harness.state.app_state import AppState, AppStateUpdates


Listener = Callable[[AppState], None]


class AppStateStore:
    """Very small observable state store."""

    def __init__(self, initial_state: AppState) -> None:
        self._state = initial_state
        self._listeners: list[Listener] = []

    def get(self) -> AppState:
        """Return the current state snapshot."""
        return self._state

    def set(self, **updates: Unpack[AppStateUpdates]) -> AppState:
        """Update the state and notify listeners."""
        self._state = replace(self._state, **updates)
        for listener in list(self._listeners):
            listener(self._state)
        return self._state

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register a listener and return an unsubscribe callback."""
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe
