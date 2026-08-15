"""Tests for the /iterate slash command handler."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.commands.iterate import iterate_command_handler
from openharness.commands.registry import CommandContext
from openharness.iterate.decision_log import append_entry, make_entry


def make_context(cwd: Path) -> CommandContext:
    return CommandContext(engine=None, cwd=str(cwd))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_status_reports_config_and_log(tmp_path):
    (tmp_path / "iterate.config.yaml").write_text('goal: "g"\n')
    append_entry(tmp_path, make_entry(entry_type="round_start", round_number=1))
    result = await iterate_command_handler("status", make_context(tmp_path))
    assert "config source: override" in result.message
    assert "goal: g" in result.message
    assert "1 entries" in result.message


@pytest.mark.asyncio
async def test_review_submits_dry_run_kickoff(tmp_path):
    result = await iterate_command_handler("review", make_context(tmp_path))
    assert result.submit_prompt is not None
    assert "dry-run review" in result.submit_prompt
    assert "Do NOT modify any file" in result.submit_prompt


@pytest.mark.asyncio
async def test_run_submits_normal_kickoff(tmp_path):
    result = await iterate_command_handler("run", make_context(tmp_path))
    assert result.submit_prompt is not None
    assert "autonomous loop" in result.submit_prompt


@pytest.mark.asyncio
async def test_log_tails_entries(tmp_path):
    append_entry(tmp_path, make_entry(entry_type="decision", round_number=2, data={"why": "x"}))
    result = await iterate_command_handler("log", make_context(tmp_path))
    assert "decision" in (result.message or "")
    assert "why" in (result.message or "")


@pytest.mark.asyncio
async def test_log_empty_is_friendly(tmp_path):
    result = await iterate_command_handler("log 5", make_context(tmp_path))
    assert "empty" in (result.message or "")


@pytest.mark.asyncio
async def test_validate_lists_configured_commands(tmp_path):
    (tmp_path / "iterate.config.yaml").write_text(
        "validation:\n  commands:\n    python:\n      - 'echo hello'\n"
    )
    result = await iterate_command_handler("validate", make_context(tmp_path))
    assert "echo hello" in (result.message or "")


@pytest.mark.asyncio
async def test_validate_runs_allowed_command(tmp_path):
    (tmp_path / "iterate.config.yaml").write_text(
        "validation:\n  commands:\n    python:\n      - 'echo slash-ok'\n"
    )
    result = await iterate_command_handler("validate echo slash-ok", make_context(tmp_path))
    assert "exit=0" in (result.message or "")
    assert "slash-ok" in (result.message or "")


@pytest.mark.asyncio
async def test_validate_rejects_unknown_command(tmp_path):
    (tmp_path / "iterate.config.yaml").write_text(
        "validation:\n  commands:\n    python:\n      - 'echo slash-ok'\n"
    )
    result = await iterate_command_handler("validate rm -rf /", make_context(tmp_path))
    assert "Rejected" in (result.message or "")


@pytest.mark.asyncio
async def test_unknown_subcommand_shows_usage(tmp_path):
    result = await iterate_command_handler("bogus", make_context(tmp_path))
    assert "Usage:" in (result.message or "")
