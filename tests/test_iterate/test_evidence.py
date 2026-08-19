"""Tests for iterate_harness.iterate.evidence (code-evidence attestation)."""

from __future__ import annotations

from pathlib import Path

from iterate_harness.iterate.evidence import (
    EvidenceAudit,
    FindingEvidence,
    count_lines,
    read_set_from_metadata,
    resolve_within,
    verify_finding,
    verify_findings,
    verify_line_bounds,
)
from iterate_harness.iterate.meta_review import (
    META_REVIEW_CHECKS,
    build_final_review_report,
)
from iterate_harness.iterate.review import build_review_report
from iterate_harness.iterate.review_scope import CoverageResult
from iterate_harness.iterate.types import ReviewFinding, ReviewRound


def f(**partial: object) -> ReviewFinding:
    base: dict[str, object] = {
        "dimension": "correctness",
        "file": "src/a.py",
        "severity": "medium",
        "summary": "A problem",
        "failure_scenario": "fails when x happens",
        "suggested_fix": "do y instead",
        "is_atomic": True,
    }
    base.update(partial)
    return ReviewFinding(**base)  # type: ignore[arg-type]


class TestCountLines:
    def test_empty_text_has_zero_lines(self):
        assert count_lines("") == 0

    def test_trailing_newline_does_not_create_extra_line(self):
        assert count_lines("a\nb\n") == 2

    def test_no_trailing_newline(self):
        assert count_lines("a\nb") == 2

    def test_single_line(self):
        assert count_lines("just one line") == 1


class TestVerifyLineBounds:
    def test_anchored_line_in_range(self):
        assert verify_line_bounds(2, "a\nb\nc") == (True, 3)

    def test_anchored_line_out_of_range(self):
        assert verify_line_bounds(4, "a\nb\nc") == (False, 3)

    def test_line_zero_is_whole_file_and_valid(self):
        assert verify_line_bounds(0, "x") == (True, 1)

    def test_none_is_whole_file_and_valid(self):
        assert verify_line_bounds(None, "x") == (True, 1)

    def test_negative_line_is_invalid(self):
        assert verify_line_bounds(-1, "x") == (False, 1)


class TestResolveWithin:
    def test_joins_relative_path(self, tmp_path: Path):
        assert resolve_within(tmp_path, "src/a.py") == (tmp_path / "src/a.py").resolve()

    def test_rejects_traversal_escape(self, tmp_path: Path):
        assert resolve_within(tmp_path, "../escape.py") is None

    def test_rejects_absolute_escape(self, tmp_path: Path):
        assert resolve_within(tmp_path, "/etc/passwd") is None

    def test_accepts_dot_segments_inside_root(self, tmp_path: Path):
        assert resolve_within(tmp_path, "./src/./a.py") == (tmp_path / "src/a.py").resolve()


