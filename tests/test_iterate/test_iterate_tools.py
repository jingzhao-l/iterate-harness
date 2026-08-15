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

    async def test_append_rejects_bad_type_and_missing_round(self, tmp_path):
        from iterate_harness.tools.iterate_tools import IterateDecisionLogInput

        context = make_context(tmp_path)
        result = await run_tool(
            IterateDecisionLogTool(),
            IterateDecisionLogInput(operation="append", type="nonsense", round=1),
            context,
        )
        assert json.loads(result.output)["error"]

        result = await run_tool(
            IterateDecisionLogTool(),
            IterateDecisionLogInput(operation="append", type="decision"),
            context,
        )
        assert json.loads(result.output)["error"]


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
