"""Tests for iterate_harness.iterate.loop_policy and cost metering."""

from __future__ import annotations

from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.engine.stream_events import ReviewProgressEvent
from iterate_harness.iterate.cost import CostMeter, price_for
from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY, IterateLoopPolicy, read_state


def state(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "mode": "dry-run",
        "rounds_seen": 1,
        "total_findings": 3,
        "findings_by_round": [3],
        "converged": False,
        "by_dimension": {"correctness": 2, "security": 1},
    }
    base.update(overrides)
    return base


class TestLoopPolicy:
    def test_no_aggregate_this_turn_is_a_noop(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        decision = policy.on_turn_end({}, UsageSnapshot(), "m")
        assert decision.stop_reason is None
        assert decision.inject_message is None
        assert decision.progress is None

    def test_new_aggregate_injects_next_round_instruction(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
        assert decision.stop_reason is None
        assert decision.inject_message is not None
        assert "Round 1" in decision.inject_message
        assert isinstance(decision.progress, ReviewProgressEvent)
        assert decision.progress.round == 1
        assert decision.progress.new_findings == 3
        assert decision.progress.total_findings == 3
        assert decision.progress.per_dimension == {"correctness": 2, "security": 1}

    def test_converged_aggregate_stops_the_loop(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        snapshot = state(rounds_seen=2, total_findings=3, findings_by_round=[3, 0], converged=True)
        decision = policy.on_turn_end({ITERATE_STATE_KEY: snapshot}, UsageSnapshot(), "m")
        assert decision.stop_reason is not None
        assert "converged" in decision.stop_reason
        assert decision.inject_message is not None  # closing-report notice
        assert isinstance(decision.progress, ReviewProgressEvent)
        assert decision.progress.converged is True

    def test_round_cap_stops_even_when_not_converged(self):
        policy = IterateLoopPolicy(max_review_rounds=2)
        snapshot = state(rounds_seen=2, total_findings=5, findings_by_round=[3, 2])
        decision = policy.on_turn_end({ITERATE_STATE_KEY: snapshot}, UsageSnapshot(), "m")
        assert decision.stop_reason is not None
        assert "round cap" in decision.stop_reason

    def test_no_stop_when_stop_on_convergence_disabled(self):
        policy = IterateLoopPolicy(max_review_rounds=3, stop_on_convergence=False)
        snapshot = state(rounds_seen=2, findings_by_round=[3, 0], converged=True)
        decision = policy.on_turn_end({ITERATE_STATE_KEY: snapshot}, UsageSnapshot(), "m")
        assert decision.stop_reason is None
        assert decision.inject_message is not None

    def test_usage_accumulates_into_cost_meter(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        policy.on_turn_end(
            {ITERATE_STATE_KEY: state()},
            UsageSnapshot(input_tokens=1_000_000, output_tokens=1_000_000),
            "claude-sonnet-4-6",
        )
        # 1M in @ $3 + 1M out @ $15 = $18
        assert policy.cost_meter.total_cost_usd == 18.0

    def test_repeated_same_aggregate_emits_only_once(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        snapshot = {ITERATE_STATE_KEY: state()}
        first = policy.on_turn_end(snapshot, UsageSnapshot(), "m")
        second = policy.on_turn_end(snapshot, UsageSnapshot(), "m")
        assert first.progress is not None
        assert second.progress is None
        assert second.stop_reason is None

    def test_malformed_metadata_is_a_safe_noop(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        for bad in (None, {}, {"iterate_state": "junk"}, {"iterate_state": {"rounds_seen": "x"}}):
            decision = policy.on_turn_end(bad, UsageSnapshot(), "m")  # type: ignore[arg-type]
            assert decision.stop_reason is None
            assert decision.progress is None


class TestDimensionUsageRelay:
    """v1.2-c: reviewer-reported per-dimension token totals reach the CostMeter."""

    def test_new_aggregate_records_dimension_usage(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        snapshot = state(dimension_usage={"security": 1_200, "correctness": 800})
        policy.on_turn_end({ITERATE_STATE_KEY: snapshot}, UsageSnapshot(), "m")
        assert policy.cost_meter.dimension_tokens() == {"security": 1_200, "correctness": 800}
        summary = policy.cost_meter.format_summary()
        assert "dimension security: 1,200 tokens" in summary

    def test_running_totals_are_monotonic_never_summed(self):
        """Round 2 reports RUNNING totals; the meter keeps the max (no double-count)."""
        policy = IterateLoopPolicy(max_review_rounds=3)
        first = state(rounds_seen=1, findings_by_round=[3], dimension_usage={"security": 1_000})
        second = state(rounds_seen=2, findings_by_round=[2], dimension_usage={"security": 1_500})
        policy.on_turn_end({ITERATE_STATE_KEY: first}, UsageSnapshot(), "m")
        policy.on_turn_end({ITERATE_STATE_KEY: second}, UsageSnapshot(), "m")
        assert policy.cost_meter.dimension_tokens() == {"security": 1_500}

    def test_repeated_aggregate_does_not_double_record(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        snapshot = {ITERATE_STATE_KEY: state(dimension_usage={"security": 900})}
        policy.on_turn_end(snapshot, UsageSnapshot(), "m")
        policy.on_turn_end(snapshot, UsageSnapshot(), "m")  # no new aggregate
        assert policy.cost_meter.dimension_tokens() == {"security": 900}

    def test_budget_stop_path_still_records_usage(self):
        policy = IterateLoopPolicy(max_review_rounds=3, total_token_budget=100)
        snapshot = state(dimension_usage={"security": 700})
        decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: snapshot},
            UsageSnapshot(input_tokens=200, output_tokens=100),
            "m",
        )
        assert decision.stop_reason is not None
        assert "token budget exhausted" in decision.stop_reason
        assert policy.cost_meter.dimension_tokens() == {"security": 700}

    def test_dimension_usage_excluded_from_main_loop_token_total(self):
        """Reported subagent usage must not inflate the main-loop meter totals."""
        policy = IterateLoopPolicy(max_review_rounds=3)
        snapshot = state(dimension_usage={"security": 500_000})
        policy.on_turn_end(
            {ITERATE_STATE_KEY: snapshot}, UsageSnapshot(input_tokens=100, output_tokens=50), "m"
        )
        assert policy.cost_meter.total_tokens == 150


class TestPauseMechanics:
    """Esc intervention: pause_requested consumed at the round boundary."""

    def test_pause_requested_marks_decision_paused_with_original_injection(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        policy.request_pause()
        decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
        assert decision.paused is True
        assert decision.stop_reason is None
        # The original next-round instruction is preserved for "resume".
        assert decision.inject_message is not None
        assert "Round 1" in decision.inject_message
        assert isinstance(decision.progress, ReviewProgressEvent)
        # The pause is consumed exactly once.
        assert policy.pause_requested is False

    def test_no_pause_without_request_keeps_normal_decision(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
        assert decision.paused is False

    def test_pause_pending_before_noop_turn_survives(self):
        """A pause set during a long turn must survive a no-aggregate turn."""
        policy = IterateLoopPolicy(max_review_rounds=3)
        policy.request_pause()
        policy.on_turn_end({}, UsageSnapshot(), "m")  # no new aggregate yet
        assert policy.pause_requested is True
        decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
        assert decision.paused is True

    def test_stop_decision_clears_pending_pause(self):
        policy = IterateLoopPolicy(max_review_rounds=2)
        policy.request_pause()
        snapshot = state(rounds_seen=2, findings_by_round=[3, 0], converged=True)
        decision = policy.on_turn_end({ITERATE_STATE_KEY: snapshot}, UsageSnapshot(), "m")
        assert decision.paused is False
        assert decision.stop_reason is not None
        assert policy.pause_requested is False

    def test_clear_pause_drops_stale_request(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        policy.request_pause()
        policy.clear_pause()
        decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
        assert decision.paused is False


class TestCostMeter:
    def test_price_for_matches_patterns_and_overrides(self):
        assert price_for("claude-sonnet-4-6") == (3.0, 15.0)
        assert price_for("deepseek-chat") == (0.27, 1.1)
        assert price_for("unknown-model-xyz") == (3.0, 15.0)
        assert price_for("claude-sonnet-4-6", {"claude-sonnet-4-6": (1.0, 2.0)}) == (1.0, 2.0)

    def test_accumulate_and_summary(self):
        meter = CostMeter()
        meter.accumulate(UsageSnapshot(input_tokens=500_000, output_tokens=100_000), "claude-sonnet-4-6")
        meter.accumulate(UsageSnapshot(input_tokens=1_000_000, output_tokens=0), "deepseek-chat")
        assert meter.total_tokens == 1_600_000
        # 0.5M*$3 + 0.1M*$15 = 3.0 ; 1M*$0.27 = 0.27
        assert meter.total_cost_usd == 3.27
        summary = meter.format_summary()
        assert "claude-sonnet-4-6" in summary
        assert "deepseek-chat" in summary


class TestUsdBudget:
    """v1.35: whole-run monetary budget hard-stop (``budget_usd``)."""

    def test_usd_budget_within_limit_does_not_stop(self):
        policy = IterateLoopPolicy(
            max_review_rounds=3, budget_usd=10.0, price_overrides={"m1": (0.5, 1.0)}
        )
        # 1M in @ $0.5 + 1M out @ $1.0 = $1.5 — under the $10 cap.
        decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: state()},
            UsageSnapshot(input_tokens=1_000_000, output_tokens=1_000_000),
            "m1",
        )
        assert decision.stop_reason is None

    def test_usd_budget_exceeded_stops(self):
        policy = IterateLoopPolicy(
            max_review_rounds=3, budget_usd=10.0, price_overrides={"m1": (10.0, 20.0)}
        )
        # 1M in @ $10 + 1M out @ $20 = $30 > $10 → hard stop with closing notice.
        decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: state()},
            UsageSnapshot(input_tokens=1_000_000, output_tokens=1_000_000),
            "m1",
        )
        assert decision.stop_reason is not None
        assert "monetary budget exhausted" in decision.stop_reason
        assert decision.inject_message is not None

    def test_usd_budget_accumulates_across_turns(self):
        policy = IterateLoopPolicy(
            max_review_rounds=3, budget_usd=1.5, price_overrides={"m1": (2.0, 0.0)}
        )
        first = state(rounds_seen=1, findings_by_round=[3])
        second = state(rounds_seen=2, findings_by_round=[2])
        first_decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: first},
            UsageSnapshot(input_tokens=500_000, output_tokens=0),
            "m1",
        )
        assert first_decision.stop_reason is None
        # Second turn pushes total to $2.0 > $1.5 → stop.
        second_decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: second},
            UsageSnapshot(input_tokens=500_000, output_tokens=0),
            "m1",
        )
        assert second_decision.stop_reason is not None
        assert "monetary budget exhausted" in second_decision.stop_reason

    def test_usd_budget_exact_boundary_does_not_stop(self):
        policy = IterateLoopPolicy(
            max_review_rounds=3, budget_usd=1.0, price_overrides={"m1": (2.0, 0.0)}
        )
        # 0.5M * $2 = $1.0 — exactly at the cap (not over) → no stop.
        decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: state()},
            UsageSnapshot(input_tokens=500_000, output_tokens=0),
            "m1",
        )
        assert decision.stop_reason is None

    def test_usd_budget_none_disables_cap(self):
        policy = IterateLoopPolicy(
            max_review_rounds=3, budget_usd=None, price_overrides={"m1": (100.0, 100.0)}
        )
        decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: state()},
            UsageSnapshot(input_tokens=1_000_000, output_tokens=1_000_000),
            "m1",
        )
        assert decision.stop_reason is None

    def test_token_and_usd_budget_combined_reason(self):
        policy = IterateLoopPolicy(
            max_review_rounds=3,
            total_token_budget=100,
            budget_usd=0.01,
            price_overrides={"m1": (10.0, 0.0)},
        )
        decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: state()},
            UsageSnapshot(input_tokens=1_000_000, output_tokens=1_000_000),
            "m1",
        )
        assert decision.stop_reason is not None
        assert "token budget exhausted" in decision.stop_reason
        assert "monetary budget exhausted" in decision.stop_reason


