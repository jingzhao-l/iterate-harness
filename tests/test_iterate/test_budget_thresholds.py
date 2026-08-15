"""v1.1 tests: per-dimension token budgets, threshold gates, budget-aware policy."""

from __future__ import annotations

from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.iterate.config_loader import (
    parse_thresholds,
    parse_token_budget,
    thresholds_to_dict,
)
from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY, IterateLoopPolicy
from iterate_harness.iterate.meta_review import build_final_review_report
from iterate_harness.iterate.prompts import next_round_instruction
from iterate_harness.iterate.review import (
    audit_dimension_budgets,
    evaluate_threshold_gates,
)
from iterate_harness.iterate.types import (
    DimensionThresholds,
    ReviewFinding,
    ThresholdsConfig,
)


def f(**partial: object) -> ReviewFinding:
    base: dict[str, object] = {
        "dimension": "correctness",
        "file": "src/a.ts",
        "severity": "medium",
        "summary": "A problem",
        "failure_scenario": "fails when x",
        "suggested_fix": "do y",
        "is_atomic": True,
    }
    base.update(partial)
    return ReviewFinding(**base)  # type: ignore[arg-type]


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


# --- audit_dimension_budgets -------------------------------------------------


class TestAuditDimensionBudgets:
    def test_usage_within_budget_is_not_exceeded(self):
        audit = audit_dimension_budgets({"correctness": 100_000}, {"correctness": 40_000})
        assert audit.exceeded_dimensions == []
        assert audit.all_budgeted_exhausted is False
        row = audit.dimensions[0]
        assert row["remaining"] == 60_000
        assert row["exceeded"] is False

    def test_usage_over_budget_is_exceeded(self):
        audit = audit_dimension_budgets({"correctness": 100}, {"correctness": 101})
        assert audit.exceeded_dimensions == ["correctness"]
        assert audit.dimensions[0]["remaining"] == 0

    def test_all_budgeted_exhausted_only_when_every_budget_exceeded(self):
        budgets = {"correctness": 100, "security": 200}
        partial = audit_dimension_budgets(budgets, {"correctness": 150, "security": 10})
        assert partial.exceeded_dimensions == ["correctness"]
        assert partial.all_budgeted_exhausted is False
        full = audit_dimension_budgets(budgets, {"correctness": 150, "security": 999})
        assert full.all_budgeted_exhausted is True

    def test_no_budgets_configured_never_exhausted(self):
        audit = audit_dimension_budgets({}, {"correctness": 500})
        assert audit.dimensions == []
        assert audit.all_budgeted_exhausted is False

    def test_unreported_dimension_counts_as_zero_usage(self):
        audit = audit_dimension_budgets({"correctness": 100, "security": 100}, {"correctness": 50})
        by_dim = {row["dimension"]: row for row in audit.dimensions}
        assert by_dim["security"]["used"] == 0

    def test_negative_usage_is_clamped_to_zero(self):
        audit = audit_dimension_budgets({"correctness": 100}, {"correctness": -5})
        assert audit.dimensions[0]["used"] == 0

    def test_to_dict_shape(self):
        audit = audit_dimension_budgets({"correctness": 10}, {"correctness": 20})
        payload = audit.to_dict()
        assert payload["exceededDimensions"] == ["correctness"]
        assert payload["allBudgetedExhausted"] is True
        assert payload["dimensions"][0]["dimension"] == "correctness"


# --- evaluate_threshold_gates ------------------------------------------------


