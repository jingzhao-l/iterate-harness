"""Tests for the six iterate tools (BaseTool contract level)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iterate_harness.tools.base import ToolExecutionContext
from iterate_harness.tools.iterate_tools import (
    IterateConfigTool,
    IterateContextTool,
    IterateDecisionLogTool,
    IterateReviewTool,
    IterateTriageTool,
    IterateValidateTool,
)
from iterate_harness.tools.iterate_tools import IterateValidateInput as IterateValidateInputModel

FINDING = {
    "dimension": "security",
    "file": "src/a.py",
    "severity": "critical",
    "summary": "sql injection",
    "failure_scenario": "attacker input reaches query",
    "suggested_fix": "parameterize",
    "is_atomic": True,
    "line": 10,
}


def make_context(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=tmp_path)


def write_config(tmp_path: Path, content: str) -> None:
    (tmp_path / "iterate.config.yaml").write_text(content, encoding="utf-8")


async def run_tool(tool, arguments, context):
    return await tool.execute(arguments, context)


class TestIterateConfigTool:
    async def test_read_defaults_when_no_config(self, tmp_path):
        result = await run_tool(IterateConfigTool(), None, make_context(tmp_path))  # type: ignore[arg-type]
        payload = json.loads(result.output)
        assert result.is_error is False
        assert payload["source"] == "defaults"
        assert payload["validation"]["commands"] == {}
        # Reviewer sub-config exposes both evidence + coverage settings (L5).
        assert payload["reviewer"]["evidenceValidation"] is True
        assert payload["reviewer"]["coverageValidation"] is True
        assert payload["reviewer"]["scopeChunkSize"] == 25

    async def test_read_override_merges(self, tmp_path):
        write_config(tmp_path, 'goal: "g2"\ndimensions:\n  - correctness\n')
        result = await run_tool(IterateConfigTool(), None, make_context(tmp_path))  # type: ignore[arg-type]
        payload = json.loads(result.output)
        assert payload["source"] == "override"
        assert payload["goal"] == "g2"
        assert payload["dimensions"] == ["correctness"]
        assert payload["atomic"]["maxLines"] == 20

    async def test_validate_reports_missing_fields_of_override(self, tmp_path):
        write_config(tmp_path, "language: en\n")  # no goal/dimensions/validation
        result = await run_tool(
            IterateConfigTool(), None, make_context(tmp_path)  # type: ignore[arg-type]
        )
        assert result.is_error is False
        from iterate_harness.tools.iterate_tools import IterateConfigInput

        result = await run_tool(
            IterateConfigTool(), IterateConfigInput(operation="validate"), make_context(tmp_path)
        )
        payload = json.loads(result.output)
        assert payload["valid"] is False
        assert "goal" in payload["missingFields"]

    async def test_is_read_only(self):
        assert IterateConfigTool().is_read_only(None) is True  # type: ignore[arg-type]


class TestIterateValidateTool:
    async def test_rejects_unconfigured_command(self, tmp_path):
        result = await run_tool(
            IterateValidateTool(),
            IterateValidateInputModel(command="rm -rf /"),
            make_context(tmp_path),
        )
        payload = json.loads(result.output)
        assert payload["allowed"] is False
        assert payload["exitCode"] == -1

    async def test_runs_exact_configured_command(self, tmp_path):
        write_config(
            tmp_path,
            "validation:\n  commands:\n    python:\n      - 'echo tool-ok'\n",
        )
        result = await run_tool(
            IterateValidateTool(),
            IterateValidateInputModel(command="echo tool-ok"),
            make_context(tmp_path),
        )
        payload = json.loads(result.output)
        assert payload["allowed"] is True
        assert payload["exitCode"] == 0
        assert payload["stdout"].strip() == "tool-ok"


class TestIterateReviewTool:
    async def test_plan_lists_dimensions_and_schema(self, tmp_path):
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        write_config(tmp_path, "dimensions:\n  - security\n")
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="plan", mode="dry-run", max_review_rounds=2),
            make_context(tmp_path),
        )
        payload = json.loads(result.output)
        plan = payload["plan"]
        assert plan["mode"] == "dry-run"
        assert [d["id"] for d in plan["dimensions"]] == ["security"]
        assert plan["maxReviewRounds"] == 2
        assert plan["dimensions"][0]["findingsSchema"]["type"] == "object"

    async def test_aggregate_dedupes_and_publishes_state(self, tmp_path):
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        context = make_context(tmp_path)
        rounds = [
            {"round": 1, "findings": [FINDING, dict(FINDING)]},  # duplicate
            {"round": 2, "findings": []},  # converged
        ]
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="aggregate", rounds=rounds, max_review_rounds=2),
            context,
        )
        payload = json.loads(result.output)
        report = payload["report"]
        assert report["summary"]["totalFindings"] == 1
        assert report["convergence"]["findingsByRound"] == [1, 0]
        assert report["convergence"]["converged"] is True
        # Loop-policy state published for the engine control block.
        assert context.metadata[ITERATE_STATE_KEY]["rounds_seen"] == 2
        assert context.metadata[ITERATE_STATE_KEY]["total_findings"] == 1

    async def _aggregate_state(self, tmp_path, rounds):
        """Run one aggregate and hand back the loop state the tool published."""
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        context = make_context(tmp_path)
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="aggregate", rounds=rounds, max_review_rounds=2),
            context,
        )
        assert result.is_error is False
        return context, context.metadata[ITERATE_STATE_KEY]

    async def test_aggregate_publishes_evidence_complete_for_real_anchors(self, tmp_path):
        """End-to-end on the publish line: the tool wrote the gate it measured, not a default."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 12)), encoding="utf-8")
        _, state = await self._aggregate_state(tmp_path, [{"round": 1, "findings": [FINDING]}])
        assert state["evidence_complete"] is True

    async def test_aggregate_publishes_false_for_a_fabricated_anchor(self, tmp_path):
        # FINDING anchors src/a.py:10; this project has no such file.
        _, state = await self._aggregate_state(tmp_path, [{"round": 1, "findings": [FINDING]}])
        assert state["evidence_complete"] is False

    async def test_aggregate_omits_evidence_complete_when_the_channel_is_off(self, tmp_path):
        write_config(tmp_path, "reviewer:\n  evidence_validation: false\n")
        _, state = await self._aggregate_state(tmp_path, [{"round": 1, "findings": [FINDING]}])
        assert "evidence_complete" not in state, "an unmeasured gate must be absent, not defaulted"

    async def test_aggregate_publishes_invariant_state_from_a_kernel_in_metadata(self, tmp_path):
        """The end-to-end line `8664b0e` named as missing: a real kernel instance reaches the state."""
        from iterate_harness.defensive.invariants import InvariantReport
        from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY

        class _Kernel:
            last_invariant_report = InvariantReport(passed=True, checks_run=2)

        context = make_context(tmp_path)
        context.metadata[DEFENSIVE_KERNEL_KEY] = _Kernel()
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        await run_tool(
            IterateReviewTool(),
            IterateReviewInput(
                operation="aggregate", rounds=[{"round": 1, "findings": [FINDING]}], max_review_rounds=2
            ),
            context,
        )
        assert context.metadata[ITERATE_STATE_KEY]["invariant_state"] == "pass"

    async def test_aggregate_omits_invariant_state_without_a_kernel(self, tmp_path):
        _, state = await self._aggregate_state(tmp_path, [{"round": 1, "findings": [FINDING]}])
        assert "invariant_state" not in state

    async def test_aggregate_rejects_malformed_findings(self, tmp_path):
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        bad_rounds = [{"round": 1, "findings": [{"dimension": "x"}]}]
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="aggregate", rounds=bad_rounds),
            make_context(tmp_path),
        )
        payload = json.loads(result.output)
        assert result.is_error is True
        assert "missing required fields" in payload["error"]

    async def test_meta_review_audits_report(self, tmp_path):
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        # Evidence gate (default on) requires the finding's file+line to exist.
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 12)), encoding="utf-8")

        report_dict = {
            "mode": "dry-run",
            "goal": "g",
            "dimensions": ["security"],
            "maxReviewRounds": 2,
            "rounds": [
                {"round": 1, "findings": [FINDING]},
                {"round": 2, "findings": []},
            ],
        }
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="meta-review", report=report_dict),
            make_context(tmp_path),
        )
        payload = json.loads(result.output)
        final = payload["finalReport"]
        assert final["metaReview"]["passed"] is True
        assert final["verdict"] == "approved"
        assert final["summary"]["totalFindings"] == 1

    async def test_meta_review_flags_corrupted_report(self, tmp_path):
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        report_dict = {
            "mode": "dry-run",
            "goal": "g",
            "dimensions": ["security"],
            "maxReviewRounds": 2,
            "rounds": [
                {"round": 1, "findings": [dict(FINDING, dimension="nonsense")]},
                {"round": 2, "findings": []},
            ],
        }
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="meta-review", report=report_dict),
            make_context(tmp_path),
        )
        payload = json.loads(result.output)
        issues = payload["finalReport"]["metaReview"]["issues"]
        assert any(i["code"] == "DIMENSION_UNKNOWN" for i in issues)

    async def test_aggregate_audits_dimension_usage_against_budgets(self, tmp_path):
        """v1.1: aggregate with dimension_usage emits budgetAudit + exhausted state."""
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        write_config(
            tmp_path,
            "dimensions:\n  - security\n  - correctness\n"
            "dimension_resources:\n"
            "  security:\n    token_budget: 1000\n"
            "  correctness:\n    token_budget: 5000\n",
        )
        context = make_context(tmp_path)
        rounds = [{"round": 1, "findings": [FINDING]}]
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(
                operation="aggregate",
                rounds=rounds,
                dimension_usage={"security": 1500, "correctness": 100},
            ),
            context,
        )
        payload = json.loads(result.output)
        audit = payload["budgetAudit"]
        assert audit["exceededDimensions"] == ["security"]
        assert audit["allBudgetedExhausted"] is False
        state = context.metadata[ITERATE_STATE_KEY]
        assert state["exhausted_dimensions"] == ["security"]
        assert state["all_dimensions_exhausted"] is False

    async def test_aggregate_without_budgets_omits_audit(self, tmp_path):
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        context = make_context(tmp_path)
        rounds = [{"round": 1, "findings": [FINDING]}]
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="aggregate", rounds=rounds, dimension_usage={"security": 10}),
            context,
        )
        payload = json.loads(result.output)
        assert "budgetAudit" not in payload
        assert "exhausted_dimensions" not in context.metadata[ITERATE_STATE_KEY]
        # v1.2-c: usage is relayed to the engine even without budgets.
        assert context.metadata[ITERATE_STATE_KEY]["dimension_usage"] == {"security": 10}

    async def test_aggregate_sanitizes_dimension_usage(self, tmp_path):
        """v1.2-c: negative usage entries are clamped to zero in published state."""
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        context = make_context(tmp_path)
        rounds = [{"round": 1, "findings": [FINDING]}]
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(
                operation="aggregate",
                rounds=rounds,
                dimension_usage={"security": -50, "correctness": 120},
            ),
            context,
        )
        assert result.is_error is False
        state = context.metadata[ITERATE_STATE_KEY]
        assert state["dimension_usage"] == {"security": 0, "correctness": 120}

    async def test_aggregate_relays_dimension_usage_io(self, tmp_path):
        """v1.26: in/out split is published alongside the bare totals."""
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        context = make_context(tmp_path)
        rounds = [{"round": 1, "findings": [FINDING]}]
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(
                operation="aggregate",
                rounds=rounds,
                dimension_usage={"style": 30},
                dimension_usage_io={"security": {"input": 1000, "output": 500}},
            ),
            context,
        )
        assert result.is_error is False
        state = context.metadata[ITERATE_STATE_KEY]
        assert state["dimension_usage"] == {"security": 1500, "style": 30}
        assert state["dimension_usage_io"] == {"security": {"input": 1000, "output": 500}}

    async def test_aggregate_sanitizes_dimension_usage_io(self, tmp_path):
        """v1.26: negative split counts clamp to zero; all-zero splits drop."""
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        context = make_context(tmp_path)
        rounds = [{"round": 1, "findings": [FINDING]}]
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(
                operation="aggregate",
                rounds=rounds,
                dimension_usage_io={
                    "security": {"input": -10, "output": 5},
                    "style": {"input": 0, "output": 0},
                },
            ),
            context,
        )
        assert result.is_error is False
        state = context.metadata[ITERATE_STATE_KEY]
        assert state["dimension_usage_io"] == {"security": {"input": 0, "output": 5}}
        assert state["dimension_usage"] == {"security": 5}

    async def test_aggregate_counts_io_toward_budget_audit(self, tmp_path):
        """v1.26: split reports feed budget audits even without bare totals."""
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        write_config(
            tmp_path,
            "dimensions:\n  - security\n"
            "dimension_resources:\n"
            "  security:\n    token_budget: 1000\n",
        )
        context = make_context(tmp_path)
        rounds = [{"round": 1, "findings": [FINDING]}]
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(
                operation="aggregate",
                rounds=rounds,
                dimension_usage_io={"security": {"input": 700, "output": 400}},
            ),
            context,
        )
        payload = json.loads(result.output)
        assert payload["budgetAudit"]["exceededDimensions"] == ["security"]
        assert context.metadata[ITERATE_STATE_KEY]["exhausted_dimensions"] == ["security"]

    async def test_meta_review_emits_threshold_gate_when_configured(self, tmp_path):
        """v1.1: meta-review folds project thresholds into the final verdict."""
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        write_config(
            tmp_path,
            "dimensions:\n  - security\nthresholds:\n  max_critical: 0\n",
        )
        report_dict = {
            "mode": "dry-run",
            "goal": "g",
            "dimensions": ["security"],
            "maxReviewRounds": 2,
            "rounds": [
                {"round": 1, "findings": [FINDING]},  # severity: critical
                {"round": 2, "findings": []},
            ],
        }
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="meta-review", report=report_dict),
            make_context(tmp_path),
        )
        payload = json.loads(result.output)
        final = payload["finalReport"]
        gate = final["thresholdGate"]
        assert gate["passed"] is False
        assert gate["violations"][0]["metric"] == "critical"
        assert final["verdict"] == "needs_revision"
        assert any(i["code"] == "THRESHOLD_EXCEEDED" for i in final["metaReview"]["issues"])

    async def test_meta_review_without_thresholds_omits_gate(self, tmp_path):
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        report_dict = {
            "mode": "dry-run",
            "goal": "g",
            "dimensions": ["security"],
            "maxReviewRounds": 2,
            "rounds": [
                {"round": 1, "findings": [FINDING]},
                {"round": 2, "findings": []},
            ],
        }
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="meta-review", report=report_dict),
            make_context(tmp_path),
        )
        payload = json.loads(result.output)
        assert "thresholdGate" not in payload["finalReport"]

    async def test_plan_publishes_the_run_plan_and_cites_its_digest(self, tmp_path):
        """Scenario ② (producing half): the digest in the payload is the disk file's digest.

        That equality is the only one the GlassPane side can re-check without trusting
        this process — so it is the one asserted here.
        """
        from iterate_harness.iterate import run_plan as run_plan_mod
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        write_config(tmp_path, "dimensions:\n  - security\n  - correctness\n")
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="plan", mode="dry-run", max_review_rounds=2),
            make_context(tmp_path),
        )
        assert result.is_error is False
        run_plan_info = json.loads(result.output)["runPlan"]
        assert "error" not in run_plan_info
        assert Path(run_plan_info["path"]) == tmp_path / ".iterate" / "run-plan.json"
        assert run_plan_info["dimensions"] == ["security", "correctness"]
        on_disk = json.loads(Path(run_plan_info["path"]).read_text(encoding="utf-8"))
        assert run_plan_info["digest"] == run_plan_mod.run_plan_digest(on_disk)

    async def test_plan_records_the_published_digest_in_the_ledger(self, tmp_path):
        """The digest must stay checkable after a compaction drops the tool output."""
        from iterate_harness.iterate import decision_log
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        write_config(tmp_path, "dimensions:\n  - security\n")
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="plan", mode="dry-run", max_review_rounds=2),
            make_context(tmp_path),
        )
        run_plan_info = json.loads(result.output)["runPlan"]
        assert "ledgerError" not in run_plan_info
        entries = decision_log.read_entries(tmp_path)
        matched = [
            entry
            for entry in entries
            if isinstance(entry.data, dict) and entry.data.get("kind") == "run-plan"
        ]
        assert matched, "no run-plan decision reached the audit chain"
        assert matched[-1].type == "decision"
        assert matched[-1].data["runPlanDigest"] == run_plan_info["digest"]
        assert matched[-1].data["dimensions"] == ["security"]

    async def test_plan_without_a_config_refuses_the_run_plan_loudly_and_keeps_the_review_plan(
        self, tmp_path
    ):
        """A guard that cannot report is not delivered: None config → named refusal, plan intact."""
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="plan", mode="dry-run", max_review_rounds=2),
            make_context(tmp_path),
        )
        assert result.is_error is False
        payload = json.loads(result.output)
        assert "plan" in payload  # the review plan is still returned
        assert "runPlan" in payload  # and the failure is stated, never omitted
        assert payload["runPlan"]["error"].startswith("RUN_PLAN_E_NO_CONFIG:")
        assert "remedy" in payload["runPlan"]

    async def test_plan_with_a_dimensionless_config_reports_the_run_plan_error_not_an_omission(
        self, tmp_path
    ):
        """(d) RunPlanError on a readable-but-unplannable config: still a review plan + a code."""
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        write_config(tmp_path, "goal: g\n")  # a mapping, but no dimensions to plan from
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="plan", mode="dry-run", max_review_rounds=2),
            make_context(tmp_path),
        )
        assert result.is_error is False
        payload = json.loads(result.output)
        assert payload["plan"]["dimensions"]
        assert payload["runPlan"]["error"].startswith("RUN_PLAN_E_NO_DIMENSIONS:")
        assert payload["runPlan"]["remedy"]

    async def _aggregate_once(self, tmp_path, context=None):
        from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        ctx = context or make_context(tmp_path)
        result = await run_tool(
            IterateReviewTool(),
            IterateReviewInput(
                operation="aggregate",
                rounds=[{"round": 1, "findings": [FINDING]}],
                max_review_rounds=2,
            ),
            ctx,
        )
        assert result.is_error is False
        return ctx.metadata[ITERATE_STATE_KEY]

    async def test_aggregate_coverage_with_a_dimensionless_ledger_verifies_nothing(self, tmp_path):
        """(b) Decisions that name no dimension belong to no dimension: 0/N, never a guess."""
        from iterate_harness.iterate import decision_log
        from iterate_harness.iterate.dimension_context import dimension_context, format_dimension_context

        write_config(tmp_path, "dimensions:\n  - security\n  - correctness\n")
        for kind in ("intervention", "assumption-check"):
            decision_log.append_entry(
                tmp_path,
                decision_log.make_entry(
                    entry_type="decision",
                    round_number=1,
                    data={"kind": kind, "detail": "the run recorded this, but never a dimension"},
                ),
            )
        state = await self._aggregate_once(tmp_path)
        expected = format_dimension_context(
            dimension_context(["security", "correctness"], {})
        )
        assert state["dimension_coverage"] == expected
        assert expected.startswith("0/2 dimensions verified")
        assert "2 unverified (security, correctness)" in expected

    async def test_aggregate_coverage_counts_only_valid_dimensioned_entries(self, tmp_path):
        """One dimensioned ledger decision flips exactly one plan dimension to verified."""
        from iterate_harness.iterate import decision_log
        from iterate_harness.iterate.dimension_context import dimension_context, format_dimension_context

        write_config(tmp_path, "dimensions:\n  - security\n  - correctness\n")
        decision_log.append_entry(
            tmp_path,
            decision_log.make_entry(
                entry_type="decision",
                round_number=1,
                data={"dimension": "security", "kind": "review-note"},
            ),
        )
        decision_log.append_entry(
            tmp_path,
            decision_log.make_entry(
                entry_type="decision", round_number=1, data={"dimension": "Not-A-Kebab-Id"}
            ),
        )
        state = await self._aggregate_once(tmp_path)
        assert state["dimension_coverage"] == format_dimension_context(
            dimension_context(["security", "correctness"], {"security": {"decisions": 1}})
        )

    async def test_aggregate_coverage_reads_the_published_plan_before_the_config(self, tmp_path):
        """The plan file wins over the config: the two CAN differ, and the difference is the point."""
        from iterate_harness.tools.iterate_tools import IterateReviewInput

        write_config(tmp_path, "dimensions:\n  - security\n  - correctness\n")
        await run_tool(
            IterateReviewTool(),
            IterateReviewInput(operation="plan", mode="dry-run", max_review_rounds=2),
            make_context(tmp_path),
        )
        # Config edited after publication: the published contract stays the contract.
        write_config(tmp_path, "dimensions:\n  - ui-ux\n")
        state = await self._aggregate_once(tmp_path)
        assert "ui-ux" not in state["dimension_coverage"]
        assert "unverified (security, correctness)" in state["dimension_coverage"]


