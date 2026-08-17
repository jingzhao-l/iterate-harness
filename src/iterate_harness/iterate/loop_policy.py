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
  observed one. When a NEW aggregate batch arrives it emits a
  :class:`~iterate_harness.engine.stream_events.ReviewProgressEvent`, then
  decides: converged / round-cap reached → STOP; otherwise → inject the
  canonical next-round instruction.
- Usage snapshots are billed into :class:`~iterate_harness.iterate.cost.CostMeter`
  so every progress event carries the running USD cost.

All logic is pure and unit-testable; the engine only wires it in.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..api.usage import UsageSnapshot
from . import prompts
from .cost import CostMeter

ITERATE_STATE_KEY = "iterate_state"

#: Width (seconds) of the rolling window for ``max_turns_per_minute``.
RATE_LIMIT_WINDOW_SECONDS = 60


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


@dataclass
class AggregateSnapshot:
    """Counters extracted from one iterate_review aggregate call."""

    rounds_seen: int = 0
    total_findings: int = 0
    findings_by_round: list[int] = field(default_factory=list)
    converged: bool = False
    by_dimension: dict[str, int] = field(default_factory=dict)
    mode: str = "dry-run"
    # Per-dimension token-budget state published by the aggregate tool
    # (empty when no budgets are configured or no usage was reported).
    exhausted_dimensions: list[str] = field(default_factory=list)
    all_dimensions_exhausted: bool = False
    # Reviewer-reported cumulative token totals per dimension (v1.2-c).
    # Subagent usage bills outside the main API stream, so these are reported
    # figures the policy relays into the CostMeter (monotonic running totals).
    dimension_usage: dict[str, int] = field(default_factory=dict)
    # Optional per-dimension input/output split (v1.26): billed at exact
    # prices instead of the blended average.
    dimension_usage_io: dict[str, dict[str, int]] = field(default_factory=dict)


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
    by_dim = {str(k): int(v) for k, v in by_dim_raw.items()} if isinstance(by_dim_raw, dict) else {}
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
    return AggregateSnapshot(
        rounds_seen=int(state.get("rounds_seen", 0) or 0),
        total_findings=int(state.get("total_findings", 0) or 0),
        findings_by_round=fbr,
        converged=bool(state.get("converged", False)),
        by_dimension=by_dim,
        mode=str(state.get("mode", "dry-run")),
        exhausted_dimensions=exhausted,
        all_dimensions_exhausted=bool(state.get("all_dimensions_exhausted", False)),
        dimension_usage=usage,
        dimension_usage_io=usage_io,
    )


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

    cost_meter: CostMeter = field(default=None)  # type: ignore[assignment]
    _rounds_seen: int = 0
    _emitted_progress: int = 0
    # Rolling window of turn timestamps for rate limiting (seconds since
    # epoch). Pruned to ``RATE_LIMIT_WINDOW_SECONDS`` on each check.
    _turn_timestamps: list[float] = field(default_factory=list)
    # Human-in-the-loop nudges queued by the WebUI (design §18.1 "督促注入"):
    # drained into the next-round instruction at the next round boundary so
    # a user's urge to keep going is honored even mid-round.
    _pending_nudges: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.cost_meter is None:
            self.cost_meter = CostMeter(price_overrides=self.price_overrides)

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
        self.cost_meter.accumulate(usage, model)
        budget_stop = self._budget_stop_reason()
        if budget_stop is not None:
            snapshot = read_state(tool_metadata) or AggregateSnapshot(mode=self.mode)
            self._record_dimension_usage(snapshot)
            return self._stop_decision(
                snapshot, budget_stop, self._build_progress(snapshot, usage, model)
            )
        current = read_state(tool_metadata)
        if current is None or current.rounds_seen <= self._rounds_seen:
            # No NEW aggregate this turn → nothing iterate-specific to do.
            return LoopDecision()
        self._rounds_seen = current.rounds_seen
        self._emitted_progress += 1
        self._record_dimension_usage(current)
        progress = self._build_progress(current, usage, model)
        return self._decide(current, progress)

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

    def _build_progress(self, snapshot: AggregateSnapshot, usage: UsageSnapshot, model: str) -> object:
        # Imported lazily to avoid a circular import with the engine package.
        from ..engine.stream_events import ReviewProgressEvent

        new_findings = snapshot.findings_by_round[-1] if snapshot.findings_by_round else 0
        return ReviewProgressEvent(
            round=snapshot.rounds_seen,
            new_findings=new_findings,
            total_findings=snapshot.total_findings,
            per_dimension=dict(snapshot.by_dimension),
            converged=snapshot.converged,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=self.cost_meter.total_cost_usd,
            mode=snapshot.mode,
            dimension_cost_usd=self.cost_meter.dimension_cost_usd(model),
        )

    def _decide(self, snapshot: AggregateSnapshot, progress: object) -> LoopDecision:
        last_new = snapshot.findings_by_round[-1] if snapshot.findings_by_round else 0
        if snapshot.converged or last_new == 0:
            reason = "converged (0 new findings in the last round)"
            if snapshot.rounds_seen >= self.max_review_rounds:
                reason = f"round cap reached ({self.max_review_rounds}) with 0 new findings"
            return self._stop_decision(snapshot, reason, progress)
        if snapshot.rounds_seen >= self.max_review_rounds:
            return self._stop_decision(
                snapshot, f"round cap reached ({self.max_review_rounds})", progress
            )
        if snapshot.all_dimensions_exhausted and snapshot.exhausted_dimensions:
            return self._stop_decision(
                snapshot,
                "all dimension token budgets exhausted ("
                + ", ".join(snapshot.exhausted_dimensions)
                + ")",
                progress,
            )
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
        decision = LoopDecision(
            inject_message=inject_message,
            progress=progress,
        )
        if self.pause_requested:
            # Consume the pause at the round boundary: hand control to the
            # engine's intervention menu; the original next-round instruction
            # stays on the decision for the "resume" answer.
            self.pause_requested = False
            decision.paused = True
        return decision

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
