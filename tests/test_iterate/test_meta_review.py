"""Tests for openharness.iterate.meta_review (port of meta-review.test.ts)."""

from __future__ import annotations

import dataclasses

from openharness.iterate.meta_review import (
    META_REVIEW_CHECKS,
    build_final_review_report,
    meta_review_report,
)
from openharness.iterate.review import build_review_report
from openharness.iterate.types import ReviewFinding, ReviewRound


def f(**partial: object) -> ReviewFinding:
    base: dict[str, object] = {
        "dimension": "correctness",
        "file": "src/a.ts",
        "severity": "medium",
        "summary": "A problem",
        "failure_scenario": "fails when x happens",
        "suggested_fix": "do y instead",
        "is_atomic": True,
    }
    base.update(partial)
    return ReviewFinding(**base)  # type: ignore[arg-type]


def good_report():
    """A well-formed, internally consistent report (dry-run, converged)."""
    return build_review_report(
        mode="dry-run",
        goal="Improve quality",
        dimensions=["correctness", "security"],
        max_review_rounds=3,
        rounds=[
            ReviewRound(round=1, findings=[
                f(dimension="security", severity="critical", summary="sql injection", line=10),
                f(dimension="correctness", severity="low", summary="typo", line=20),
            ]),
            ReviewRound(round=2, findings=[
                f(dimension="security", severity="critical", summary="sql injection", line=10),
            ]),
        ],
    )


def codes(result) -> set[str]:
    return {i.code for i in result.issues}


class TestMetaReviewReport:
    def test_passes_a_well_formed_converged_report_with_zero_issues(self):
        result = meta_review_report(good_report())
        assert result.passed is True
        assert result.verdict == "approved"
        assert result.checks_run == META_REVIEW_CHECKS
        assert result.issues == []

    def test_detects_count_match_failure(self):
        report = good_report()
        report.summary.total_findings = len(report.findings) + 1
        result = meta_review_report(report)
        assert result.passed is False
        assert result.verdict == "revise"
        assert "COUNT_MATCH" in codes(result)

    def test_detects_severity_sum_failure(self):
        report = good_report()
        report.summary.low = report.summary.low + 5  # inflate low out of the real total
        result = meta_review_report(report)
        assert "SEVERITY_SUM" in codes(result)

    def test_detects_dimension_sum_failure(self):
        report = good_report()
        report.summary.by_dimension = {"correctness": len(report.findings) + 3}
        result = meta_review_report(report)
        assert "DIMENSION_SUM" in codes(result)

    def test_detects_dimension_unknown_failure(self):
        report = good_report()
        report.findings[0] = dataclasses.replace(report.findings[0], dimension="nonsense")
        result = meta_review_report(report)
        assert "DIMENSION_UNKNOWN" in codes(result)

    def test_detects_sort_order_failure(self):
        report = good_report()
        report.findings[0], report.findings[1] = report.findings[1], report.findings[0]
        result = meta_review_report(report)
        assert "SORT_ORDER" in codes(result)

    def test_detects_convergence_flag_failure(self):
        report = good_report()
        report.convergence.converged = not report.convergence.converged
        result = meta_review_report(report)
        assert "CONVERGENCE_FLAG" in codes(result)

    def test_detects_convergence_sum_failure(self):
        report = good_report()
        report.convergence.findings_by_round = [2, 1]
        result = meta_review_report(report)
        assert "CONVERGENCE_SUM" in codes(result)

    def test_detects_round_gap_failure(self):
        report = good_report()
        report.rounds = [
            ReviewRound(round=1, findings=[f(summary="r1")]),
            ReviewRound(round=3, findings=[f(summary="r3")]),
        ]
        result = meta_review_report(report)
        assert "ROUND_GAP" in codes(result)

    def test_does_not_flag_round_empty_for_final_converged_round(self):
        report = build_review_report(
            mode="dry-run",
            goal="Improve quality",
            dimensions=["correctness", "security"],
            max_review_rounds=3,
            rounds=[
                ReviewRound(round=1, findings=[
                    f(dimension="security", severity="critical", summary="sql injection", line=10),
                    f(dimension="correctness", severity="low", summary="typo", line=20),
                ]),
                ReviewRound(round=2, findings=[]),  # converged round: nothing new
            ],
        )
        result = meta_review_report(report)
        # No ROUND_EMPTY / ROUND_GAP: an empty final round IS the convergence signal.
        assert result.passed is True
        assert result.verdict == "approved"
        assert "ROUND_EMPTY" not in codes(result)
        assert "ROUND_GAP" not in codes(result)

    def test_still_flags_round_empty_for_non_final_empty_round(self):
        report = good_report()
        report.rounds = [
            ReviewRound(round=1, findings=[f(summary="r1")]),
            ReviewRound(round=2, findings=[]),  # empty middle round
            ReviewRound(round=3, findings=[f(summary="r3")]),
        ]
        result = meta_review_report(report)
        assert "ROUND_EMPTY" in codes(result)

    def test_handles_none_report_without_crashing(self):
        result = meta_review_report(None)
        assert result.passed is False
        assert result.verdict == "revise"
        assert "REPORT_UNDEFINED" in codes(result)


class TestBuildFinalReviewReport:
    def test_produces_approved_final_report_for_consistent_source(self):
        final = build_final_review_report(good_report())
        assert final.verdict == "approved"
        assert final.meta_review.verdict == "approved"
        assert final.summary.verdict == "approved"
        assert final.summary.report_issues == 0
        assert final.summary.total_findings == good_report().summary.total_findings
        assert final.summary.converged is True

    def test_produces_needs_revision_for_inconsistent_source(self):
        report = good_report()
        report.summary.total_findings = len(report.findings) + 1
        final = build_final_review_report(report)
        assert final.verdict == "needs_revision"
        assert final.summary.verdict == "needs_revision"
        assert final.summary.report_issues == len(final.meta_review.issues)
        # The source report itself is preserved unchanged.
        assert final.source is report
