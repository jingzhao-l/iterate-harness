"""Regression tests for review.py defects:

- ``build_review_report`` addressed ``findings_by_round`` by list position
  (``len(rounds) - 1``) although the aggregate is sized/indexed by the ACTUAL
  round number — duplicate round numbers raised ``IndexError`` (not caught by
  the ``ValueError`` tool contract) and non-contiguous resumed round numbers
  misjudged convergence.
- ``report_from_dict`` did a bare ``int()`` on the ``round`` field, so
  ``null`` / ``dict`` values raised ``TypeError`` instead of the documented
  ``ValueError`` tool-error contract.
"""

from __future__ import annotations

import pytest

from iterate_harness.iterate.review import build_review_report, report_from_dict
from iterate_harness.iterate.types import ReviewFinding, ReviewRound


def f(**partial: object) -> ReviewFinding:
    base: dict[str, object] = {
        "dimension": "security",
        "file": "src/a.ts",
        "severity": "medium",
        "summary": "A problem",
        "failure_scenario": "fails when x happens",
        "suggested_fix": "do y instead",
        "is_atomic": True,
    }
    base.update(partial)
    return ReviewFinding(**base)  # type: ignore[arg-type]


def _finding_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "dimension": "security",
        "file": "src/a.ts",
        "severity": "medium",
        "summary": "A problem",
        "failure_scenario": "fails when x happens",
        "suggested_fix": "do y instead",
        "is_atomic": True,
    }
    base.update(overrides)
    return base


class TestBuildReviewReportRoundIndexing:
    """Defect 1: len(rounds)-1 indexing of findings_by_round."""

    def test_duplicate_round_numbers_do_not_raise_index_error(self):
        # findings_by_round is sized by max round (2) while len(rounds) == 3:
        # the old code indexed [2] and crashed with IndexError (which the
        # ValueError tool contract does not catch).
        report = build_review_report(
            mode="dry-run",
            goal="g",
            dimensions=["security"],
            max_review_rounds=3,
            rounds=[
                ReviewRound(round=1, findings=[f(summary="a")]),
                ReviewRound(round=1, findings=[f(summary="A")]),  # dup → 0 new
                ReviewRound(round=2, findings=[f(summary="b")]),  # new finding
            ],
        )
        assert report.convergence.total_rounds == 3
        assert report.convergence.findings_by_round == [1, 1]
        assert report.convergence.converged is False
        assert report.convergence.stopped_reason == "max_rounds_reached"

    def test_all_duplicate_round_numbers_do_not_raise_index_error(self):
        # Every round reuses round number 2: aggregate length is 2, but the
        # old code indexed len(rounds) - 1 == 2 → IndexError.
        report = build_review_report(
            mode="dry-run",
            goal="g",
            dimensions=["security"],
            max_review_rounds=5,
            rounds=[
                ReviewRound(round=2, findings=[f(summary="a")]),
                ReviewRound(round=2, findings=[f(summary="A")]),  # dup → 0 new
                ReviewRound(round=2, findings=[f(summary="a")]),  # dup → 0 new
            ],
        )
        assert report.convergence.findings_by_round == [0, 1]
        assert report.convergence.converged is False

    def test_non_contiguous_resumed_rounds_use_last_round_count(self):
        # Resumed runs skip round numbers ([1, 3]). The old code read
        # findings_by_round[1] — round 2's count (0) — and wrongly reported
        # "converged" even though round 3 produced a new finding.
        report = build_review_report(
            mode="dry-run",
            goal="g",
            dimensions=["security"],
            max_review_rounds=5,
            rounds=[
                ReviewRound(round=1, findings=[f(summary="a")]),
                ReviewRound(round=3, findings=[f(summary="b")]),
            ],
        )
        assert report.convergence.findings_by_round == [1, 0, 1]
        assert report.convergence.converged is False
        assert report.convergence.stopped_reason == "max_rounds_reached"

    def test_non_contiguous_converged_round_reports_converged(self):
        # Round 3 (the last present round) produced 0 new findings → converged,
        # even though the round numbers are non-contiguous.
        report = build_review_report(
            mode="dry-run",
            goal="g",
            dimensions=["security"],
            max_review_rounds=5,
            rounds=[
                ReviewRound(round=1, findings=[f(summary="a")]),
                ReviewRound(round=3, findings=[f(summary="A")]),  # dup → 0 new
            ],
        )
        assert report.convergence.findings_by_round == [1, 0, 0]
        assert report.convergence.converged is True
        assert report.convergence.stopped_reason == "converged"


class TestReportFromDictRoundParsing:
    """Defect 2: bare int() on the round field bypassed the ValueError contract."""

    def test_null_round_raises_value_error_not_type_error(self):
        data = {
            "mode": "dry-run",
            "dimensions": ["security"],
            "rounds": [
                {"round": None, "findings": []},
                {"round": 2, "findings": []},
            ],
        }
        with pytest.raises(ValueError) as excinfo:
            report_from_dict(data)
        assert "rounds[0].round" in str(excinfo.value)

    def test_dict_round_raises_value_error_not_type_error(self):
        data = {
            "mode": "dry-run",
            "rounds": [{"round": {"nested": 1}, "findings": []}],
        }
        with pytest.raises(ValueError) as excinfo:
            report_from_dict(data)
        assert "rounds[0].round" in str(excinfo.value)

    def test_invalid_round_does_not_abort_remaining_parsing(self):
        # A bad round must not halt the whole parse (TypeError); parsing
        # continues so every problem is collected into one ValueError.
        data = {
            "mode": "dry-run",
            "rounds": [
                {"round": None, "findings": []},
                {"round": {"x": 1}, "findings": [_finding_dict(severity="low")]},
            ],
        }
        with pytest.raises(ValueError) as excinfo:
            report_from_dict(data)
        message = str(excinfo.value)
        assert "rounds[0].round" in message
        assert "rounds[1].round" in message

    def test_string_round_is_coerced_to_int(self):
        data = {
            "mode": "dry-run",
            "dimensions": ["security"],
            "rounds": [
                {"round": "3", "findings": []},
                {"round": 5, "findings": []},
            ],
        }
        report = report_from_dict(data)
        assert [r.round for r in report.rounds] == [3, 5]

    def test_missing_round_defaults_to_index_plus_one(self):
        data = {
            "mode": "dry-run",
            "dimensions": ["security"],
            "rounds": [{"findings": []}, {"findings": []}],
        }
        report = report_from_dict(data)
        assert [r.round for r in report.rounds] == [1, 2]

    def test_unparseable_string_round_falls_back_and_is_reported(self):
        data = {
            "mode": "dry-run",
            "rounds": [{"round": "twelve", "findings": []}],
        }
        with pytest.raises(ValueError) as excinfo:
            report_from_dict(data)
        assert "rounds[0].round" in str(excinfo.value)
