"""Tests for the five iterate tools (BaseTool contract level)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.tools.base import ToolExecutionContext
from openharness.tools.iterate_tools import (
    IterateConfigTool,
    IterateContextTool,
    IterateDecisionLogTool,
    IterateReviewTool,
    IterateValidateTool,
)
from openharness.tools.iterate_tools import IterateValidateInput as IterateValidateInputModel

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
        from openharness.tools.iterate_tools import IterateConfigInput

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
        from openharness.tools.iterate_tools import IterateReviewInput

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
        from openharness.iterate.loop_policy import ITERATE_STATE_KEY
        from openharness.tools.iterate_tools import IterateReviewInput

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
        from openharness.tools.iterate_tools import IterateReviewInput

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
        from openharness.tools.iterate_tools import IterateReviewInput

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
        from openharness.tools.iterate_tools import IterateReviewInput

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


class TestIterateDecisionLogTool:
    async def test_append_and_read_roundtrip(self, tmp_path):
        from openharness.tools.iterate_tools import IterateDecisionLogInput

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
        from openharness.tools.iterate_tools import IterateDecisionLogInput

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
        from openharness.iterate.personalization import PersonalizationData, save

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