class TestRateLimiting:
    """v1.35: ``max_turns_per_minute`` throttling via ``before_request``."""

    def test_no_cap_returns_zero_and_records_nothing(self):
        policy = IterateLoopPolicy(max_review_rounds=3)
        assert policy.before_request(now=1000.0) == 0.0
        assert policy._turn_timestamps == []

    def test_requests_within_cap_return_zero(self):
        policy = IterateLoopPolicy(max_review_rounds=3, max_turns_per_minute=3)
        assert policy.before_request(now=1000.0) == 0.0
        assert policy.before_request(now=1001.0) == 0.0
        assert policy.before_request(now=1002.0) == 0.0
        assert len(policy._turn_timestamps) == 3

    def test_requests_over_cap_return_delay_and_defer_timestamp(self):
        policy = IterateLoopPolicy(max_review_rounds=3, max_turns_per_minute=2)
        policy.before_request(now=1000.0)
        policy.before_request(now=1001.0)
        delay = policy.before_request(now=1002.0)
        # Oldest in-window turn (t=1000) falls out at t=1060 → wait 58s.
        assert delay > 0
        assert abs(delay - 58.0) < 1e-9
        # The throttled request is recorded at its deferred issue time.
        assert policy._turn_timestamps[-1] == 1002.0 + delay

    def test_old_turns_fall_out_of_window(self):
        policy = IterateLoopPolicy(max_review_rounds=3, max_turns_per_minute=1)
        policy.before_request(now=0.0)
        # 61s later the only recorded turn is out of the window → no delay.
        assert policy.before_request(now=61.0) == 0.0

    def test_rate_limit_engaged_flag(self):
        policy = IterateLoopPolicy(max_review_rounds=3, max_turns_per_minute=1)
        assert policy.rate_limit_engaged(now=0.0) is False
        policy.before_request(now=0.0)
        assert policy.rate_limit_engaged(now=1.0) is True
        assert policy.rate_limit_engaged(now=61.0) is False


