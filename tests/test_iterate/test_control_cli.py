"""CLI tests for ``ih iterate control`` (the second-terminal intervention path)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.iterate import control
from iterate_harness.iterate.control import begin_run, read_pending


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def invoke(*args: str):
    return CliRunner().invoke(cli.app, ["iterate", "control", *args])


def test_status_on_an_idle_project_is_successful(tmp_path: Path) -> None:
    result = invoke("status")
    assert result.exit_code == 0, result.output
    assert "idle (no active run)" in result.output
    assert "ih iterate run" in result.output


def test_status_require_run_fails_when_idle(tmp_path: Path) -> None:
    result = invoke("status", "--require-run")
    assert result.exit_code == 1


def test_nudge_without_an_active_run_fails_with_a_remedy(tmp_path: Path) -> None:
    result = invoke("nudge", "focus", "on", "auth")
    assert result.exit_code == 1
    assert "No iterate run is active" in result.output
    assert "Hint:" in result.output
    assert read_pending(tmp_path) == []


def test_nudge_queues_a_command_and_status_shows_it(tmp_path: Path) -> None:
    begin_run(tmp_path)
    result = invoke("nudge", "focus", "on", "auth")
    assert result.exit_code == 0, result.output
    assert "Queued nudge" in result.output
    pending = read_pending(tmp_path)
    assert len(pending) == 1
    assert pending[0].kind == "nudge"
    assert pending[0].content == "focus on auth"

    status = invoke("status", "--json")
    assert status.exit_code == 0
    payload = json.loads(status.output)
    assert payload["active"] is True
    assert payload["pendingCount"] == 1
    assert payload["pending"][0]["kind"] == "nudge"


def test_pause_and_stop_carry_the_operator_reason(tmp_path: Path) -> None:
    begin_run(tmp_path)
    pause = invoke("pause", "--reason", "reviewing the plan")
    assert pause.exit_code == 0, pause.output
    assert "ih iterate resume --run" in pause.output
    stop = invoke("stop", "--reason", "done for today")
    assert stop.exit_code == 0, stop.output
    assert "checkpoint is kept" in stop.output
    pending = read_pending(tmp_path)
    assert [c.kind for c in pending] == ["pause", "stop"]
    assert pending[0].content == "reviewing the plan"
    assert pending[1].content == "done for today"


def test_clear_drops_queued_commands(tmp_path: Path) -> None:
    begin_run(tmp_path)
    invoke("nudge", "one")
    invoke("stop")
    cleared = invoke("clear")
    assert cleared.exit_code == 0, cleared.output
    assert "Cleared 2 pending control command(s)." in cleared.output
    assert read_pending(tmp_path) == []
    again = invoke("clear")
    assert again.exit_code == 0
    assert "No pending control commands." in again.output


def test_control_group_is_listed_in_help(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.app, ["iterate", "--help"])
    assert result.exit_code == 0
    assert "control" in result.output
    help_text = invoke("--help")
    assert help_text.exit_code == 0
    for command in ("status", "nudge", "pause", "stop", "clear"):
        assert command in help_text.output


def test_status_explains_a_webui_run_is_not_controllable(tmp_path: Path) -> None:
    from iterate_harness.iterate.run_lock import ROLE_WEBUI, claim_run_lease

    with claim_run_lease(tmp_path, role=ROLE_WEBUI):
        result = invoke("status")
    assert result.exit_code == 0, result.output
    assert "not controllable" in result.output
    assert "its own UI" in result.output


def test_status_never_mutates_a_stale_state(tmp_path: Path) -> None:
    begin_run(tmp_path)
    state_path = tmp_path / ".iterate" / control.STATE_FILE
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["pid"] = 999_999_999
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    before = state_path.read_text(encoding="utf-8")
    result = invoke("status")
    assert result.exit_code == 0
    assert "stale" in result.output
    assert state_path.read_text(encoding="utf-8") == before
