"""Defensive kernel coordinator for code mode (design §20.3.2).

The kernel is the *enforcement* half of code mode: while the code-mode system
prompt tells the agent to program defensively, the kernel makes the three
deterministic guarantees mechanically:

- **Atomic mutations** — every mutating file tool is snapshotted before it
  runs; a failed edit is rolled back automatically (fail-fast + atomic
  transaction).
- **Invariant guarding** — after each successful mutation the project
  invariants (``invariants.ensure`` file assertions + ``invariants.commands``
  per-module command lists, falling back to ``validation.commands`` when no
  ``invariants`` section is configured) are re-checked; a violation rolls the
  edit back and surfaces as a tool error the model must respond to.
- **Assumption audit trail** — declared assumptions are recorded in the
  decision log so falsified assumptions are visible in the run's history.

The kernel is per-query-run state: the engine constructs a fresh instance for
each ``submit_message`` in ``code`` mode and attaches it to the
:class:`~iterate_harness.engine.query.QueryContext`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from iterate_harness.defensive.assumptions import (
    record_assumption,
    record_assumption_checked,
)
from iterate_harness.defensive.invariants import (
    InvariantReport,
    check_invariants_async,
)
from iterate_harness.defensive.transaction import FileTransactionBuffer
from iterate_harness.iterate.config_loader import EffectiveConfig

log = logging.getLogger(__name__)

#: Key under which the per-query :class:`DefensiveKernel` is exposed to tools
#: through ``ToolExecutionContext.metadata`` (design §20.3.2). Tools consult
#: it to record assumptions; the engine consults it to snapshot / post-check
#: mutating file tools. Absent ⇒ the call runs without defensive enforcement.
DEFENSIVE_KERNEL_KEY = "__defensive_kernel__"


class DefensiveKernel:
    """Per-query defensive enforcement context (code mode)."""

    def __init__(
        self,
        project_root: str | Path,
        effective: EffectiveConfig | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        self._project_root = Path(project_root).resolve()
        self._buffer = FileTransactionBuffer(self._project_root)
        self._enabled = enabled
        #: Resolved invariant command source — ``invariants.commands`` when an
        #: ``invariants`` section exists in the project config, else a fallback
        #: to ``validation.commands`` (skill parity, design §6).
        self._commands: dict[str, list[str]] = {}
        self._ensure: list[str] = []
        self._configured_invariants = False
        if effective is not None:
            self._resolve_invariants(effective)
        self._last_report: InvariantReport | None = None

    @property
    def enabled(self) -> bool:
        """Return whether the kernel enforces anything (always True for code mode)."""
        return self._enabled

    @property
    def pending_mutations(self) -> list[Path]:
        """Return paths with uncommitted (snapshotted) edits."""
        return self._buffer.pending

    @property
    def last_report(self) -> InvariantReport | None:
        """Return the most recent invariant run (None before the first check)."""
        return self._last_report

    @property
    def invariants_configured(self) -> bool:
        """Return whether any invariant commands/assertions are configured."""
        return self._configured_invariants

    def _resolve_invariants(self, effective: EffectiveConfig) -> None:
        """Resolve the effective invariant command set from project config.

        Follows the skill's degradation rule: when the raw project config has
        no ``invariants`` section, invariant checking falls back to the
        configured ``validation.commands`` so pre-v3.0 projects still get
        post-edit validation for free.
        """
        raw_override = effective.override
        has_invariants = isinstance(raw_override, dict) and isinstance(
            raw_override.get("invariants"), dict
        )
        if has_invariants:
            invariants = effective.config.invariants
            if invariants is not None:
                self._commands = dict(invariants.commands)
                self._ensure = list(invariants.ensure)
            self._configured_invariants = True
        else:
            self._commands = dict(effective.config.validation.commands)
            self._ensure = []
            self._configured_invariants = False

    def snapshot(self, path: str | Path) -> None:
        """Snapshot a file before a mutating tool runs (no-op when disabled)."""
        if not self._enabled:
            return
        self._buffer.snapshot(path)

    def commit(self, path: str | Path) -> None:
        """Accept an edit whose post-check passed (drop its snapshot)."""
        self._buffer.commit(path)

    def rollback(self) -> list[Path]:
        """Restore all snapshotted edits and return the restored paths."""
        return self._buffer.rollback()

    def record_assumption(self, statement: str, status: str = "declared", detail: str = "") -> None:
        """Record an agent-declared assumption into the decision log."""
        if not self._enabled or not statement.strip():
            return
        try:
            record_assumption(self._project_root, statement, status, detail)
        except OSError as exc:
            log.warning("defensive assumption record failed: %s", exc)

    def record_assumption_checked(self, statement: str, holds: bool, detail: str = "") -> None:
        """Record the verification result of a declared assumption."""
        if not self._enabled or not statement.strip():
            return
        try:
            record_assumption_checked(self._project_root, statement, holds, detail)
        except OSError as exc:
            log.warning("defensive assumption record failed: %s", exc)

    async def check_invariants(self) -> InvariantReport:
        """Run the project invariants (never blocks the event loop)."""
        report = await check_invariants_async(
            self._project_root,
            ensure=self._ensure,
            commands=self._commands,
        )
        self._last_report = report
        return report

    async def after_mutation(
        self,
        tool_name: str,
        path: str | Path,
        *,
        success: bool,
        error_hint: str = "",
    ) -> str | None:
        """Run the defensive post-check for one mutation.

        - On a failed tool result: roll the edit back and return the error
          text (fail-fast + atomic transaction).
        - On success: run the invariant guard. A violation rolls the edit back
          and returns a message the model must respond to; otherwise the edit
          is committed.

        Returns ``None`` when the edit is safe, else a human-readable failure
        reason (already applied as a rollback).
        """
        if not self._enabled:
            return None
        if not success:
            restored = self.rollback()
            reason = f"[defensive] {tool_name} failed on {path}"
            if error_hint:
                reason += f": {error_hint}"
            if restored:
                reason += f" — rolled back {len(restored)} pending edit(s)"
            log.warning("defensive rollback after failed %s on %s", tool_name, path)
            return reason

        # Invariant guard: only meaningful when the project declares some.
        if not (self._commands or self._ensure):
            self.commit(path)
            return None
        report = await self.check_invariants()
        if report.passed:
            self.commit(path)
            return None
        restored = self.rollback()
        violations = "; ".join(
            f"{v.kind}:{v.label} ({v.detail})" for v in report.violations[:4]
        )
        reason = (
            f"[defensive] invariant violated after {tool_name} on {path}: "
            f"{violations}"
        )
        if restored:
            reason += f" — rolled back {len(restored)} pending edit(s)"
        log.warning("defensive invariant violation: %s", reason)
        return reason

    def to_metadata(self) -> dict[str, Any]:
        """Expose kernel state for tool metadata / decision-log surfacing."""
        return {
            "enabled": self._enabled,
            "invariants_configured": self._configured_invariants,
            "pending_mutations": [str(p) for p in self.pending_mutations],
            "last_invariant_passed": None if self._last_report is None else self._last_report.passed,
        }
