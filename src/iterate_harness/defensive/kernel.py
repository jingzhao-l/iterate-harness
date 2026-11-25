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
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from iterate_harness.defensive.assumptions import (
    record_assumption,
    record_assumption_checked,
)
from iterate_harness.defensive.invariants import (
    SOURCE_NONE,
    InvariantReport,
    check_invariants_async,
    report_outcome,
    resolve_invariants,
)
from iterate_harness.defensive.journal import (
    KIND_INVARIANT,
    KIND_ROLLBACK,
    OUTCOME_FAILED_TOOL,
    OUTCOME_PASS,
    OUTCOME_UNMEASURED,
    record_defense_event,
)
from iterate_harness.defensive.transaction import FileTransactionBuffer
from iterate_harness.iterate.config_loader import EffectiveConfig
from iterate_harness.iterate.decision_log import (
    # A decision that cannot enter the ledger is a refusal with a code and a remedy, not an
    # ``OSError``: since the write side became canonical, an unverifiable history or an
    # interleaved legacy line stops the append instead of being written over. These best-effort
    # boundaries must name that class too, or a declared assumption would break the edit loop
    # it exists to inform.
    DECISION_LOG_REFUSALS,
)

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
        self._invariant_source = SOURCE_NONE
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
    def last_invariant_report(self) -> InvariantReport | None:
        """The same run, under the name the loop's sanctioned reader asks for.

        ``iterate/invariant_state.read_invariant_state`` reads exactly this attribute out of the
        kernel it finds in tool metadata; without it the invariant gate could never be measured
        on a real kernel instance (only on a stub), and a verdict the loop cannot read is a
        verdict that cannot trigger anything downstream.
        """
        return self._last_report

    @property
    def invariant_ensure(self) -> list[str]:
        """The file assertions this kernel runs, in configured order.

        Published because a re-verification request cites its report by digest, and the digest
        is a claim about *every* configured check: only the runner that executed them can say
        which ones those were. Guessing them from the config at the call site would re-resolve
        the fallback rule and digest a report that is not the one that ran.
        """
        return list(self._ensure)

    @property
    def invariant_commands(self) -> dict[str, list[str]]:
        """The per-module invariant commands this kernel runs (same reason as above)."""
        return {module: list(commands) for module, commands in self._commands.items()}

    @property
    def invariants_configured(self) -> bool:
        """Return whether any invariant commands/assertions are configured."""
        return self._configured_invariants

    @property
    def invariant_source(self) -> str:
        """Where the effective guard came from (``invariants`` / fallback / …)."""
        return self._invariant_source

    def _resolve_invariants(self, effective: EffectiveConfig) -> None:
        """Resolve the effective guard with the shared resolver.

        The kernel, ``ih defense show`` and ``GET /api/v1/defense`` all call
        :func:`~iterate_harness.defensive.invariants.resolve_invariants`, so the
        inventory can never describe a guard the kernel would not run (or miss
        the fallback rule that silently replaced the configured one).
        """
        resolved = resolve_invariants(effective)
        self._commands = resolved.commands
        self._ensure = resolved.ensure
        self._configured_invariants = resolved.configured
        self._invariant_source = resolved.source

    def _record_event(
        self,
        *,
        kind: str,
        outcome: str,
        tool: str = "",
        path: str = "",
        trigger: str = "",
        reason: str = "",
        checks_run: int = 0,
        duration_ms: int = 0,
        rolled_back: bool = False,
        violations: Sequence[Any] | None = None,
    ) -> None:
        """Append one guard verdict to the shared journal (best-effort).

        Journaling is inline in the mutation path, so a failure here is a
        warning inside ``record_defense_event`` -- it must never stop an edit
        from being committed or rolled back.
        """
        record_defense_event(
            self._project_root,
            kind=kind,
            outcome=outcome,
            tool=tool,
            path=path,
            trigger=trigger,
            reason=reason,
            checks_run=checks_run,
            duration_ms=duration_ms,
            rolled_back=rolled_back,
            violations=violations,
        )

    def snapshot(self, path: str | Path) -> None:
        """Snapshot a file before a mutating tool runs (no-op when disabled)."""
        if not self._enabled:
            return
        self._buffer.snapshot(path)

    def commit(self, path: str | Path) -> None:
        """Accept an edit whose post-check passed (drop its snapshot)."""
        self._buffer.commit(path)

    def rollback(self, paths: str | Path | Iterable[str | Path] | None = None) -> list[Path]:
        """Restore snapshotted edits (``paths`` or every pending one).

        Scoped rollback lets a failed mutation revert only its own snapshot
        while sibling edits stay tracked — fail-fast without a cross-edit
        cascade (design §20.3.2).
        """
        return self._buffer.rollback(paths)

    def record_assumption(self, statement: str, status: str = "declared", detail: str = "") -> None:
        """Record an agent-declared assumption into the decision log."""
        if not self._enabled or not statement.strip():
            return
        try:
            record_assumption(self._project_root, statement, status, detail)
        except (OSError, *DECISION_LOG_REFUSALS) as exc:
            log.warning("defensive assumption record failed: %s (%s)", exc, getattr(exc, "code", "OSError"))

    def record_assumption_checked(self, statement: str, holds: bool, detail: str = "") -> None:
        """Record the verification result of a declared assumption."""
        if not self._enabled or not statement.strip():
            return
        try:
            record_assumption_checked(self._project_root, statement, holds, detail)
        except (OSError, *DECISION_LOG_REFUSALS) as exc:
            log.warning("defensive assumption record failed: %s (%s)", exc, getattr(exc, "code", "OSError"))

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
            restored = self.rollback(path)
            reason = f"[defensive] {tool_name} failed on {path}"
            if error_hint:
                reason += f": {error_hint}"
            if restored:
                reason += f" — rolled back {len(restored)} pending edit(s)"
            log.warning("defensive rollback after failed %s on %s", tool_name, path)
            self._record_event(
                kind=KIND_ROLLBACK,
                outcome=OUTCOME_FAILED_TOOL,
                tool=tool_name,
                path=str(path),
                trigger="tool-failure",
                reason=reason,
                rolled_back=bool(restored),
            )
            return reason

        # Invariant guard: only meaningful when the project declares some.
        if not (self._commands or self._ensure):
            self.commit(path)
            return None
        started = time.monotonic()
        report = await self.check_invariants()
        duration_ms = int((time.monotonic() - started) * 1000)
        if report.passed and report.checks_run > 0:
            self.commit(path)
            self._record_event(
                kind=KIND_INVARIANT,
                outcome=OUTCOME_PASS,
                tool=tool_name,
                path=str(path),
                trigger="invariant-pass",
                checks_run=report.checks_run,
                duration_ms=duration_ms,
            )
            return None
        if report.checks_run == 0:
            # Configured but zero checks actually ran: a green light from
            # nothing measured is not a pass, and committing here would let
            # every edit through under an empty/placeholder invariant set.
            restored = self.rollback(path)
            reason = (
                f"[defensive] invariant configuration produced zero runnable checks "
                f"after {tool_name} on {path}; an unmeasured guard cannot pass — "
                "the edit was rolled back"
            )
            if restored:
                reason += f" (rolled back {len(restored)} pending edit(s))"
            log.warning("defensive invariant guard ran zero checks: %s", reason)
            self._record_event(
                kind=KIND_ROLLBACK,
                outcome=OUTCOME_UNMEASURED,
                tool=tool_name,
                path=str(path),
                trigger="invariant-unmeasured",
                reason=reason,
                rolled_back=bool(restored),
                duration_ms=duration_ms,
            )
            return reason
        restored = self.rollback(path)
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
        self._record_event(
            kind=KIND_ROLLBACK,
            outcome=report_outcome(report),
            tool=tool_name,
            path=str(path),
            trigger="invariant-violation",
            reason=reason,
            checks_run=report.checks_run,
            duration_ms=duration_ms,
            rolled_back=bool(restored),
            violations=report.violations,
        )
        return reason

    def to_metadata(self) -> dict[str, Any]:
        """Expose kernel state for tool metadata / decision-log surfacing."""
        return {
            "enabled": self._enabled,
            "invariants_configured": self._configured_invariants,
            "invariant_source": self._invariant_source,
            "pending_mutations": [str(p) for p in self.pending_mutations],
            "last_invariant_passed": None if self._last_report is None else self._last_report.passed,
        }
