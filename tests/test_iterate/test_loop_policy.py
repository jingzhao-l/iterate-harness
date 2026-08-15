"""Tests for openharness.iterate.loop_policy and cost metering."""

from __future__ import annotations

from openharness.api.usage import UsageSnapshot
from openharness.engine.stream_events import ReviewProgressEvent
from openharness.iterate.cost import CostMeter, price_for
from openharness.iterate.loop_policy import ITERATE_STATE_KEY, IterateLoopPolicy


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
