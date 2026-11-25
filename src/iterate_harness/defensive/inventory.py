"""Defensive-kernel inventory (``ih defense show`` + ``GET /api/v1/defense``).

The engine's defensive kernel is the enforcement half of code mode: every
mutating file tool is snapshotted, the project invariants are re-checked after
the edit, and a violation rolls the edit back. Until now the operator could
only observe that machinery as the *outcome* of a live run -- a refused edit
inside a transcript, or a warning in a log file -- and there was no way to
answer the two questions that decide whether the guard is worth anything:

* **What will it check?** The effective set is not just ``invariants``: a
  pre-v3.0 config silently falls back to ``validation.commands``, an explicit
  empty section disables the guard, and empty module entries are dropped.
* **Did it actually run?** A configured guard that produces zero runnable
  checks rolls every edit back; a refused command (shell metacharacters in a
  config entry) does the same. Both used to be invisible until the agent could
  no longer edit anything.

:func:`build_defense_inventory` is the read-only model shared by the CLI and
the console page. It resolves the effective guard with the *same* function the
kernel uses (``resolve_invariants``), reads the append-only defense journal
written by the kernel, and reconstructs assumption declarations/verifications
from the decision log. It never executes an invariant command and never
touches the network.

``ih defense check`` is the one mutating-ish entry point (it runs the
configured commands, exactly like the engine would) and is therefore a CLI
verb only -- the page offers its copyable command instead of spawning
subprocesses from a browser request.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iterate_harness.defensive.invariants import (
    COMMAND_METACHARS,
    SOURCE_DISABLED,
    SOURCE_INVARIANTS,
    SOURCE_NONE,
    SOURCE_VALIDATION_FALLBACK,
    ResolvedInvariants,
    command_is_safe,
    resolve_invariants,
)
from iterate_harness.defensive.journal import (
    defense_journal_summary,
    distinct_kinds,
    distinct_outcomes,
    distinct_tools,
    list_defense_events,
)

UTC = timezone.utc

#: Maximum assumptions reconstructed from the decision log (newest first).
MAX_ASSUMPTIONS = 100

#: Maximum recent journal events embedded in the inventory page.
MAX_RECENT_EVENTS = 20

_SOURCE_LABELS = {
    SOURCE_INVARIANTS: "invariants",
    SOURCE_VALIDATION_FALLBACK: "validation.commands (fallback)",
    SOURCE_DISABLED: "explicitly disabled",
    SOURCE_NONE: "not configured",
}


def _problem(kind: str, name: str, detail: str, remedy: str) -> dict[str, str]:
    return {"kind": kind, "name": name, "detail": detail, "remedy": remedy}


def _resolve_policy(project_root: Path) -> tuple[ResolvedInvariants, dict[str, Any], list[dict[str, str]]]:
    """Load the effective config and resolve the guard (never raises)."""
    from iterate_harness.iterate.config_loader import load_effective_config

    problems: list[dict[str, str]] = []
    try:
        effective = load_effective_config(project_root)
    except Exception as exc:  # noqa: BLE001 - the inventory must explain, not raise
        resolved = ResolvedInvariants()
        problems.append(
            _problem(
                "config",
                "iterate.config.yaml",
                f"cannot load the project config: {type(exc).__name__}: {exc}",
                "Fix iterate.config.yaml (or run `ih iterate init`); `ih defense show` will "
                "then resolve the effective guard.",
            )
        )
        return resolved, {"notes": [], "error": str(exc)}, problems

    resolved = resolve_invariants(effective)
    notes: list[str] = []
    if resolved.source == SOURCE_NONE:
        notes.append(
            "No invariant guard is configured: code-mode edits are not post-checked or "
            "rolled back on a failed check."
        )
    elif resolved.source == SOURCE_DISABLED:
        notes.append(
            "An explicit empty `invariants` section disables guard checks on purpose; "
            "the kernel does not fall back to validation.commands."
        )
    elif resolved.source == SOURCE_VALIDATION_FALLBACK:
        notes.append(
            "No `invariants` section; the guard falls back to `validation.commands`."
        )
    if resolved.dropped_empty_modules:
        notes.append(
            "Empty command lists are not treated as checks (dropped modules: "
            + ", ".join(sorted(resolved.dropped_empty_modules))
            + ")."
        )

    for module, commands in resolved.commands.items():
        for command in commands:
            if command_is_safe(command):
                continue
            offenders = sorted({char for char in command if char in COMMAND_METACHARS})
            config_key = (
                "invariants.commands"
                if resolved.source == SOURCE_INVARIANTS
                else "validation.commands"
            )
            detail = (
                "refused: the command contains shell metacharacters "
                + repr(offenders)
                + " and can never run; the kernel treats it as an evaluation error and "
                "rolls back every edit"
            )
            problems.append(
                _problem(
                    "command",
                    f"{module}:{command}",
                    detail,
                    f"Remove the shell metacharacters {offenders} from this {config_key} "
                    "entry (globs/quotes/chaining are not supported), or delete the entry.",
                )
            )
    for entry in resolved.ensure:
        if not entry.strip():
            problems.append(
                _problem(
                    "ensure",
                    "(empty entry)",
                    "an empty `invariants.ensure` entry targets the project root, which is a "
                    "directory, so the guard always fails",
                    "Delete the blank line in `invariants.ensure`.",
                )
            )
        elif Path(entry).is_absolute():
            problems.append(
                _problem(
                    "ensure",
                    entry,
                    "an absolute `invariants.ensure` path points outside the project root; "
                    "the guard's path assertions are project-relative",
                    "Make the `invariants.ensure` entry relative to the project root.",
                )
            )

    policy = {
        "source": resolved.source,
        "source_label": _SOURCE_LABELS.get(resolved.source, resolved.source),
        "configured": resolved.configured,
        "enabled": resolved.checks > 0,
        "ensure": list(resolved.ensure),
        "commands": {module: list(commands) for module, commands in resolved.commands.items()},
        "modules": len(resolved.commands),
        "checks": resolved.checks,
        "dropped_empty_modules": list(resolved.dropped_empty_modules),
        "notes": notes,
    }
    return resolved, policy, problems


def _assumption_rows(project_root: Path) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Reconstruct the latest state of every declared assumption.

    Assumptions are append-only pairs in the decision log (``assumption_declared``
    then ``assumption_checked``). The page wants the *current* state per
    statement, newest first, plus aggregate counts -- a declaration followed by
    a falsification must read ``falsified``, not ``declared``.
    """
    from iterate_harness.iterate.decision_log import read_entries

    latest: dict[str, dict[str, str]] = {}
    for entry in read_entries(project_root):
        if entry.type not in ("assumption_declared", "assumption_checked"):
            continue
        data = entry.data if isinstance(entry.data, dict) else {}
        statement = str(data.get("statement") or "").strip()
        if not statement:
            continue
        status = str(data.get("status") or ("declared" if entry.type == "assumption_declared" else ""))
        if entry.type == "assumption_checked":
            status = "holds" if str(status).lower() in {"holds", "true", "1"} else "falsified"
        latest[statement] = {
            "statement": statement,
            "status": status or "declared",
            "detail": str(data.get("detail") or ""),
            "timestamp": entry.timestamp,
            "round": str(entry.round),
        }

    rows = sorted(latest.values(), key=lambda row: row.get("timestamp", ""), reverse=True)
    summary = {
        "total": len(rows),
        "declared": sum(1 for row in rows if row["status"] == "declared"),
        "holds": sum(1 for row in rows if row["status"] == "holds"),
        "falsified": sum(1 for row in rows if row["status"] == "falsified"),
    }
    return rows[:MAX_ASSUMPTIONS], summary


