"""Tests for the record_assumption tool (defensive kernel, design §20.3.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iterate_harness.tools.base import ToolExecutionContext
from iterate_harness.tools.iterate_tools import (
    IterateAssumptionInput,
    IterateAssumptionTool,
)


def _run(
    tool: IterateAssumptionTool,
    tmp_path: Path,
    *,
    statement: str,
    status: str = "declared",
    detail: str = "",
):
    return tool.execute(
        IterateAssumptionInput(statement=statement, status=status, detail=detail),
        ToolExecutionContext(cwd=tmp_path),
    )


@pytest.mark.asyncio
async def test_record_declared_assumption(tmp_path: Path) -> None:
    tool = IterateAssumptionTool()
    result = await _run(tool, tmp_path, statement="src/api.py exists", detail="from glob")

    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["status"] == "declared"
    assert payload["success"] is True
    assert payload["logPath"].endswith("decision-log.jsonl")

    log_file = tmp_path / ".iterate" / "decision-log.jsonl"
    assert log_file.is_file()
    assert "assumption_declared" in log_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_record_holds_and_falsified(tmp_path: Path) -> None:
    tool = IterateAssumptionTool()

    holds = await _run(tool, tmp_path, statement="mypy installed", status="holds")
    falsified = await _run(tool, tmp_path, statement="git clean", status="falsified", detail="uncommitted")

    assert json.loads(holds.output)["status"] == "holds"
    assert json.loads(falsified.output)["status"] == "falsified"
    content = log_content(tmp_path)
    assert '"status": "holds"' in content
    assert '"status": "falsified"' in content


@pytest.mark.asyncio
async def test_whitespace_statement_rejected(tmp_path: Path) -> None:
    tool = IterateAssumptionTool()
    result = await _run(tool, tmp_path, statement="   ")

    assert result.is_error
    assert "must not be empty" in result.output


@pytest.mark.asyncio
async def test_overlong_statement_rejected(tmp_path: Path) -> None:
    tool = IterateAssumptionTool()
    with pytest.raises(Exception):
        await _run(tool, tmp_path, statement="x" * 501)


def log_content(tmp_path: Path) -> str:
    return (tmp_path / ".iterate" / "decision-log.jsonl").read_text(encoding="utf-8")
