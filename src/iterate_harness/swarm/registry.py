"""Backend registry for teammate execution."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from iterate_harness.platforms import get_platform_capabilities
from iterate_harness.swarm.types import BackendDetectionResult, BackendType, TeammateExecutor

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BackendRegistry
# ---------------------------------------------------------------------------


class BackendRegistry:
    """Registry that maps BackendType names to TeammateExecutor instances.

    Currently only the ``in_process`` and ``subprocess`` backends are
    implemented and registered.  The pane-based backends (``tmux`` /
    ``iterm2``, see :class:`~iterate_harness.swarm.types.PaneBackend`) are
    reserved for a future implementation and are never returned by detection,
    so detection always yields a backend that :meth:`get_executor` can resolve.

    Usage::

        registry = BackendRegistry()
        executor = registry.get_executor()           # auto-detect best backend
        executor = registry.get_executor("in_process")  # explicit selection
    """

    def __init__(self) -> None:
        self._backends: dict[BackendType, TeammateExecutor] = {}
        self._detected: BackendType | None = None
        self._detection_result: BackendDetectionResult | None = None
        self._in_process_fallback_active: bool = False
        self._register_defaults()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_backend(self, executor: TeammateExecutor) -> None:
        """Register a custom executor under its declared ``type`` key."""
        self._backends[executor.type] = executor
        logger.debug("Registered backend: %s", executor.type)

    def detect_backend(self) -> BackendType:
        """Detect and cache the most capable available backend.

        Only the implemented backends are ever selected here:
        ``in_process`` or ``subprocess``.  The pane backends (``tmux`` /
        ``iterm2``) are reserved for a future implementation and are never
        returned, so the result is always resolvable via :meth:`get_executor`.

        Detection priority:
        1. ``in_process`` – if in-process fallback was previously activated.
        2. ``subprocess`` – always available as the safe fallback.

        Returns:
            The detected :data:`BackendType` string.
        """
        if self._detected is not None:
            logger.debug(
                "[BackendRegistry] Using cached backend detection: %s", self._detected
            )
            return self._detected

        logger.debug("[BackendRegistry] Starting backend detection...")

        # Priority 1: in-process fallback (activated after a prior failed spawn)
        if self._in_process_fallback_active:
            logger.debug(
                "[BackendRegistry] in_process fallback active — selecting in_process"
            )
            self._detected = "in_process"
            self._detection_result = BackendDetectionResult(
                backend="in_process",
                is_native=True,
            )
            return self._detected

        # Priority 2: subprocess (always available)
        logger.debug("[BackendRegistry] Selected: subprocess (default fallback)")
        self._detected = "subprocess"
        self._detection_result = BackendDetectionResult(
            backend="subprocess",
            is_native=False,
        )
        return self._detected

    def detect_pane_backend(self) -> BackendDetectionResult:
        """Detect which pane backend should be used.

        The pane backends (``tmux`` / ``iterm2``) are reserved for a future
        implementation and are currently **not** registered.  Rather than
        return a ``backend_type`` that :meth:`get_executor` would raise a
        :class:`KeyError` for, this method raises :class:`RuntimeError` every
        call so callers fall back to the implemented execution backends.

        Returns:
            :class:`BackendDetectionResult` describing the chosen pane backend.

        Raises:
            RuntimeError: Always, because no pane backend is implemented /
                registered yet.
        """
        logger.warning(
            "[BackendRegistry] detect_pane_backend: tmux/iTerm2 pane backends are "
            "reserved (not yet implemented/registered); raising instead of returning "
            "an unusable backend_type"
        )
        raise RuntimeError(
            "Pane-backed execution (tmux / iTerm2) is not available: these backends "
            "are reserved for a future implementation and not currently registered. "
            "Use the 'subprocess' or 'in_process' execution mode instead."
        )

    def get_executor(self, backend: BackendType | None = None) -> TeammateExecutor:
        """Return a TeammateExecutor for the given backend type.

        Args:
            backend: Explicit backend type to use. When *None* the registry
                     auto-detects the best available backend.

        Returns:
            The registered :class:`~iterate_harness.swarm.types.TeammateExecutor`.

        Raises:
            KeyError: If the requested backend has not been registered.
        """
        resolved = backend or self.detect_backend()
        executor = self._backends.get(resolved)
        if executor is None:
            available = list(self._backends.keys())
            raise KeyError(
                f"Backend {resolved!r} is not registered. Available: {available}"
            )
        return executor

    def get_preferred_backend(self, config: dict | None = None) -> BackendType:
        """Return the user-preferred backend from settings / config.

        Falls back to auto-detection when no explicit preference is set.

        Args:
            config: Optional settings dict. Reads ``teammate_mode`` key if
                    present (values: ``"auto"``, ``"in_process"``,
                    ``"tmux"``).

        Returns:
            The resolved :data:`BackendType`.
        """
        if config:
            mode = config.get("teammate_mode", "auto")
        else:
            mode = os.environ.get("ITERATE_TEAMMATE_MODE", "auto")

        logger.debug("[BackendRegistry] get_preferred_backend: mode=%s", mode)

        if mode == "in_process":
            return "in_process"
        elif mode in ("tmux", "iterm2"):
            # Pane backends are reserved for a future implementation and are
            # not registered, so requesting one explicitly would later throw a
            # KeyError from get_executor(). Fall back to auto-detection,
            # which always yields an implemented/registered backend.
            logger.warning(
                "[BackendRegistry] Preferred backend %r is reserved (not yet "
                "implemented/registered); falling back to auto-detection",
                mode,
            )
            return self.detect_backend()
        else:
            # "auto" — fall through to detection
            return self.detect_backend()

    def mark_in_process_fallback(self) -> None:
        """Record that spawn fell back to in-process mode.

        Called when no pane backend was available. After this,
        ``get_executor()`` will keep returning the in-process backend for the
        lifetime of the process (the environment won't change mid-session).
        """
        logger.debug("[BackendRegistry] Marking in-process fallback as active")
        self._in_process_fallback_active = True
        # Invalidate cached detection so the next call re-detects
        self._detected = None
        self._detection_result = None

    def get_cached_detection_result(self) -> BackendDetectionResult | None:
        """Return the cached :class:`BackendDetectionResult`, or *None* if not yet detected."""
        return self._detection_result

    def available_backends(self) -> list[BackendType]:
        """Return sorted list of registered backend types."""
        return sorted(self._backends.keys())  # type: ignore[return-value]

    def health_check(self) -> dict[str, Any]:
        """Check the health of all registered backends.

        Returns:
            Dict with backend_name -> {available: bool, type: str} mapping,
            plus a total_count of available backends.
        """
        results: dict[str, dict[str, Any]] = {}
        available_count = 0

        for backend_type, executor in self._backends.items():
            is_available = executor.is_available()
            results[backend_type] = {
                "available": is_available,
                "type": str(executor.type),
            }
            if is_available:
                available_count += 1

        return {
            "backends": results,
            "total_count": available_count,
        }

    def reset(self) -> None:
        """Clear detection cache and re-register defaults.

        Intended for testing — allows re-detection after env changes.
        """
        self._detected = None
        self._detection_result = None
        self._in_process_fallback_active = False
        self._backends.clear()
        self._register_defaults()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register_defaults(self) -> None:
        """Register built-in backends that are unconditionally available."""
        from iterate_harness.swarm.subprocess_backend import SubprocessBackend

        self._backends["subprocess"] = SubprocessBackend()
        if get_platform_capabilities().supports_swarm_mailbox:
            from iterate_harness.swarm.in_process import InProcessBackend

            self._backends["in_process"] = InProcessBackend()

        # Tmux/iTerm2 backends are reserved for a future implementation and
        # are intentionally NOT registered here, so detection never yields an
        # unresolvable backend_type. A future TmuxBackend/iTerm2Backend can be
        # hooked up via register_backend().


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: BackendRegistry | None = None


def get_backend_registry() -> BackendRegistry:
    """Return the process-wide singleton BackendRegistry."""
    global _registry
    if _registry is None:
        _registry = BackendRegistry()
    return _registry


def mark_in_process_fallback() -> None:
    """Module-level convenience: mark in-process fallback on the singleton registry."""
    get_backend_registry().mark_in_process_fallback()
