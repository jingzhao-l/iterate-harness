"""Hooks exports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from iterate_harness.hooks.events import HookEvent
    from iterate_harness.hooks.executor import HookExecutionContext, HookExecutor
    from iterate_harness.hooks.loader import HookRegistry
    from iterate_harness.hooks.types import AggregatedHookResult, HookResult

__all__ = [
    "AggregatedHookResult",
    "HookEvent",
    "HookExecutionContext",
    "HookExecutor",
    "HookRegistry",
    "HookResult",
    "load_hook_registry",
]


def __getattr__(name: str) -> Any:
    if name == "HookEvent":
        from iterate_harness.hooks.events import HookEvent

        return HookEvent
    if name in {"HookExecutionContext", "HookExecutor"}:
        from iterate_harness.hooks.executor import HookExecutionContext, HookExecutor

        return {
            "HookExecutionContext": HookExecutionContext,
            "HookExecutor": HookExecutor,
        }[name]
    if name in {"HookRegistry", "load_hook_registry"}:
        from iterate_harness.hooks.loader import HookRegistry, load_hook_registry

        return {
            "HookRegistry": HookRegistry,
            "load_hook_registry": load_hook_registry,
        }[name]
    if name in {"AggregatedHookResult", "HookResult"}:
        from iterate_harness.hooks.types import AggregatedHookResult, HookResult

        return {
            "AggregatedHookResult": AggregatedHookResult,
            "HookResult": HookResult,
        }[name]
    raise AttributeError(name)
