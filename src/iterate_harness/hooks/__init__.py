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
    "build_hook_inventory",
    "render_hook_inventory",
    "hook_rows_for_capabilities",
    "journal_path",
    "record_hook_execution",
    "iter_hook_executions",
    "list_hook_executions",
    "distinct_events",
    "distinct_hook_types",
    "journal_summary",
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
    if name in {
        "build_hook_inventory",
        "render_hook_inventory",
        "hook_rows_for_capabilities",
    }:
        from iterate_harness.hooks.inventory import (
            build_hook_inventory,
            hook_rows_for_capabilities,
            render_hook_inventory,
        )

        return {
            "build_hook_inventory": build_hook_inventory,
            "render_hook_inventory": render_hook_inventory,
            "hook_rows_for_capabilities": hook_rows_for_capabilities,
        }[name]
    if name in {
        "journal_path",
        "record_hook_execution",
        "iter_hook_executions",
        "list_hook_executions",
        "distinct_events",
        "distinct_hook_types",
        "journal_summary",
    }:
        from iterate_harness.hooks.journal import (
            distinct_events,
            distinct_hook_types,
            iter_hook_executions,
            journal_path,
            journal_summary,
            list_hook_executions,
            record_hook_execution,
        )

        return {
            "journal_path": journal_path,
            "record_hook_execution": record_hook_execution,
            "iter_hook_executions": iter_hook_executions,
            "list_hook_executions": list_hook_executions,
            "distinct_events": distinct_events,
            "distinct_hook_types": distinct_hook_types,
            "journal_summary": journal_summary,
        }[name]
    raise AttributeError(name)