class TestEvaluateThresholdGates:
    def test_empty_thresholds_pass_with_no_findings(self):
        result = evaluate_threshold_gates(ThresholdsConfig(), [f(severity="critical")])
        assert result.passed is True
        assert result.violations == []

    def test_global_critical_cap_violated(self):
        thresholds = ThresholdsConfig(max_critical=0)
        result = evaluate_threshold_gates(thresholds, [f(severity="critical")])
        assert result.passed is False
        assert result.violations == [
            {"scope": "global", "metric": "critical", "limit": 0, "actual": 1}
        ]

    def test_exact_limit_is_not_a_violation(self):
        thresholds = ThresholdsConfig(max_critical=2)
        findings = [f(severity="critical"), f(severity="critical")]
        assert evaluate_threshold_gates(thresholds, findings).passed is True

    def test_global_high_cap_violated(self):
        thresholds = ThresholdsConfig(max_high=1)
        findings = [f(severity="high"), f(severity="high"), f(severity="high")]
        result = evaluate_threshold_gates(thresholds, findings)
        assert result.passed is False
        assert result.violations[0]["metric"] == "high"
        assert result.violations[0]["actual"] == 3

    def test_per_dimension_cap_only_counts_that_dimension(self):
        thresholds = ThresholdsConfig(
            dimensions={"security": DimensionThresholds(max_critical=0)}
        )
        findings = [
            f(dimension="correctness", severity="critical"),
            f(dimension="security", severity="critical"),
        ]
        result = evaluate_threshold_gates(thresholds, findings)
        assert result.passed is False
        assert result.violations == [
            {"scope": "dimension:security", "metric": "critical", "limit": 0, "actual": 1}
        ]

    def test_unset_dimension_inherits_no_gate(self):
        thresholds = ThresholdsConfig(
            dimensions={"security": DimensionThresholds(max_critical=0)}
        )
        # correctness has no dimension entry and no global cap → gate passes.
        assert evaluate_threshold_gates(thresholds, [f(dimension="correctness", severity="critical")]).passed is True

    def test_multiple_violations_all_reported(self):
        thresholds = ThresholdsConfig(
            max_critical=0,
            max_high=0,
            dimensions={"security": DimensionThresholds(max_high=1)},
        )
        findings = [
            f(severity="critical"),
            f(severity="high"),
            f(dimension="security", severity="high"),
            f(dimension="security", severity="high"),
        ]
        result = evaluate_threshold_gates(thresholds, findings)
        assert result.passed is False
        scopes = {(v["scope"], v["metric"]) for v in result.violations}
        assert ("global", "critical") in scopes
        assert ("global", "high") in scopes
        assert ("dimension:security", "high") in scopes

    def test_global_medium_cap_violated(self):
        thresholds = ThresholdsConfig(max_medium=1)
        findings = [f(severity="medium"), f(severity="medium")]
        result = evaluate_threshold_gates(thresholds, findings)
        assert result.passed is False
        assert result.violations == [
            {"scope": "global", "metric": "medium", "limit": 1, "actual": 2}
        ]

    def test_global_low_cap_violated(self):
        thresholds = ThresholdsConfig(max_low=0)
        result = evaluate_threshold_gates(thresholds, [f(severity="low")])
        assert result.passed is False
        assert result.violations[0]["metric"] == "low"

    def test_medium_cap_counts_only_medium_findings(self):
        """Each metric counts findings at EXACTLY that severity."""
        thresholds = ThresholdsConfig(max_medium=0, max_high=0)
        findings = [f(severity="high"), f(severity="critical"), f(severity="low")]
        result = evaluate_threshold_gates(thresholds, findings)
        assert result.passed is False
        assert result.violations == [
            {"scope": "global", "metric": "high", "limit": 0, "actual": 1}
        ]

    def test_per_dimension_medium_and_low_caps(self):
        thresholds = ThresholdsConfig(
            dimensions={"style": DimensionThresholds(max_medium=0, max_low=0)}
        )
        findings = [
            f(dimension="style", severity="medium"),
            f(dimension="style", severity="low"),
            f(dimension="correctness", severity="low"),
        ]
        result = evaluate_threshold_gates(thresholds, findings)
        assert result.passed is False
        scopes = {(v["scope"], v["metric"]) for v in result.violations}
        assert ("dimension:style", "medium") in scopes
        assert ("dimension:style", "low") in scopes
        assert ("global", "low") not in scopes


# --- config parsing -----------------------------------------------------------


class TestConfigParsing:
    def test_token_budget_accepts_positive_int(self):
        assert parse_token_budget(50_000) == (50_000, [])

    def test_token_budget_rejects_bad_values(self):
        assert parse_token_budget(None) == (None, [])
        for bad in (0, -1, True, "1000", 100.5):
            value, errors = parse_token_budget(bad)
            assert value is None
            assert errors == ["token_budget must be a positive integer"]

    def test_thresholds_roundtrip(self):
        raw = {
            "max_critical": 1,
            "max_high": 3,
            "max_medium": 5,
            "max_low": 9,
            "dimensions": {"security": {"max_critical": 0, "max_medium": 2, "max_low": 4}},
        }
        thresholds, errors = parse_thresholds(raw)
        assert errors == []
        assert thresholds.max_critical == 1
        assert thresholds.max_high == 3
        assert thresholds.max_medium == 5
        assert thresholds.max_low == 9
        assert thresholds.dimensions["security"].max_critical == 0
        assert thresholds.dimensions["security"].max_medium == 2
        assert thresholds.dimensions["security"].max_low == 4
        assert thresholds_to_dict(thresholds) == raw

    def test_thresholds_invalid_entries_report_errors_and_skip(self):
        thresholds, errors = parse_thresholds(
            {
                "max_critical": -1,
                "max_high": "many",
                "max_medium": True,
                "max_low": 1.5,
                "dimensions": {"security": "none", "perf": {"max_high": -2, "max_low": "few"}},
            }
        )
        assert thresholds.max_critical is None
        assert thresholds.max_high is None
        assert thresholds.max_medium is None
        assert thresholds.max_low is None
        assert thresholds.dimensions["security"].is_empty()
        assert thresholds.dimensions["perf"].is_empty()
        # 4 global + perf.max_high + perf.max_low + security "must be a mapping"
        assert len(errors) == 7

    def test_thresholds_non_mapping_rejected(self):
        _, errors = parse_thresholds(["nope"])
        assert errors == ["thresholds must be a mapping"]

    def test_none_and_defaults(self):
        thresholds, errors = parse_thresholds(None)
        assert errors == []
        assert thresholds.is_empty()


