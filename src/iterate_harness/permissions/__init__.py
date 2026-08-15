"""Permission helpers for IterateHarness."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from iterate_harness.permissions.checker import (
        PermissionChecker,
        PermissionDecision,
        build_permission_checker,
    )
    from iterate_harness.permissions.modes import PermissionMode

__all__ = [
    "PermissionChecker",
    "PermissionDecision",
    "PermissionMode",
    "build_permission_checker",
]


def __getattr__(name: str):
    if name in {"PermissionChecker", "PermissionDecision", "build_permission_checker"}:
        from iterate_harness.permissions.checker import (
            PermissionChecker,
            PermissionDecision,
            build_permission_checker,
        )

        return {
            "PermissionChecker": PermissionChecker,
            "PermissionDecision": PermissionDecision,
            "build_permission_checker": build_permission_checker,
        }[name]
    if name == "PermissionMode":
        from iterate_harness.permissions.modes import PermissionMode

        return PermissionMode
    raise AttributeError(name)