class TestIterateDecisionLogTool:
    async def test_append_and_read_roundtrip(self, tmp_path):
        from iterate_harness.tools.iterate_tools import IterateDecisionLogInput

        context = make_context(tmp_path)
        result = await run_tool(
            IterateDecisionLogTool(),
            IterateDecisionLogInput(operation="append", type="round_start", round=1, data={"phase": "plan"}),
            context,
        )
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["entryCount"] == 1

        result = await run_tool(
            IterateDecisionLogTool(), IterateDecisionLogInput(operation="read"), context
        )
        payload = json.loads(result.output)
        assert payload["entryCount"] == 1
        assert payload["entries"][0]["type"] == "round_start"

    async def test_append_refuses_a_kind_the_kernel_cannot_carry(self, tmp_path):
        """The gate is the kernel's shape rule, not this shell's list.

        It used to be a ``frozenset`` of eight names, and the TypeScript plugin's list had ten
        — so the same decision was legal in one shell and rejected in the other. That was the
        divergence, so the test now states both directions: a kind that cannot be *compared*
        is refused, and a kind nobody has ever listed is recorded.
        """
        from iterate_harness.tools.iterate_tools import IterateDecisionLogInput

        context = make_context(tmp_path)
        result = await run_tool(
            IterateDecisionLogTool(),
            IterateDecisionLogInput(operation="append", type="Round Failed!", round=1),
            context,
        )
        payload = json.loads(result.output)
        assert payload["error"], payload
        assert payload["code"] == "KERNEL_E_DECISION_KIND"
        assert "snake_case" in payload["remedy"]

        result = await run_tool(
            IterateDecisionLogTool(),
            IterateDecisionLogInput(operation="append", type="decision"),
            context,
        )
        assert json.loads(result.output)["error"]

        result = await run_tool(
            IterateDecisionLogTool(),
            IterateDecisionLogInput(operation="append", type="resume", round=2, data={"why": "user"}),
            context,
        )
        appended = json.loads(result.output)
        assert appended.get("success") is True, appended
        lines = [
            json.loads(line)
            for line in (tmp_path / ".iterate" / "decision-log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [entry["kind"] for entry in lines] == ["resume"], "a kind outside the old list must reach the ledger"
        assert lines[0]["payload"] == {"why": "user"}


class TestIterateContextTool:
    async def test_reads_skill_iterate_and_personalization(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))  # isolate personalization store
        (tmp_path / "SKILL.md").write_text("# skill docs")
        (tmp_path / "ITERATE.md").write_text("# project knowledge")
        from iterate_harness.iterate.personalization import PersonalizationData, save

        save(None, tmp_path, PersonalizationData(review_focus_areas=["security"]))
        result = await run_tool(IterateContextTool(), None, make_context(tmp_path))  # type: ignore[arg-type]
        payload = json.loads(result.output)
        assert payload["skill"].startswith("# skill docs")
        assert payload["projectKnowledge"].startswith("# project knowledge")
        assert payload["personalization"]["reviewFocusAreas"] == ["security"]

    async def test_empty_project_returns_only_personalization(self, tmp_path):
        result = await run_tool(IterateContextTool(), None, make_context(tmp_path))  # type: ignore[arg-type]
        payload = json.loads(result.output)
        # No SKILL.md / ITERATE.md found; personalization summary always present.
        assert "skill" not in payload
        assert "projectKnowledge" not in payload
        assert payload["personalization"]["reviewFocusAreas"] == []


@pytest.mark.parametrize(
    "tool",
    [IterateConfigTool(), IterateReviewTool(), IterateContextTool()],
)
def test_read_only_tools_declared(tool):
    assert tool.is_read_only(None) is True  # type: ignore[arg-type]


TRIAGE_FINDINGS = [
    {
        "file": "src/auth.py",
        "dimension": "security",
        "severity": "high",
        "summary": "token logged in plaintext",
        "line": 42,
    },
    {
        "file": "src/api.py",
        "dimension": "performance",
        "severity": "medium",
        "summary": "N+1 query in list handler",
        "line": 7,
    },
    {
        "file": "src/auth.py",
        "dimension": "security",
        "severity": "low",
        "summary": "broad except swallows errors",
    },
]


def make_triage_input(**overrides):
    from iterate_harness.tools.iterate_tools import IterateTriageInput

    payload = {"findings": TRIAGE_FINDINGS, "round": 2}
    payload.update(overrides)
    return IterateTriageInput.model_validate(payload)


class TestIterateTriageTool:
    async def test_headless_applies_default_to_all(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))  # isolate personalization store
        result = await run_tool(
            IterateTriageTool(), make_triage_input(default="fix"), make_context(tmp_path)
        )
        payload = json.loads(result.output)
        assert result.is_error is False
        assert payload["triage"]["interactive"] is False
        assert payload["triage"]["counts"] == {"fix": 3, "skip": 0, "ignore": 0}
        assert payload["persistedKnownIntentional"] == 0

    async def test_headless_ignore_persists_known_intentional(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = await run_tool(
            IterateTriageTool(),
            make_triage_input(default="ignore", note="legacy API contract"),
            make_context(tmp_path),
        )
        payload = json.loads(result.output)
        assert payload["persistedKnownIntentional"] == 3
        from iterate_harness.iterate import personalization

        known = personalization.known_intentional_of(None, tmp_path)
        assert len(known) == 3
        assert all(k.reason == "legacy API contract" for k in known)
        assert known[0].file == "src/auth.py" and known[0].dimension == "security"
        # Decision log got one triage decision entry on round 2.
        from iterate_harness.iterate import decision_log

        entries = decision_log.read_entries(tmp_path)
        triage_entries = [e for e in entries if e.type == "decision" and (e.data or {}).get("kind") == "triage"]
        assert len(triage_entries) == 1
        assert triage_entries[0].round == 2

    async def test_interactive_y_n_a_walkthrough(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        answers = iter(["y", "n", "a."])

        async def fake_ask(question: str) -> str:
            assert "Triage finding" in question
            return next(answers)

        context = ToolExecutionContext(cwd=tmp_path, metadata={"ask_user_prompt": fake_ask})
        result = await run_tool(IterateTriageTool(), make_triage_input(), context)
        payload = json.loads(result.output)
        assert payload["triage"]["interactive"] is True
        assert payload["triage"]["counts"] == {"fix": 1, "skip": 1, "ignore": 1}
        assert payload["triage"]["fixIndexes"] == [1]
        assert payload["triage"]["skipIndexes"] == [2]
        assert payload["triage"]["ignoreIndexes"] == [3]
        assert payload["persistedKnownIntentional"] == 1

    async def test_interactive_unrecognized_answer_falls_back_to_default(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(tmp_path))

        async def fake_ask(question: str) -> str:
            return "maybe??"

        context = ToolExecutionContext(cwd=tmp_path, metadata={"ask_user_prompt": fake_ask})
        result = await run_tool(
            IterateTriageTool(), make_triage_input(default="skip"), context
        )
        payload = json.loads(result.output)
        assert payload["triage"]["counts"] == {"fix": 0, "skip": 3, "ignore": 0}

    async def test_ignore_dedupes_against_existing_entries(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        first = await run_tool(
            IterateTriageTool(), make_triage_input(default="ignore"), make_context(tmp_path)
        )
        assert json.loads(first.output)["persistedKnownIntentional"] == 3
        second = await run_tool(
            IterateTriageTool(), make_triage_input(default="ignore"), make_context(tmp_path)
        )
        assert json.loads(second.output)["persistedKnownIntentional"] == 0

    async def test_ignored_finding_is_filtered_from_future_reviews(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        await run_tool(
            IterateTriageTool(),
            make_triage_input(default="ignore", note="intentional"),
            make_context(tmp_path),
        )
        from iterate_harness.iterate import personalization, review
        from iterate_harness.iterate.types import ReviewFinding

        findings = [
            ReviewFinding(
                dimension="security",
                file="src/auth.py",
                severity="high",
                summary="token logged in plaintext",
                failure_scenario="leak",
                suggested_fix="redact",
                is_atomic=True,
                line=42,
            )
        ]
        known = personalization.known_intentional_of(None, tmp_path)
        assert review.filter_known_intentional(findings, known) == []

    async def test_too_many_findings_rejected(self, tmp_path):
        from iterate_harness.tools.iterate_tools import IterateTriageInput

        findings = [
            {"file": "a.py", "dimension": "d", "summary": "s", "line": i}
            for i in range(1, 52)
        ]
        result = await run_tool(
            IterateTriageTool(),
            IterateTriageInput.model_validate({"findings": findings}),
            make_context(tmp_path),
        )
        assert result.is_error is True
        assert "too many findings" in json.loads(result.output)["error"]

    def test_is_not_read_only(self):
        assert IterateTriageTool().is_read_only(None) is False  # type: ignore[arg-type]