# --- meta-review folding -------------------------------------------------------


class TestMetaReviewThresholdFolding:
    def _report(self, findings: list[ReviewFinding]):
        from iterate_harness.iterate.review import ReviewRound, build_review_report

        return build_review_report(
            mode="dry-run",
            goal="quality",
            dimensions=["correctness"],
            max_review_rounds=2,
            rounds=[ReviewRound(round=1, findings=findings)],
        )

    def test_failed_gate_flips_verdict_and_adds_issue(self):
        gate = evaluate_threshold_gates(
            ThresholdsConfig(max_critical=0), [f(severity="critical")]
        )
        final = build_final_review_report(self._report([f(severity="critical")]), threshold_result=gate)
        assert final.verdict == "needs_revision"
        codes = [issue.code for issue in final.meta_review.issues]
        assert "THRESHOLD_EXCEEDED" in codes
        assert final.threshold_gate is gate

    def test_passed_gate_keeps_verdict(self):
        report = self._report([f(severity="low")])
        final = build_final_review_report(report)
        assert final.verdict == "approved"
        assert final.threshold_gate is None


# --- loop policy integration ---------------------------------------------------


class TestLoopPolicyBudgetEnforcement:
    def test_total_token_budget_stops_the_loop(self):
        policy = IterateLoopPolicy(max_review_rounds=5, total_token_budget=10_000)
        decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: state()},
            UsageSnapshot(input_tokens=6_000, output_tokens=5_000),
            "m",
        )
        assert decision.stop_reason is not None
        assert "token budget exhausted" in decision.stop_reason
        assert "11,000/10,000" in decision.stop_reason

    def test_within_budget_keeps_loop_running(self):
        policy = IterateLoopPolicy(max_review_rounds=5, total_token_budget=10_000)
        decision = policy.on_turn_end(
            {ITERATE_STATE_KEY: state()},
            UsageSnapshot(input_tokens=4_000, output_tokens=5_000),
            "m",
        )
        assert decision.stop_reason is None
        assert decision.inject_message is not None

    def test_budget_stop_even_without_aggregate_snapshot(self):
        """Budget enforcement must not depend on a fresh aggregate."""
        policy = IterateLoopPolicy(max_review_rounds=5, total_token_budget=100)
        decision = policy.on_turn_end({}, UsageSnapshot(input_tokens=200), "m")
        assert decision.stop_reason is not None
        assert "token budget exhausted" in decision.stop_reason

    def test_all_dimension_budgets_exhausted_stops_the_loop(self):
        policy = IterateLoopPolicy(max_review_rounds=5)
        snapshot = state(
            exhausted_dimensions=["correctness", "security"],
            all_dimensions_exhausted=True,
        )
        decision = policy.on_turn_end({ITERATE_STATE_KEY: snapshot}, UsageSnapshot(), "m")
        assert decision.stop_reason is not None
        assert "all dimension token budgets exhausted" in decision.stop_reason
        assert "correctness" in decision.stop_reason

    def test_partial_exhaustion_steers_next_round_prompt(self):
        policy = IterateLoopPolicy(max_review_rounds=5)
        snapshot = state(exhausted_dimensions=["security"], all_dimensions_exhausted=False)
        decision = policy.on_turn_end({ITERATE_STATE_KEY: snapshot}, UsageSnapshot(), "m")
        assert decision.stop_reason is None
        assert decision.inject_message is not None
        assert "EXHAUSTED" in decision.inject_message
        assert "security" in decision.inject_message

    def test_next_round_instruction_lists_exhausted_dimensions(self):
        message = next_round_instruction(2, 1, exhausted_dimensions=["security", "correctness"])
        assert "EXHAUSTED" in message
        assert "correctness, security" in message
        assert "EXHAUSTED" not in next_round_instruction(2, 1)