class TestDimensionCostUsd:
    """v1.3-c: reviewer-reported dimension tokens → estimated USD."""

    def test_dimension_cost_uses_blended_price(self):
        meter = CostMeter()
        meter.record_dimension_total("security", 1_000_000)
        meter.record_dimension_total("style", 500_000)
        # claude-sonnet-4 blend = (3 + 15) / 2 = $9 per 1M
        assert meter.dimension_cost_usd("claude-sonnet-4-6") == {"security": 9.0, "style": 4.5}

    def test_dimension_cost_respects_price_overrides(self):
        meter = CostMeter(price_overrides={"m1": (1.0, 3.0)})
        meter.record_dimension_total("security", 2_000_000)
        # blend = (1 + 3) / 2 = $2 per 1M → 2M = $4
        assert meter.dimension_cost_usd("m1") == {"security": 4.0}

    def test_dimension_cost_empty_without_reports(self):
        assert CostMeter().dimension_cost_usd("claude-sonnet-4-6") == {}

    def test_dimension_cost_not_folded_into_total(self):
        meter = CostMeter()
        meter.record_dimension_total("security", 1_000_000)
        assert meter.total_cost_usd == 0.0
        assert meter.total_tokens == 0

    def test_format_summary_appends_usd_when_model_given(self):
        meter = CostMeter()
        meter.record_dimension_total("security", 1_000_000)
        with_usd = meter.format_summary(dimension_model="claude-sonnet-4-6")
        assert "dimension security: 1,000,000 tokens" in with_usd
        assert "(~$9.0000)" in with_usd
        without_usd = meter.format_summary()
        assert "tokens (reviewer-reported)" in without_usd
        assert "$" not in without_usd.split("dimension security")[1]

    def test_split_report_bills_exact_prices(self):
        """v1.26: in/out split beats the blended average."""
        meter = CostMeter()
        meter.record_dimension_usage("security", input_tokens=1_000_000, output_tokens=1_000_000)
        # claude-sonnet-4 exact: 1M*$3 + 1M*$15 = $18 (blend would be $16)
        assert meter.dimension_cost_usd("claude-sonnet-4-6") == {"security": 18.0}

    def test_split_report_is_monotonic(self):
        meter = CostMeter()
        meter.record_dimension_usage("security", input_tokens=800_000, output_tokens=200_000)
        meter.record_dimension_usage("security", input_tokens=500_000, output_tokens=900_000)
        costs = meter.dimension_cost_usd("claude-sonnet-4-6")
        # per-stream max: in=800k, out=900k → 0.8*3 + 0.9*15 = 2.4 + 13.5 = 15.9
        assert costs == {"security": 15.9}
        # bare total keeps the max per-report SUM (1.0M then 1.4M).
        assert meter.dimension_tokens() == {"security": 1_400_000}

    def test_split_wins_over_bare_total_for_same_dimension(self):
        meter = CostMeter()
        meter.record_dimension_total("security", 2_000_000)
        meter.record_dimension_usage("security", input_tokens=400_000, output_tokens=100_000)
        costs = meter.dimension_cost_usd("claude-sonnet-4-6")
        # exact split: 0.4*3 + 0.1*15 = 1.2 + 1.5 = 2.7 (not blended 2M*$9)
        assert costs == {"security": 2.7}

    def test_mixed_split_and_total_dimensions(self):
        meter = CostMeter()
        meter.record_dimension_usage("security", input_tokens=1_000_000, output_tokens=0)
        meter.record_dimension_total("style", 1_000_000)
        costs = meter.dimension_cost_usd("claude-sonnet-4-6")
        assert costs == {"security": 3.0, "style": 9.0}

    def test_read_state_parses_dimension_usage_io(self):
        state = {
            "rounds_seen": 1,
            "total_findings": 2,
            "findings_by_round": [2],
            "converged": False,
            "by_dimension": {"security": 2},
            "dimension_usage": {"security": 900_000, "style": 10},
            "dimension_usage_io": {
                "security": {"input": 800_000, "output": 100_000},
                "style": {"input": "bogus", "output": None},
            },
        }
        snapshot = read_state({ITERATE_STATE_KEY: state})
        assert snapshot is not None
        assert snapshot.dimension_usage_io == {"security": {"input": 800_000, "output": 100_000}}

    def test_read_state_drops_malformed_io_entries(self):
        state = {
            "rounds_seen": 1,
            "dimension_usage_io": {"security": "not-a-dict", "style": {"input": 0, "output": 0}},
        }
        snapshot = read_state({ITERATE_STATE_KEY: state})
        assert snapshot is not None
        assert snapshot.dimension_usage_io == {}

    def test_progress_event_carries_dimension_cost_usd(self):
        """v1.26: ReviewProgressEvent exposes the per-dimension USD map."""
        from iterate_harness.engine.stream_events import ReviewProgressEvent

        policy = IterateLoopPolicy(price_overrides={"m1": (2.0, 4.0)})
        policy.cost_meter.record_dimension_usage("security", input_tokens=500_000, output_tokens=250_000)
        snapshot = read_state(
            {
                ITERATE_STATE_KEY: {
                    "rounds_seen": 1,
                    "total_findings": 1,
                    "findings_by_round": [1],
                    "converged": False,
                    "by_dimension": {"security": 1},
                    "dimension_usage": {"security": 750_000},
                }
            }
        )
        assert snapshot is not None
        event = policy._build_progress(snapshot, UsageSnapshot(), "m1")
        assert isinstance(event, ReviewProgressEvent)
        # exact split: 0.5M*$2 + 0.25M*$4 = 1.0 + 1.0 = 2.0
        assert event.dimension_cost_usd == {"security": 2.0}
