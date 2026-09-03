"""Project-level invariant guard for code mode (design §20.3.2).

Ports the skill's ``iterate invariant`` semantics (``iterate_cli/guard.py``)
into the harness kernel so that in ``code`` mode every mutation is followed by
an incremental invariant check: declared file assertions (``ensure``) plus
per-module command lists (``commands``).

Security posture is identical to the skill baseline:

- Commands are only ever executed when they EXACTLY match a configured
  ``invariants.commands`` / ``validation.commands`` entry (after trim). No
  command is composed, prefixed, or parameterised here.
- Fail-closed metachar enforcement: a command containing shell-chaining
  metacharacters (or empty after trim) is refused at execution time, so a
  hand-edited or drift-polluted config can never chain extra shell through
  ``subprocess.run(..., shell=True)``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from iterate_harness.iterate.validate import DEFAULT_TIMEOUT_MS, run_command

log = logging.getLogger(__name__)

#: Shell-chaining metacharacters that must never appear in an executable
#: command string (same canonical set as ``iterate_cli/personalize.py``'s
#: FORBIDDEN_COMMAND_CHARS / ``iterate_cli/guard.py``'s COMMAND_METACHARS).
COMMAND_METACHARS: frozenset[str] = frozenset(
    (";", "|", "&", "`", "$", ">", "<", "\n", "\r",
     "\\", "#", "*", "?", "~", '"', "'",
     "(", ")", "[", "]", "{", "}"),
)


@dataclass
class InvariantViolation:
    """One violated invariant (a reason to roll back the triggering edit)."""

    kind: str  # "ensure" | "command" | "refused"
    label: str
    detail: str


@dataclass
class InvariantReport:
    """Aggregated result of running the project invariants."""

    passed: bool = True
    checks_run: int = 0
    violations: list[InvariantViolation] = field(default_factory=list)


def command_is_safe(command: str) -> bool:
    """True when ``command`` may be executed: non-empty and metachar-free.

    Empty/whitespace-only commands are rejected too, so a stray blank entry
    cannot yield a false "exit 0 = invariant holds" green light.
    """
    return bool(command.strip()) and not any(ch in COMMAND_METACHARS for ch in command)


def check_invariants(
    project_root: str | Path,
    *,
    ensure: list[str] | None = None,
    commands: dict[str, list[str]] | None = None,
    dry_run: bool = False,
) -> InvariantReport:
    """Evaluate the project invariants deterministically.

    Args:
        project_root: Working directory for path assertions and commands.
        ensure: File-existence assertions (relative to project root).
        commands: Per-module invariant command lists (exact-match strings).
        dry_run: When True, preview commands without executing anything.

    Returns:
        An :class:`InvariantReport`; ``passed`` is False when any assertion is
        missing, any command exits non-zero, or any command is refused for
        being unsafe.
    """
    report = InvariantReport()
    root = Path(project_root)

    for entry in ensure or []:
        target = root / entry
        if target.is_file():
            report.checks_run += 1
        elif target.is_dir():
            report.checks_run += 1
            report.violations.append(
                InvariantViolation(
                    kind="ensure",
                    label=f"ensure:{entry}",
                    detail=f"expected a file, found a directory: {entry}",
                )
            )
            report.passed = False
        else:
            report.checks_run += 1
            report.violations.append(
                InvariantViolation(
                    kind="ensure",
                    label=f"ensure:{entry}",
                    detail=f"missing file: {entry}",
                )
            )
            report.passed = False

    for module, module_commands in (commands or {}).items():
        for command in module_commands:
            if not command_is_safe(command):
                report.checks_run += 1
                report.violations.append(
                    InvariantViolation(
                        kind="refused",
                        label=f"{module}:{command}",
                        detail="refused: unsafe command (shell metacharacter or empty)",
                    )
                )
                report.passed = False
                continue
            if dry_run:
                report.checks_run += 1
                continue
            result = run_command(command, str(root), DEFAULT_TIMEOUT_MS)
            report.checks_run += 1
            if result.exit_code != 0:
                tail = " | ".join(
                    line
                    for line in (result.stdout + result.stderr).splitlines()[-3:]
                )
                report.violations.append(
                    InvariantViolation(
                        kind="command",
                        label=f"{module}:{command}",
                        detail=f"exit {result.exit_code} | {tail[:400]}",
                    )
                )
                report.passed = False
    return report


async def check_invariants_async(
    project_root: str | Path,
    *,
    ensure: list[str] | None = None,
    commands: dict[str, list[str]] | None = None,
    dry_run: bool = False,
) -> InvariantReport:
    """Async wrapper of :func:`check_invariants` (never blocks the event loop).

    Command execution is synchronous subprocess work; it is offloaded to a
    worker thread so a slow invariant command cannot stall the agent loop.
    """
    return await asyncio.to_thread(
        check_invariants,
        project_root,
        ensure=ensure,
        commands=commands,
        dry_run=dry_run,
    )
