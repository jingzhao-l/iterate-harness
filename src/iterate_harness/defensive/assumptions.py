"""Assumption declaration and validation for code mode (design §20.3.2).

"Minimize assumptions" is the first defensive-programming principle: before
every tool call the agent states what must hold (file exists / git clean /
dependency installed / API shape unchanged). The harness records each declared
assumption in the append-only decision log and, when the kernel verifies it,
records the outcome too — so a falsified assumption is visible in the run's
audit trail instead of silently failing later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from iterate_harness.iterate.decision_log import append_entry
from iterate_harness.iterate.types import DecisionLogEntry

UTC = timezone.utc

#: Newest decision-log entry type for assumption declarations.
ASSUMPTION_DECLARED = "assumption_declared"
#: Decision-log entry type for a verified (true/false) assumption.
ASSUMPTION_CHECKED = "assumption_checked"


def record_assumption(
    project_root: str | Path,
    statement: str,
    status: str = "declared",
    detail: str = "",
) -> tuple[int, Path]:
    """Append a declared assumption to the project decision log.

    Args:
        project_root: Project root (the ``.iterate/`` log lives here).
        statement: The assumption text (e.g. ``src/main.py exists``).
        status: ``declared`` / ``holds`` / ``falsified``.
        detail: Optional supporting detail (e.g. the check that confirmed it).

    Returns:
        ``(entry_count_after_append, log_file_path)``.
    """
    entry = DecisionLogEntry(
        timestamp=datetime.now(UTC).isoformat(),
        round=0,
        type=ASSUMPTION_DECLARED if status == "declared" else ASSUMPTION_CHECKED,
        data={"statement": statement, "status": status, "detail": detail},
    )
    return append_entry(project_root, entry)


def record_assumption_checked(
    project_root: str | Path,
    statement: str,
    holds: bool,
    detail: str = "",
) -> tuple[int, Path]:
    """Record that a previously declared assumption was verified.

    ``holds=False`` is the "falsified" case — the kernel treats it as a
    fail-fast signal and stops building on the broken premise.
    """
    entry = DecisionLogEntry(
        timestamp=datetime.now(UTC).isoformat(),
        round=0,
        type=ASSUMPTION_CHECKED,
        data={
            "statement": statement,
            "status": "holds" if holds else "falsified",
            "detail": detail,
        },
    )
    return append_entry(project_root, entry)
