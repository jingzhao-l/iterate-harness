"""Tests for the /iterate slash command handler."""

from __future__ import annotations

from pathlib import Path

import pytest

from iterate_harness.commands.iterate import iterate_command_handler
from iterate_harness.commands.registry import CommandContext
from iterate_harness.iterate.decision_log import append_entry, make_entry


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


# -- /iterate dimensions -----------------------------------------------------


@pytest.mark.asyncio
async def test_dimensions_lists_configured_with_resources(tmp_path):
    (tmp_path / "iterate.config.yaml").write_text(
        "goal: g\n"
        "dimensions: [security, style-tests]\n"
        "dimension_resources:\n"
        "  security:\n"
        "    model: claude-opus-4\n"
        "    concurrency: 2\n"
        "    token_budget: 80000\n"
        "  style-tests:\n"
        "    model: claude-haiku\n",
        encoding="utf-8",
    )
    result = await iterate_command_handler("dimensions", make_context(tmp_path))
    message = result.message or ""
    assert "Configured dimensions:" in message
    assert "- security (model=claude-opus-4, concurrency=2, token_budget=80000)" in message
    assert "- style-tests (model=claude-haiku)" in message


@pytest.mark.asyncio
async def test_dimensions_default_listing_without_resources(tmp_path):
    result = await iterate_command_handler("dimensions", make_context(tmp_path))
    message = result.message or ""
    assert "Configured dimensions:" in message
    # Default dimensions include the nine base categories, listed bare.
    assert "- correctness" in message
    assert "- security" in message
    assert "- ui-ux" in message


@pytest.mark.asyncio
async def test_dimensions_survives_partial_resources(tmp_path):
    # A resource key for a dimension not in the enabled list must not break
    # the listing of the enabled dimensions.
    (tmp_path / "iterate.config.yaml").write_text(
        "goal: g\n"
        "dimensions: [security]\n"
        "dimension_resources:\n"
        "  unrelated:\n"
        "    model: claude-haiku\n",
        encoding="utf-8",
    )
    result = await iterate_command_handler("dimensions", make_context(tmp_path))
    message = result.message or ""
    assert "Configured dimensions:" in message
    assert "- security" in message
    assert "unrelated" not in message.splitlines()[0:3]