def build_defense_inventory(project_root: str | Path) -> dict[str, Any]:
    """Build the read-only defensive-kernel inventory for *project_root*.

    Returns a plain JSON-ready dict so the Typer command and the FastAPI
    response model consume the exact same values. A broken config or journal
    becomes a ``problems`` entry or an empty page, never an exception.
    """
    root = Path(project_root).expanduser().resolve()
    _resolved, policy, problems = _resolve_policy(root)
    assumptions, assumption_summary = _assumption_rows(root)
    journal = defense_journal_summary(root)
    recent, _total = list_defense_events(root, limit=MAX_RECENT_EVENTS, offset=0)
    return {
        "project_root": str(root),
        "generated_at": datetime.now(UTC).isoformat(),
        "has_problems": bool(problems),
        "problems": problems,
        "policy": policy,
        "assumptions": assumptions,
        "assumption_summary": assumption_summary,
        "journal": journal,
        "recent_events": recent,
        "summary": {
            "checks": policy.get("checks", 0),
            "modules": policy.get("modules", 0),
            "rollbacks": journal.get("rollbacks", 0),
            "invariant_runs": journal.get("invariant_runs", 0),
            "last_verdict": journal.get("last_verdict", ""),
            "last_event_at": journal.get("last_event_at", ""),
            "assumptions": assumption_summary.get("total", 0),
            "falsified_assumptions": assumption_summary.get("falsified", 0),
        },
        "journal_kinds": distinct_kinds(root),
        "journal_outcomes": distinct_outcomes(root),
        "journal_tools": distinct_tools(root),
    }


