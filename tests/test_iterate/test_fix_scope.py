"""Tests for post-fix scope verification and deferred-architectural inheritance.

Covers design §11.2.2:
- ``assess_fix_scope`` measures the uncommitted diff against the atomic cap
  (defensive when git is unavailable).
- ``extract_deferred_architectural`` pulls non-atomic findings from the most
  recent review result for the checkpoint.
- ``save_checkpoint`` persists the deferred list and ``summarize_last_run``
  surfaces it, so ``resume_kickoff`` can re-inject it across sessions.
"""

from __future__ import annotations


from iterate_harness.iterate.checkpoint import load_checkpoint, save_checkpoint
from iterate_harness.iterate.decision_log import append_entry, extract_deferred_architectural, make_entry
from iterate_harness.iterate import prompts
from iterate_harness.iterate.last_state import summarize_last_run
from iterate_harness.iterate.fix_scope import FixScopeAssessment, assess_fix_scope


class TestAssessFixScope:
    def test_unavailable_without_git_repo(self, tmp_path):
        assessment = assess_fix_scope(tmp_path, max_lines=20)
        assert assessment.available is False
        assert assessment.over_limit is False

    def test_over_limit_flags_when_counts_exceed_cap(self):
        assessment = FixScopeAssessment(available=True, added_lines=30, removed_lines=2, max_lines=20)
        assert assessment.over_limit is True
        assert assessment.total_lines == 32

    def test_within_limit_not_over(self):
        assessment = FixScopeAssessment(available=True, added_lines=10, removed_lines=5, max_lines=20)
        assert assessment.over_limit is False

    def test_zero_cap_disables_gate(self):
        assessment = FixScopeAssessment(available=True, added_lines=99, max_lines=0)
        assert assessment.over_limit is False

    def test_hint_mentions_split(self):
        assessment = FixScopeAssessment(available=True, added_lines=30, removed_lines=2, max_lines=20)
        hint = __import__(
            "iterate_harness.iterate.fix_scope", fromlist=["fix_scope_over_limit_hint"]
        ).fix_scope_over_limit_hint(assessment)
        assert "over the atomic-fix cap" in hint
        assert "Split it" in hint


class TestExtractDeferredArchitectural:
    def _log_review(self, project_root, findings):
        append_entry(
            project_root,
            make_entry(
                entry_type="review_result",
                round_number=1,
                data={"verdict": "needs-work", "findings": findings},
            ),
        )

    def test_returns_only_non_atomic_findings(self, tmp_path):
        self._log_review(
            tmp_path,
            [
                {"file": "a.py", "dimension": "security", "summary": "atomic fix", "is_atomic": True},
                {"file": "b.py", "dimension": "architecture", "summary": "broad refactor", "is_atomic": False},
                {"file": "c.py", "dimension": "style", "summary": "no flag"},
            ],
        )
        deferred = extract_deferred_architectural(tmp_path)
        assert [d["file"] for d in deferred] == ["b.py", "c.py"]

    def test_empty_when_all_atomic(self, tmp_path):
        self._log_review(
            tmp_path,
            [{"file": "a.py", "dimension": "x", "summary": "s", "is_atomic": True}],
        )
        assert extract_deferred_architectural(tmp_path) == []

    def test_empty_when_no_log(self, tmp_path):
        assert extract_deferred_architectural(tmp_path) == []


class TestDeferredInheritance:
    def test_checkpoint_round_trips_deferred(self, tmp_path):
        deferred = [{"file": "b.py", "dimension": "architecture", "summary": "broad refactor"}]
        save_checkpoint(
            tmp_path,
            round=2,
            new_findings=0,
            total_findings=3,
            per_dimension={},
            converged=False,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            mode="normal",
            deferred_architectural=deferred,
        )
        loaded = load_checkpoint(tmp_path)
        assert loaded is not None
        assert loaded["deferred_architectural"] == deferred

    def test_summarize_last_run_surfaces_deferred(self, tmp_path):
        save_checkpoint(
            tmp_path,
            round=2,
            new_findings=0,
            total_findings=3,
            per_dimension={"security": 3},
            converged=False,
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.01,
            mode="normal",
            deferred_architectural=[{"file": "b.py", "dimension": "architecture", "summary": "refactor"}],
        )
        summary = summarize_last_run(tmp_path)
        assert summary is not None
        assert summary.get("deferred_architectural") == [
            {"file": "b.py", "dimension": "architecture", "summary": "refactor"}
        ]

    def test_resume_kickoff_embeds_deferred_block(self):
        last_summary = {
            "mode": "normal",
            "interrupted": True,
            "rounds": 2,
            "totalFindings": 3,
            "deferred_architectural": [
                {"file": "b.py", "dimension": "architecture", "summary": "broad refactor"}
            ],
        }
        prompt = prompts.resume_kickoff("goal", 3, last_summary)
        assert "deferred architectural findings" in prompt
        assert "b.py" in prompt
        assert "broad refactor" in prompt