class TestVerifyFinding:
    def test_file_not_found(self, tmp_path: Path):
        result = verify_finding(tmp_path, rel_file="missing.py", line=1)
        assert result.verified is False
        assert result.error == "file_not_found"

    def test_line_out_of_range(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        result = verify_finding(tmp_path, rel_file="a.py", line=5)
        assert result.verified is False
        assert result.error == "line_out_of_range"
        assert result.line_total == 1

    def test_whole_file_finding_needs_existing_file(self, tmp_path: Path):
        result = verify_finding(tmp_path, rel_file="ghost.py", line=0)
        assert result.verified is False
        assert result.error == "file_not_found"

    def test_whole_file_finding_verified_when_file_exists(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        result = verify_finding(tmp_path, rel_file="a.py", line=0)
        assert result.verified is True
        assert result.error is None

    def test_anchored_finding_verified(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
        result = verify_finding(tmp_path, rel_file="a.py", line=2)
        assert result.verified is True
        assert result.error is None
        assert result.line_total == 2

    def test_read_verified_hint_when_in_read_set(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        result = verify_finding(tmp_path, rel_file="a.py", line=1, read_set=set())
        assert result.read_verified is False
        result2 = verify_finding(
            tmp_path,
            rel_file="a.py",
            line=1,
            read_set={str((tmp_path / "a.py").resolve())},
        )
        assert result2.read_verified is True
        # A non-matching read set only fills the hint; it never fails existence.
        assert result2.verified is True

    def test_read_hint_skipped_without_read_set(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        result = verify_finding(tmp_path, rel_file="a.py", line=1)
        assert result.read_verified is None


class TestVerifyFindings:
    def test_audit_passed_when_all_grounded(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        audit = verify_findings(
            tmp_path,
            findings=[
                f(file="a.py", line=1),
                f(file="a.py", line=0),
            ],
        )
        assert audit.passed is True
        assert audit.checked == 2
        assert audit.errors == []

    def test_audit_failed_on_poisoned_evidence(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        audit = verify_findings(
            tmp_path,
            findings=[
                f(file="a.py", line=1),
                f(file="ghost.py", line=99),
            ],
        )
        assert audit.passed is False
        assert len(audit.errors) == 1
        assert audit.errors[0].error == "file_not_found"

    def test_to_dict_shape(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
        audit = verify_findings(tmp_path, findings=[f(file="a.py", line=1)])
        d = audit.to_dict()
        assert d["checked"] == 1
        assert d["passed"] is True
        assert d["violations"] == []
        assert d["readVerifiedRatio"] is None


class TestReadSetFromMetadata:
    def test_none_when_absent(self):
        assert read_set_from_metadata(None) is None
        assert read_set_from_metadata({"other": 1}) is None
        assert read_set_from_metadata({"read_file_state": []}) is None

    def test_extracts_paths_and_normalizes_case(self):
        meta = {"read_file_state": [{"path": "/Tmp/A.py"}, {"path": "/Tmp/B.py"}]}
        result = read_set_from_metadata(meta)
        assert result is not None
        assert "/TMP/A.py" in result or "/Tmp/A.py" in result
        assert len(result) == 2


class TestMetaReviewEvidenceGate:
    @staticmethod
    def _report():
        return build_review_report(
            mode="dry-run",
            goal="improve",
            dimensions=["correctness"],
            max_review_rounds=1,
            rounds=[ReviewRound(round=1, findings=[f(line=1), f(line=1)])],
        )

    def test_clean_evidence_does_not_break_verdict(self):
        final = build_final_review_report(
            self._report(),
            evidence=EvidenceAudit(
                checked=2,
                results=[
                    FindingEvidence(file="a.py", line=1, line_total=10, resolved_path="/x", verified=True),
                    FindingEvidence(file="a.py", line=1, line_total=10, resolved_path="/x", verified=True),
                ],
            ),
        )
        assert final.meta_review.passed is True
        assert final.verdict == "approved"
        assert "EVIDENCE_VIOLATION" not in {i.code for i in final.meta_review.issues}

    def test_poisoned_evidence_forces_hard_revise(self):
        final = build_final_review_report(
            self._report(),
            evidence=EvidenceAudit(
                checked=2,
                results=[
                    FindingEvidence(file="a.py", line=1, line_total=10, resolved_path="/x", verified=True),
                    FindingEvidence(
                        file="ghost.py",
                        line=99,
                        line_total=None,
                        resolved_path=None,
                        verified=False,
                        error="file_not_found",
                    ),
                ],
            ),
        )
        assert final.meta_review.passed is False
        assert final.verdict == "needs_revision"
        codes = {i.code for i in final.meta_review.issues}
        assert "EVIDENCE_VIOLATION" in codes
        ev = next(i for i in final.meta_review.issues if i.code == "EVIDENCE_VIOLATION")
        assert ev.severity == "critical"

    def test_evidence_increments_checks_run(self):
        final = build_final_review_report(
            self._report(),
            evidence=EvidenceAudit(
                checked=1,
                results=[
                    FindingEvidence(file="a.py", line=1, line_total=10, resolved_path="/x", verified=True)
                ],
            ),
        )
        assert final.meta_review.checks_run == META_REVIEW_CHECKS + 1


class TestMetaReviewCoverageHint:
    @staticmethod
    def _report():
        return build_review_report(
            mode="dry-run",
            goal="improve",
            dimensions=["correctness"],
            max_review_rounds=1,
            rounds=[ReviewRound(round=1, findings=[f(line=1)])],
        )

    def test_gap_emits_medium_hint_without_flipping_verdict(self):
        gap = CoverageResult(
            assigned=["src/a.py", "src/b.py", "src/c.py"],
            read=["src/a.py"],
            covered=["src/a.py"],
            uncovered=["src/b.py", "src/c.py"],
            ratio=1 / 3,
        )
        final = build_final_review_report(self._report(), coverage=gap)
        codes = {i.code for i in final.meta_review.issues}
        assert "COVERAGE_GAP" in codes
        hint = next(i for i in final.meta_review.issues if i.code == "COVERAGE_GAP")
        assert hint.severity == "medium"
        assert "2 of 3" in hint.summary
        # Prompt-informative only: the verdict and passed flag stay clean.
        assert final.verdict == "approved"
        assert final.meta_review.passed is True

    def test_full_coverage_emits_no_gap(self):
        covered = CoverageResult(
            assigned=["src/a.py"],
            read=["src/a.py"],
            covered=["src/a.py"],
            uncovered=[],
            ratio=1.0,
        )
        final = build_final_review_report(self._report(), coverage=covered)
        assert "COVERAGE_GAP" not in {i.code for i in final.meta_review.issues}
        assert final.verdict == "approved"

    def test_coverage_increments_checks_run(self):
        covered = CoverageResult(
            assigned=["src/a.py"],
            read=["src/a.py"],
            covered=["src/a.py"],
            uncovered=[],
            ratio=1.0,
        )
        final = build_final_review_report(self._report(), coverage=covered)
        assert final.meta_review.checks_run == META_REVIEW_CHECKS + 1