def render_defense_inventory(inventory: dict[str, Any]) -> str:
    """Render the inventory as aligned terminal text (mirrors the console)."""
    policy = inventory.get("policy") or {}
    summary = inventory.get("summary") or {}
    assumptions = inventory.get("assumption_summary") or {}
    journal = inventory.get("journal") or {}
    lines = [
        f"iterate defensive kernel — {inventory.get('project_root', '')}",
        f"generated {inventory.get('generated_at', '')}",
        "",
        f"guard: {policy.get('source_label', 'unknown')} "
        f"({'enabled' if policy.get('enabled') else 'not running'})",
        f"checks: {summary.get('checks', 0)} "
        f"({len(policy.get('ensure') or [])} ensure + "
        f"{summary.get('modules', 0)} module command list(s))",
    ]
    for note in policy.get("notes") or []:
        lines.append(f"  note: {note}")

    ensure = policy.get("ensure") or []
    if ensure:
        lines.append("")
        lines.append("ensure:")
        for entry in ensure:
            lines.append(f"  - {entry}")

    commands = policy.get("commands") or {}
    if commands:
        lines.append("")
        lines.append("commands:")
        for module, module_commands in commands.items():
            for command in module_commands:
                lines.append(f"  [{module}] {command}")

    lines.append("")
    lines.append(
        f"journal: {journal.get('total', 0)} event(s) · "
        f"{journal.get('rollbacks', 0)} rollback(s) · "
        f"last verdict {journal.get('last_verdict') or '—'}"
    )
    lines.append(
        f"assumptions: {assumptions.get('total', 0)} tracked · "
        f"{assumptions.get('holds', 0)} hold · "
        f"{assumptions.get('falsified', 0)} falsified"
    )

    problems = inventory.get("problems") or []
    if problems:
        lines.append("")
        lines.append(f"problems ({len(problems)}):")
        for problem in problems:
            lines.append(f"  [{problem.get('kind')}] {problem.get('name')}: {problem.get('detail')}")
            if problem.get("remedy"):
                lines.append(f"      fix: {problem.get('remedy')}")

    recent = inventory.get("recent_events") or []
    if recent:
        lines.append("")
        lines.append("recent events (newest first):")
        for event in recent[:5]:
            result = event.get("outcome") or "?"
            rollback = " (rolled back)" if event.get("rolled_back") else ""
            lines.append(
                f"  {str(event.get('timestamp') or '')[:19]} "
                f"{event.get('kind')} {result}{rollback} "
                f"{event.get('tool') or '-'} {event.get('path') or ''}".rstrip()
            )
    elif journal.get("total", 0) == 0:
        lines.append("")
        lines.append(
            "No defense events recorded yet (the kernel writes one line per guard verdict)."
        )

    if not problems:
        lines.append("")
        lines.append("Run the guard now without an agent: ih defense check")
    return "\n".join(lines)


__all__ = [
    "MAX_ASSUMPTIONS",
    "MAX_RECENT_EVENTS",
    "build_defense_inventory",
    "render_defense_inventory",
]
