"""Lifecycle-hook inventory / CLI tests (design §17.3 P15).

The engine loads lifecycle hooks from settings and enabled plugins; these tests
pin the read surface the operator now gets (``ih hooks list`` + console page):
the inventory is read-only, lists every registered hook grouped by event with
its static shape, reports an unknown event name as a problem with a copyable
remedy (the loader silently drops it otherwise), and the ``--strict`` CLI flag
exits 1 on any problem so a broken hook config can gate CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iterate_harness.cli import app as cli_app
from iterate_harness.config.settings import Settings
from iterate_harness.hooks.inventory import (
    build_hook_inventory,
    hook_rows_for_capabilities,
    render_hook_inventory,
)
from iterate_harness.hooks.schemas import CommandHookDefinition, PromptHookDefinition


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("ITERATE_DATA_DIR", str(data))
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path / "config"))
    return data


def _settings_with_hooks() -> Settings:
    settings = Settings()
    settings.hooks = {
        "session_start": [CommandHookDefinition(command="printf 'booted'")],
        "pre_tool_use": [
            PromptHookDefinition(prompt="Check the tool call", matcher="bash"),
            CommandHookDefinition(command="echo gated", matcher="*edit*", block_on_failure=True),
        ],
    }
    return settings


def test_inventory_lists_hooks_grouped_by_event(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    inventory = build_hook_inventory(project, settings=_settings_with_hooks())
    assert inventory["summary"]["total"] == 3
    assert inventory["summary"]["events"] == 2
    assert inventory["has_problems"] is False
    events = {entry["event"]: entry for entry in inventory["events"]}
    assert set(events) == {"session_start", "pre_tool_use"}
    assert events["session_start"]["count"] == 1
    assert events["pre_tool_use"]["count"] == 2
    first = events["pre_tool_use"]["hooks"][0]
    assert first["type"] == "prompt"
    assert first["matcher"] == "bash"
    assert first["source"] == "settings"
    second = events["pre_tool_use"]["hooks"][1]
    assert second["block_on_failure"] is True
    assert second["command"] == "echo gated"


def test_inventory_reports_unknown_event_as_problem(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    settings = Settings()
    settings.hooks = {
        "pre-tool-use": [CommandHookDefinition(command="echo nope")],
    }
    inventory = build_hook_inventory(project, settings=settings)
    assert inventory["has_problems"] is True
    assert inventory["summary"]["total"] == 0
    problem = inventory["problems"][0]
    assert problem["kind"] == "event"
    assert problem["name"] == "pre-tool-use"
    assert "pre_tool_use" in problem["remedy"]


def test_inventory_is_read_only_does_not_create_journal(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    build_hook_inventory(project, settings=_settings_with_hooks())
    # Looking must not create the journal directory.
    assert not (project / ".iterate" / "hook-journal.jsonl").exists()


def test_hook_rows_feed_capabilities(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    rows = hook_rows_for_capabilities(project, settings=_settings_with_hooks())
    assert len(rows) == 3
    assert {row["event"] for row in rows} == {"session_start", "pre_tool_use"}
    assert all("type" in row and "source" in row for row in rows)


def test_render_inventory_human_readable(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    inventory = build_hook_inventory(project, settings=_settings_with_hooks())
    text = render_hook_inventory(inventory)
    assert "pre_tool_use" in text
    assert "session_start" in text
    assert "printf 'booted'" in text
    assert "matcher=bash" in text
    assert "Problems: none" in text


def test_cli_list_and_json(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _write_settings(project, _settings_with_hooks())
    runner = CliRunner()

    result = runner.invoke(cli_app, ["hooks", "list", "--project-root", str(project)])
    assert result.exit_code == 0, result.output
    assert "pre_tool_use" in result.output
    assert "printf 'booted'" in result.output

    result_json = runner.invoke(
        cli_app, ["hooks", "list", "--project-root", str(project), "--json"]
    )
    assert result_json.exit_code == 0, result_json.output
    payload = json.loads(result_json.output)
    assert payload["summary"]["total"] == 3


def test_inventory_flags_a_dead_command_with_a_remedy(tmp_path: Path) -> None:
    """A hook whose executable does not exist is a visible problem, not a mystery."""
    project = tmp_path / "repo"
    project.mkdir()
    settings = Settings()
    settings.hooks = {
        "pre_tool_use": [
            CommandHookDefinition(command="iterate-definitely-missing-binary --check")
        ]
    }
    inventory = build_hook_inventory(project, settings=settings)
    assert inventory["has_problems"] is True
    row = inventory["events"][0]["hooks"][0]
    assert row["status"] == "warn"
    assert "not found on PATH" in row["issues"][0]
    problem = inventory["problems"][0]
    assert problem["kind"] == "hook"
    assert "iterate-definitely-missing-binary" in problem["detail"]
    assert "settings.hooks.pre_tool_use" in problem["remedy"]
    rendered = render_hook_inventory(inventory)
    assert "not found on PATH" in rendered


def test_inventory_accepts_shell_builtins_and_installed_programs(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    settings = Settings()
    settings.hooks = {
        "session_start": [
            CommandHookDefinition(command="cd . && echo ok"),
            CommandHookDefinition(command="/bin/sh -c 'echo ok'"),
        ]
    }
    inventory = build_hook_inventory(project, settings=settings)
    assert inventory["has_problems"] is False
    rows = inventory["events"][0]["hooks"]
    assert all(row["status"] == "ok" and row["issues"] == [] for row in rows)


def test_inventory_flags_a_bad_http_url_and_empty_prompt(tmp_path: Path) -> None:
    from iterate_harness.hooks.schemas import HttpHookDefinition

    project = tmp_path / "repo"
    project.mkdir()
    settings = Settings()
    settings.hooks = {
        "stop": [
            HttpHookDefinition(url="ftp://example.com/hook"),
            PromptHookDefinition(prompt="   "),
        ]
    }
    inventory = build_hook_inventory(project, settings=settings)
    assert inventory["has_problems"] is True
    rows = inventory["events"][0]["hooks"]
    assert rows[0]["status"] == "warn"
    assert "invalid http(s) url" in rows[0]["issues"][0]
    assert rows[1]["status"] == "warn"
    assert "empty prompt" in rows[1]["issues"][0]


def test_cli_strict_exits_1_on_problem(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    settings = Settings()
    settings.hooks = {"bad-event": [CommandHookDefinition(command="echo x")]}
    _write_settings(project, settings)
    runner = CliRunner()
    result = runner.invoke(cli_app, ["hooks", "list", "--project-root", str(project), "--strict"])
    assert result.exit_code == 1


def _write_settings(project: Path, settings: Settings) -> Path:
    """Persist settings so ``load_settings`` resolves them for the CLI path."""
    from iterate_harness.config.paths import get_config_file_path

    path = get_config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(settings.model_dump_json(indent=2))
    return path
