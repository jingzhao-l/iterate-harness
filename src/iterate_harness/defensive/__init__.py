"""Defensive kernel for code mode (design §20.3.2).

In ``code`` mode the harness enforces defensive programming mechanically
rather than by prompt discipline alone: every mutating file tool runs inside a
small transaction (snapshot → edit → post-check → commit/rollback) and the
project invariants are re-checked after each mutation. Declared assumptions
are recorded in the decision log for a full audit trail.
"""

from __future__ import annotations

from iterate_harness.defensive.invariants import (
    COMMAND_METACHARS,
    InvariantReport,
    InvariantViolation,
    check_invariants,
    check_invariants_async,
    command_is_safe,
)
from iterate_harness.defensive.kernel import DefensiveKernel
from iterate_harness.defensive.transaction import FileTransactionBuffer

__all__ = [
    "COMMAND_METACHARS",
    "DefensiveKernel",
    "FileTransactionBuffer",
    "InvariantReport",
    "InvariantViolation",
    "check_invariants",
    "check_invariants_async",
    "command_is_safe",
]
