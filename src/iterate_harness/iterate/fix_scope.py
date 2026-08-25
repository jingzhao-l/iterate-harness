"""Post-fix scope verification (design §11.2.2 "is_atomic 实测校验").

The reviewer classifies each finding as atomic or architectural and the fixer
is instructed to only touch atomic findings — but a classification is a model
self-report. This module MEASURES the actual fix scope after a normal-mode
round: if the uncommitted diff exceeds ``atomic.max_lines`` the fix almost
certainly leaked architectural changes. The engine logs a
``degraded-to-architectural`` decision entry and injects a steering hint so
the next round splits the change.

All git interactions are defensive: no git executable or no repository
yields ``available=False`` instead of an error.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FixScopeAssessment:
    """Measured post-fix diff scope."""

    available: bool
    added_lines: int = 0
    removed_lines: int = 0
    max_lines: int = 0

    @property
    def total_lines(self) -> int:
        return self.added_lines + self.removed_lines

    @property
    def over_limit(self) -> bool:
        return self.available and self.max_lines > 0 and self.total_lines > self.max_lines


def git_diff_numstat(project_root: str | Path) -> tuple[int, int] | None:
    """``git diff --numstat`` added/removed counts, or None when unavailable.

    Binary files report ``-``; they contribute nothing to the line count.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "diff", "--numstat", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    added = 0
    removed = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            if parts[0] != "-":
                added += int(parts[0])
            if parts[1] != "-":
                removed += int(parts[1])
        except ValueError:
            continue
    return added, removed


def assess_fix_scope(project_root: str | Path, max_lines: int) -> FixScopeAssessment:
    """Measure the uncommitted diff scope against the atomic-fix cap."""
    counts = git_diff_numstat(project_root)
    if counts is None:
        return FixScopeAssessment(available=False, max_lines=max_lines)
    added, removed = counts
    return FixScopeAssessment(
        available=True, added_lines=added, removed_lines=removed, max_lines=max_lines
    )


def fix_scope_over_limit_hint(assessment: FixScopeAssessment) -> str:
    """Engine-injected steering hint when the post-fix diff exceeds the cap."""
    return (
        f"[iterate] Post-fix scope check: the uncommitted diff is "
        f"{assessment.total_lines} lines (+{assessment.added_lines}/-{assessment.removed_lines}), "
        f"over the atomic-fix cap of {assessment.max_lines} lines. This looks like "
        "an architectural change leaked into the fix. Split it: revert the "
        "over-scope parts, keep only the minimal atomic change for the current "
        "finding, and record the broader change as an architectural finding in "
        "the decision log for a follow-up."
    )


__all__ = [
    "FixScopeAssessment",
    "assess_fix_scope",
    "fix_scope_over_limit_hint",
    "git_diff_numstat",
]
