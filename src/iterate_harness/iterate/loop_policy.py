"""Engine-level iterate loop policy (convergence enforcement).

The upstream agent loop has no round concept (design §11.3.2 finding #2:
hooks cannot control the loop). This module is the deterministic policy
object injected via ``QueryContext.iterate_policy`` (design §11.4.1 kernel
fix #1) and consulted by ``run_query`` once per turn AFTER tool results are
appended:

- The ``iterate_review`` tool writes its aggregate state into
  ``tool_metadata["iterate_state"]`` (same mechanism as the kernel's
  ``task_focus_state``).
- :meth:`IterateLoopPolicy.on_turn_end` diffs that state against the last
  observed one. When a NEW aggregate batch arrives it counts the round into the
  run's breaker level, feeds the round to
  :func:`~iterate_harness.iterate.convergence_policy.convergence_decision` and
  emits a :class:`~iterate_harness.engine.stream_events.ReviewProgressEvent`.
  STOP happens on the kernel's ``halt``/``converged``, on the breaker tripping,
  or on the round cap; anything else injects the canonical next-round
  instruction. The reviewer's own ``converged`` flag is a report, not a verdict:
  when a gate was never measured the kernel is not consulted and the loop does
  not claim convergence (owner decision 2026-10-13 — see
  :meth:`IterateLoopPolicy._kernel_decision` for the accepted consequence).
- Usage snapshots are billed into :class:`~iterate_harness.iterate.cost.CostMeter`
  so every progress event carries the running USD cost.

All logic is pure and unit-testable; the engine only wires it in.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import cast

from ..api.usage import UsageSnapshot
from . import prompts
from .convergence_policy import (
    BREAKER_TRIPPED_LEVEL,
    ConvergenceDecision,
    ConvergencePolicyError,
    InvariantState,
    MIN_ROUND,
    RoundState,
    convergence_decision,
)
from .cost import CostMeter

ITERATE_STATE_KEY = "iterate_state"

#: Width (seconds) of the rolling window for ``max_turns_per_minute``.
RATE_LIMIT_WINDOW_SECONDS = 60

#: Why the loop auto-paused (as opposed to a manual Esc pause). The engine
#: shows a tailored intervention menu title per reason.
PAUSE_REASON_ESC = "esc"
PAUSE_REASON_STALL = "stall"
PAUSE_REASON_BUDGET = "budget"


@dataclass
class LoopDecision:
    """What the engine should do after one turn."""

    stop_reason: str | None = None
    inject_message: str | None = None
    progress: object | None = None  # ReviewProgressEvent (typed in engine layer)
    # Set when the user requested a pause (Esc): the engine must surface the
    # intervention menu instead of injecting ``inject_message`` right away.
    # ``inject_message`` keeps the original next-round instruction so the
    # "resume" answer can proceed unchanged.
    paused: bool = False
    # Why the loop auto-paused (stall detection / budget headroom); None when
    # the pause came from a manual Esc request. The engine picks the menu
    # title from this (design §18.5).
    pause_reason: str | None = None


@dataclass
class AggregateSnapshot:
    """Counters extracted from one iterate_review aggregate call."""

    rounds_seen: int = 0
    total_findings: int = 0
    findings_by_round: list[int] = field(default_factory=list)
    #: The review report's own claim. Relayed to the UI as what the reviewer said; it is NOT the
    #: stop condition — that is ``_kernel_decision``, which refuses to answer without a measured
    #: invariant verdict and a measured evidence attestation.
    converged: bool = False
    by_dimension: dict[str, int] = field(default_factory=dict)
    mode: str = "dry-run"
    # Per-dimension token-budget state published by the aggregate tool
    # (empty when no budgets are configured or no usage was reported).
    exhausted_dimensions: list[str] = field(default_factory=list)
    all_dimensions_exhausted: bool = False
    # The convergence contract's invariant verdict, or None when nothing measured
    # it. Never defaulted: see iterate/invariant_state.py for why absent beats
    # "unmeasured" beats "pass" here.
    #
    # Typed as the contract's literal union, not `str`: `_read_invariant_state`
    # is the only producer and it rejects anything outside those four values, so
    # a plain `str` here would promise more than the snapshot can actually hold.
    invariant_state: InvariantState | None = None
    # The convergence contract's evidence attestation, or None when the project declares no
    # evidence gate. Absent means the kernel cannot be consulted at all: the contract has no
    # "unknown" slot for this field, so inventing either bool would be a verdict nobody
    # measured (see iterate/loop_gates.py).
    evidence_complete: bool | None = None
    # Reviewer-reported cumulative token totals per dimension (v1.2-c).
    # Subagent usage bills outside the main API stream, so these are reported
    # figures the policy relays into the CostMeter (monotonic running totals).
    dimension_usage: dict[str, int] = field(default_factory=dict)
    # Optional per-dimension input/output split (v1.26): billed at exact
    # prices instead of the blended average.
    dimension_usage_io: dict[str, dict[str, int]] = field(default_factory=dict)
    # The kernel dimensionContext verdict line (scenario ②): published by the aggregate
    # tool when both halves measured something — the published plan's dimensions and the
    # ledger's per-dimension decision counts. ``None`` means nobody could produce the
    # verdict, and the compaction attachment prints nothing rather than an invented 0/0.
    dimension_coverage: str | None = None


def read_state(tool_metadata: dict[str, object] | None) -> AggregateSnapshot | None:
    """Extract the latest aggregate snapshot from tool metadata (defensively).

    Any malformed shape returns ``None`` — the policy must never crash the
    engine on bad state.
    """
    try:
        return _read_state_unsafe(tool_metadata)
    except (TypeError, ValueError):
        return None


def _read_state_unsafe(tool_metadata: dict[str, object] | None) -> AggregateSnapshot | None:
    if not isinstance(tool_metadata, dict):
        return None
    state = tool_metadata.get(ITERATE_STATE_KEY)
    if not isinstance(state, dict):
        return None
    by_dim_raw = state.get("by_dimension")
    by_dim = (
        {
            str(k): max(0, int(v))
            for k, v in by_dim_raw.items()
            if isinstance(v, int) and not isinstance(v, bool)
        }
        if isinstance(by_dim_raw, dict)
        else {}
    )
    fbr_raw = state.get("findings_by_round")
    fbr = [int(x) for x in fbr_raw] if isinstance(fbr_raw, list) else []
    exhausted_raw = state.get("exhausted_dimensions")
    exhausted = [str(x) for x in exhausted_raw if isinstance(x, str)] if isinstance(exhausted_raw, list) else []
    usage_raw = state.get("dimension_usage")
    usage = (
        {str(k): max(0, int(v)) for k, v in usage_raw.items() if isinstance(v, int) and not isinstance(v, bool)}
        if isinstance(usage_raw, dict)
        else {}
    )
    usage_io_raw = state.get("dimension_usage_io")
    usage_io = _clean_usage_io(usage_io_raw)
    coverage_raw = state.get("dimension_coverage")
    return AggregateSnapshot(
        rounds_seen=int(state.get("rounds_seen", 0) or 0),
        total_findings=int(state.get("total_findings", 0) or 0),
        findings_by_round=fbr,
        converged=bool(state.get("converged", False)),
        by_dimension=by_dim,
        mode=str(state.get("mode", "dry-run")),
        invariant_state=_read_invariant_state(state.get("invariant_state")),
        evidence_complete=_read_evidence_complete(state.get("evidence_complete")),
        exhausted_dimensions=exhausted,
        all_dimensions_exhausted=bool(state.get("all_dimensions_exhausted", False)),
        dimension_usage=usage,
        dimension_usage_io=usage_io,
        # Non-string or blank values are treated as absent: the attachment must not
        # render a verdict a producer did not actually measure.
        dimension_coverage=coverage_raw if isinstance(coverage_raw, str) and coverage_raw.strip() else None,
    )


def _read_invariant_state(raw: object) -> InvariantState | None:
    """Accept only the four contract verdicts; anything else stays absent.

    A producer that misspells the value must not silently upgrade "nobody looked"
    into a verdict the convergence policy can act on.
    """
    from .invariant_state import INVARIANT_STATE_VALUES

    if not isinstance(raw, str) or raw not in INVARIANT_STATE_VALUES:
        return None
    # The membership test above is the guard; the cast only says what that guard
    # already proves at runtime. mypy cannot narrow `str` through `in` against a
    # tuple of literals, so the type must be stated for it explicitly.
    return cast(InvariantState, raw)


def _read_evidence_complete(raw: object) -> bool | None:
    """Accept only a real bool; anything else leaves the attestation absent.

    ``bool`` is a subclass of ``int``, so a producer that wrote ``1`` would otherwise read as
    "complete", and ``bool("false")`` is True — both are verdicts nobody measured. Absent here
    means the kernel is not consulted (see ``IterateLoopPolicy._kernel_decision``), not that the
    round failed to evidence something.
    """
    return raw if isinstance(raw, bool) else None


def _clean_usage_io(raw: object) -> dict[str, dict[str, int]]:
    """Defensively parse the per-dimension input/output split from state."""
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, dict[str, int]] = {}
    for dim, split in raw.items():
        if not isinstance(split, dict):
            continue
        in_raw = split.get("input")
        out_raw = split.get("output")
        if not (isinstance(in_raw, int) and not isinstance(in_raw, bool)):
            in_raw = 0
        if not (isinstance(out_raw, int) and not isinstance(out_raw, bool)):
            out_raw = 0
        if in_raw <= 0 and out_raw <= 0:
            continue
        cleaned[str(dim)] = {"input": max(0, in_raw), "output": max(0, out_raw)}
    return cleaned


@dataclass
class IterateLoopPolicy:
    """Deterministic convergence policy for one iterate run.

    ``max_review_rounds`` caps the loop; ``stop_on_convergence`` can be
    disabled by callers that want the model to finish its own summary turn
    (the stop notice is then still injected, but the loop continues).
    """

    mode: str = "dry-run"
    max_review_rounds: int = 3
    stop_on_convergence: bool = True
    # Per-fix diff approval gate (Settings.iterate.require_fix_approval):
    # when True, the engine's tool executor routes mutating file tool calls
    # through the interactive permission prompt with a diff preview while a
    # normal-mode iterate loop is active.
    require_fix_approval: bool = False
    price_overrides: dict[str, tuple[float, float]] = field(default_factory=dict)
    # Whole-run token budget (from iterate.config.yaml ``token_budget``):
    # once the main-loop usage exceeds it the loop hard-stops and steers the
    # model toward the closing report. ``None`` disables the cap.
    total_token_budget: int | None = None
    # Whole-run monetary budget (USD): once the accumulated cost exceeds it
    # the loop hard-stops. ``None`` disables the cap.
    budget_usd: float | None = None
    # Turn-level rate cap (requests per minute) for long-running loops:
    # when exceeded, the policy injects a backoff message instead of the
    # normal next-round instruction. ``None`` disables throttling.
    max_turns_per_minute: int | None = None
    # Session workspace isolation (design §11.3.2 finding #7): when True and
    # a normal-mode loop is active, the engine runs fix rounds inside a
    # dedicated git worktree (merged on success, dropped on abnormal stop).
    worktree_isolation: bool = False
    # Esc intervention channel (design §11.2.1): set externally by the
    # backend interrupt path while an iterate loop is running; consumed at
    # the next round boundary.
    pause_requested: bool = False
    # Stall-awareness (design §18.5): pause after this many consecutive rounds
    # with zero new findings (and no explicit converged flag) and ask the user
    # how to continue — resume / skip / narrow / stop. ``0`` disables the
    # feature, preserving the historical hard-stop on the first zero round.
    stall_pause_rounds: int = 2
    # Budget-headroom pause (design §18.5): before starting another round,
    # pause when the remaining token/USD budget can't cover at least this many
    # rounds at the recent burn rate, asking whether to top up / stop.
    # ``0`` disables the feature.
    budget_pause_min_rounds: int = 1
    _rounds_seen: int = 0
    _emitted_progress: int = 0
    # Rolling window of turn timestamps for rate limiting (seconds since
    # epoch). Pruned to ``RATE_LIMIT_WINDOW_SECONDS`` on each check.
    _turn_timestamps: list[float] = field(default_factory=list)
    # Human-in-the-loop nudges queued by the WebUI (design §18.1 "督促注入"):
    # drained into the next-round instruction at the next round boundary so
    # a user's urge to keep going is honored even mid-round.
    _pending_nudges: list[str] = field(default_factory=list)
    # Stall detection: consecutive rounds with zero new findings (design §18.5).
    _consecutive_zero_rounds: int = 0
    # The run's circuit-breaker level, fed to the kernel as ``breakerLevel`` (3 trips). Owned by
    # the loop because it counts the loop's own no-progress rounds — a round cannot measure the
    # escalation of the run inside it, which is why ``iterate/loop_gates.py`` declines to produce
    # it. Increments on any round that found nothing new, resets on a round that found something.
    _breaker_level: int = 0
    # Recent single-round consumption, used for budget-headroom estimation.
    _last_round_tokens: int = 0
    _last_round_cost_usd: float = 0.0
    # Backing store for the :attr:`cost_meter` property — always filled by
    # ``__post_init__``, so the property is non-None in practice.
    _cost_meter: CostMeter | None = field(default=None)

    @property
    def cost_meter(self) -> CostMeter:
        """Return the run's cost meter (built in ``__post_init__``).

        Exposed as a typed property so the field's None default is invisible
        to consumers without a ``type: ignore``: ``__post_init__`` guarantees
        the backing store is populated before any consumer runs.
        """
        meter = self._cost_meter
        if meter is None:
            raise RuntimeError("iterate loop policy cost meter not initialized")
        return meter

    def __post_init__(self) -> None:
        if self._cost_meter is None:
            self._cost_meter = CostMeter(price_overrides=self.price_overrides)

    @property
    def breaker_level(self) -> int:
        """How many consecutive rounds this run has watched end with nothing new (max 3)."""
        return self._breaker_level

    def request_pause(self) -> None:
        """Ask the loop to pause at the next round boundary (user pressed Esc)."""
        self.pause_requested = True

    def clear_pause(self) -> None:
        """Drop a pending pause (fresh submit / stale request)."""
        self.pause_requested = False

    def inject_nudge(self, content: str) -> None:
        """Queue a user nudge to inject at the next round boundary.

        The nudge is prepended to the canonical next-round instruction (design
        §18.1 "督促注入"), so the model sees the user's request before it
        continues. Empty content is ignored.
        """
        stripped = content.strip()
        if not stripped:
            return
        self._pending_nudges.append(stripped)

    def on_turn_end(
        self,
        tool_metadata: dict[str, object] | None,
        usage: UsageSnapshot,
        model: str,
    ) -> LoopDecision:
        """Evaluate one completed turn; return the engine decision."""
        tokens_before = self.cost_meter.total_tokens
        usd_before = self.cost_meter.total_cost_usd
        self.cost_meter.accumulate(usage, model)
        # Track the most recent single-round consumption so the budget-headroom
        # pause can compare the remaining budget against the actual burn rate.
        self._last_round_tokens = max(0, self.cost_meter.total_tokens - tokens_before)
        self._last_round_cost_usd = max(0.0, self.cost_meter.total_cost_usd - usd_before)
        budget_stop = self._budget_stop_reason()
        if budget_stop is not None:
            snapshot = read_state(tool_metadata) or AggregateSnapshot(mode=self.mode)
            self._record_dimension_usage(snapshot)
            return self._stop_decision(
                snapshot,
                budget_stop,
                self._build_progress(snapshot, usage, model, converged=False),
            )
        current = read_state(tool_metadata)
        if current is None or current.rounds_seen <= self._rounds_seen:
            # No NEW aggregate this turn → nothing iterate-specific to do.
            return LoopDecision()
        self._rounds_seen = current.rounds_seen
        self._emitted_progress += 1
        self._record_dimension_usage(current)
        # The breaker reflects this round before anything judges it: the kernel's rule 1 reads
        # the run's own escalation, so the level must not lag the round it is judging.
        self._advance_breaker(current)
        kernel = self._kernel_decision(current)
        progress = self._build_progress(
            current, usage, model, converged=kernel is not None and kernel.action == "converged"
        )
        return self._decide(current, progress, kernel)

    def _record_dimension_usage(self, snapshot: AggregateSnapshot) -> None:
        """Relay reviewer-reported per-dimension token usage into the meter.

        The aggregate tool reports each dimension's RUNNING total, so the
        meter keeps the monotonic max (never double-counts). Dimensions with
        an input/output split are relayed via the precise path.
        """
        for dimension, split in snapshot.dimension_usage_io.items():
            self.cost_meter.record_dimension_usage(
                dimension, split.get("input", 0), split.get("output", 0)
            )
        for dimension, tokens in snapshot.dimension_usage.items():
            self.cost_meter.record_dimension_total(dimension, tokens)

    def _budget_stop_reason(self) -> str | None:
        """Return the budget stop reason (token and/or USD), or None within budget."""
        reasons: list[str] = []
        token_budget = self.total_token_budget
        if token_budget is not None:
            used_tokens = self.cost_meter.total_tokens
            if used_tokens > token_budget:
                reasons.append(f"token budget exhausted ({used_tokens:,}/{token_budget:,} tokens)")
        usd_budget = self.budget_usd
        if usd_budget is not None:
            used_usd = self.cost_meter.total_cost_usd
            if used_usd > usd_budget:
                reasons.append(f"monetary budget exhausted (${used_usd:.4f}/${usd_budget:.4f})")
        return "; ".join(reasons) if reasons else None

    def before_request(self, now: float | None = None) -> float:
        """Consulted by the engine before each main-loop API request.

        Returns the number of seconds the engine must sleep before issuing the
        request so the per-minute turn cap (``max_turns_per_minute``) is
        honored, then records the (deferred) request timestamp. Returns ``0.0``
        when no cap is configured or the loop is within the limit.
        """
        timestamp = time.monotonic() if now is None else now
        delay = self._throttle_delay(timestamp)
        if self.max_turns_per_minute is not None and self.max_turns_per_minute > 0:
            # Record the request at its actual issue time so a throttled request
            # counts toward the cap from when it is finally sent.
            self._turn_timestamps.append(timestamp + delay)
            # Bound the window: drop timestamps that can no longer count toward
            # the cap so a long loop never grows the list unbounded (also keeps
            # ``_throttle_delay`` cheap).
            window_start = timestamp + delay - RATE_LIMIT_WINDOW_SECONDS
            self._turn_timestamps = [t for t in self._turn_timestamps if t >= window_start]
        return delay

    def rate_limit_engaged(self, now: float | None = None) -> bool:
        """True while the policy is actively throttling (a cap is configured and
        the request rate is at or above it)."""
        return self._throttle_delay(time.monotonic() if now is None else now) > 0

    def _throttle_delay(self, now: float) -> float:
        """Seconds until the oldest in-window turn falls out (0 when within cap)."""
        cap = self.max_turns_per_minute
        if cap is None or cap <= 0 or not self._turn_timestamps:
            return 0.0
        window_start = now - RATE_LIMIT_WINDOW_SECONDS
        recent = [t for t in self._turn_timestamps if t >= window_start]
        if len(recent) < cap:
            return 0.0
        oldest = min(recent)
        return max(0.0, oldest + RATE_LIMIT_WINDOW_SECONDS - now)

    # -- internals --------------------------------------------------------

    def _build_progress(
        self, snapshot: AggregateSnapshot, usage: UsageSnapshot, model: str, *, converged: bool
    ) -> object:
        # Imported lazily to avoid a circular import with the engine package.
        from ..engine.stream_events import ReviewProgressEvent

        new_findings = snapshot.findings_by_round[-1] if snapshot.findings_by_round else 0
        return ReviewProgressEvent(
            round=snapshot.rounds_seen,
            new_findings=new_findings,
            total_findings=snapshot.total_findings,
            per_dimension=dict(snapshot.by_dimension),
            converged=converged,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=self.cost_meter.total_cost_usd,
            mode=snapshot.mode,
            dimension_cost_usd=self.cost_meter.dimension_cost_usd(model),
        )

    def _advance_breaker(self, snapshot: AggregateSnapshot) -> None:
        """Count this round's outcome into the run's breaker level (capped at the trip)."""
        last_new = snapshot.findings_by_round[-1] if snapshot.findings_by_round else 0
        if last_new == 0:
            self._breaker_level = min(BREAKER_TRIPPED_LEVEL, self._breaker_level + 1)
        else:
            self._breaker_level = 0

    def kernel_blockers(self, snapshot: AggregateSnapshot) -> list[str]:
        """Why the kernel cannot be consulted for this round; empty when it can.

        Surfaced in the round-cap stop reason: a run that used to halt as "converged" now spends
        its rounds, and the operator is owed the actual cause instead of a bare cap number.
        """
        blockers: list[str] = []
        if snapshot.rounds_seen < MIN_ROUND:
            blockers.append("no round has been counted yet")
        if snapshot.invariant_state is None:
            blockers.append("no invariant verdict was measured")
        if snapshot.evidence_complete is None:
            blockers.append("no evidence attestation was made")
        return blockers

    def _validated_snapshot(self, snapshot: AggregateSnapshot) -> tuple[InvariantState, bool] | None:
        """``(invariant_state, evidence_complete)`` when both gates were measured.

        The exact opposite of :meth:`kernel_blockers`: that method returns the
        reasons for declining, this one returns the narrowed values that make
        asking the kernel safe. The two answers must agree for the same snapshot,
        and :meth:`kernel_blockers` is what the operator sees when they do not.

        The round-cap check is part of the condition here too, so "the cap was
        reached" and "every gate was measured" stay a single test -- the values
        are only useful once a full round has been counted.

        Each field is read into its own local before the ``None`` check. Mypy
        narrows locals from that; narrowing a dataclass attribute across a
        ``return None`` branch is what the two ``arg-type`` errors were about.
        The tuple is therefore annotated without ``None``, which is how the
        kernel arguments get their exact contract types with no cast and no
        assert standing in for a guard that was already present.
        """
        if snapshot.rounds_seen < MIN_ROUND:
            return None
        invariant_state = snapshot.invariant_state
        if invariant_state is None:
            return None
        evidence_complete = snapshot.evidence_complete
        if evidence_complete is None:
            return None
        return invariant_state, evidence_complete

    def _kernel_decision(self, snapshot: AggregateSnapshot) -> ConvergenceDecision | None:
        """Ask the kernel what this round decides, or decline when a gate was never measured.

        Every input has to exist as a measurement — see :meth:`kernel_blockers`. The contract also
        refuses a `None` at construction, so declining here is not what keeps a fabricated verdict
        out; it is what keeps the *reason* the loop kept running honest and nameable.
        """
        validated = self._validated_snapshot(snapshot)
        if validated is None:
            return None
        invariant_state, evidence_complete = validated
        last_new = snapshot.findings_by_round[-1] if snapshot.findings_by_round else 0
        try:
            return convergence_decision(
                RoundState(
                    round=snapshot.rounds_seen,
                    max_rounds=self.max_review_rounds,
                    invariant_state=invariant_state,
                    open_findings=last_new,
                    evidence_complete=evidence_complete,
                    breaker_level=self._breaker_level,
                )
            )
        except ConvergencePolicyError:
            # A state the contract refuses is a state the loop may not call converged either.
            return None

    @staticmethod
    def _stop_reason_for(kernel: ConvergenceDecision) -> str:
        """Map a kernel halt onto the reason strings reports and CI already parse."""
        if kernel.reason == "all-gates-green":
            return "converged"
        if kernel.reason == "round-budget-exhausted":
            return f"round cap reached ({kernel.max_rounds})"
        if kernel.reason == "circuit-breaker-tripped":
            return "circuit breaker tripped (level 3)"
        if kernel.reason == "invariant-check-errored":
            return "invariant check errored (the gate did not run to a verdict)"
        return f"convergence halted ({kernel.reason})"

    def _decide(
        self, snapshot: AggregateSnapshot, progress: object, kernel: ConvergenceDecision | None
    ) -> LoopDecision:
        last_new = snapshot.findings_by_round[-1] if snapshot.findings_by_round else 0
        # The breaker is the loop's own limit, so it holds whether or not the kernel could be
        # consulted — the spinning, gateless run is exactly the one that cannot feed the contract.
        if self._breaker_level >= BREAKER_TRIPPED_LEVEL:
            return self._stop_decision(snapshot, "circuit breaker tripped (level 3)", progress)
        # Convergence is the kernel's call now, never the reviewer's self-report: an action of
        # fix/verify means "keep working", and an unconsultable state means nobody may claim a
        # green stop at all.
        if kernel is not None and kernel.action in ("halt", "converged"):
            return self._stop_decision(snapshot, self._stop_reason_for(kernel), progress)
        if snapshot.rounds_seen >= self.max_review_rounds:
            reason = f"round cap reached ({self.max_review_rounds})"
            blockers = self.kernel_blockers(snapshot)
            if blockers:
                reason += " — kernel not consulted: " + "; ".join(blockers)
            return self._stop_decision(snapshot, reason, progress)
        if snapshot.all_dimensions_exhausted and snapshot.exhausted_dimensions:
            return self._stop_decision(
                snapshot,
                "all dimension token budgets exhausted ("
                + ", ".join(snapshot.exhausted_dimensions)
                + ")",
                progress,
            )
        # Budget-headroom pause (design §18.5): the remaining token/USD budget
        # can no longer cover a full round at the recent burn rate. Pause and
        # ask the user whether to top up / stop before continuing, instead of
        # letting the next round hard-stop against the budget.
        budget_reason = self._budget_pause_reason()
        if budget_reason is not None:
            return LoopDecision(
                # When the user resumes, the engine re-injects this message into
                # the conversation (design §11.4.1). The model must receive the
                # canonical next-round instructions, not the human-facing pause
                # menu text — otherwise a resume derails the loop into a
                # confused reply. The notice is surfaced by the engine UI.
                inject_message=self._build_next_round_message(snapshot),
                progress=progress,
                paused=True,
                pause_reason=PAUSE_REASON_BUDGET,
            )
        # Stall-awareness (design §18.5): consecutive rounds with zero new
        # findings and no explicit converged flag suggest the loop is going in
        # circles. After ``stall_pause_rounds`` such rounds, pause and ask the
        # user (resume / skip / narrow / stop) instead of declaring "converged"
        # on their behalf.
        if last_new == 0:
            if self.stall_pause_rounds <= 0:
                # Feature disabled: hard-stop, but never CALL it converged —
                # the reviewer did not claim convergence and the report may
                # still contain open findings. The reason string is surfaced in
                # the UI and consumed by report/CI consumers as success.
                return self._stop_decision(
                    snapshot, "stalled: 0 new findings in the last round", progress
                )
            self._consecutive_zero_rounds += 1
            if self._consecutive_zero_rounds >= self.stall_pause_rounds:
                self._consecutive_zero_rounds = 0
                return LoopDecision(
                    inject_message=self._build_next_round_message(snapshot),
                    progress=progress,
                    paused=True,
                    pause_reason=PAUSE_REASON_STALL,
                )
        else:
            self._consecutive_zero_rounds = 0
        decision = LoopDecision(
            inject_message=self._build_next_round_message(snapshot),
            progress=progress,
        )
        if self.pause_requested:
            # Consume the pause at the round boundary: hand control to the
            # engine's intervention menu; the original next-round instruction
            # stays on the decision for the "resume" answer.
            self.pause_requested = False
            decision.paused = True
        return decision

    def _build_next_round_message(self, snapshot: AggregateSnapshot) -> str:
        """Canonical next-round instruction, prefixed with any queued nudges."""
        last_new = snapshot.findings_by_round[-1] if snapshot.findings_by_round else 0
        inject_message = prompts.next_round_instruction(
            snapshot.rounds_seen,
            last_new,
            exhausted_dimensions=snapshot.exhausted_dimensions or None,
        )
        if self._pending_nudges:
            # Drain human-in-the-loop nudges (design §18.1 督促注入): the user's
            # request leads the next-round instruction so the model acts on it
            # before continuing the loop.
            nudge_block = "\n".join(self._pending_nudges)
            self._pending_nudges = []
            inject_message = (
                f"[用户督促]\n{nudge_block}\n\n---\n\n{inject_message}"
            )
        return inject_message

    def _budget_pause_reason(self) -> str | None:
        """Why the remaining budget can't cover another round (or None).

        Uses the most recent round's actual consumption as the burn rate so a
        pause only fires when the headroom is genuinely tight, not on the
        first round with an arbitrarily small budget.
        """
        min_rounds = self.budget_pause_min_rounds
        if min_rounds <= 0:
            return None
        reasons: list[str] = []
        token_budget = self.total_token_budget
        if token_budget is not None:
            used_tokens = self.cost_meter.total_tokens
            remaining_tokens = max(0, token_budget - used_tokens)
            round_tokens = max(self._last_round_tokens, 1)
            if remaining_tokens < min_rounds * round_tokens:
                reasons.append(
                    f"token headroom ({remaining_tokens:,}) below {min_rounds} "
                    f"round(s) at the recent burn rate ({round_tokens:,}/round)"
                )
        usd_budget = self.budget_usd
        if usd_budget is not None:
            used_usd = self.cost_meter.total_cost_usd
            remaining_usd = max(0.0, usd_budget - used_usd)
            round_cost = self._last_round_cost_usd
            if round_cost > 0 and remaining_usd < min_rounds * round_cost:
                reasons.append(
                    f"monetary headroom (${remaining_usd:.4f}) below {min_rounds} "
                    f"round(s) at the recent burn rate (${round_cost:.4f}/round)"
                )
        return "; ".join(reasons) if reasons else None

    def _stop_decision(
        self, snapshot: AggregateSnapshot, reason: str, progress: object
    ) -> LoopDecision:
        # The loop is ending anyway; a pending pause is moot.
        self.pause_requested = False
        notice = prompts.convergence_stop_notice(reason, snapshot.total_findings)
        if not self.stop_on_convergence:
            # Keep looping but steer the model toward the closing report.
            return LoopDecision(inject_message=notice, progress=progress)
        return LoopDecision(stop_reason=reason, inject_message=notice, progress=progress)
