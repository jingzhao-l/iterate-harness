"""CLI tests for ``ih iterate agents`` (the team roster command)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.tasks.manager import get_task_manager, reset_task_manager


@pytest.fixture(autouse=True)
def _clean_task_manager():
    """Isolate every test from whichever harness run left a manager behind."""
    reset_task_manager()
    yield
    reset_task_manager()


@pytest.fixture(autouse=True)
def _cwd(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _spawn(task_id: str, *, agent_id: str = "", task_type: str = "in_process_teammate"):
    """Register one tracked worker and return its record."""
    return get_task_manager().register_in_process_task(
        task_id,
        task_type=task_type,
        description=f"worker {task_id}",
        cwd=".",
        agent_id=agent_id,
        team=agent_id.split("@")[-1] if agent_id else "",
    )


def _with_output(task_id: str, body: str):
    record = _spawn(task_id, agent_id=f"{task_id}@team")
    record.output_file.parent.mkdir(parents=True, exist_ok=True)
    record.output_file.write_text(body, encoding="utf-8")
    return record


def _terminal(task_id: str, status: str) -> None:
    record = _spawn(task_id, agent_id=f"{task_id}@team")
    record.status = status  # type: ignore[assignment]
    record.ended_at = 9000.0


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------


def test_agents_exits_zero_and_explains_when_nothing_is_tracked() -> None:
    result = CliRunner().invoke(cli.app, ["iterate", "agents"])
    assert result.exit_code == 0, result.output
    assert "没有正在运行或记录在册的子代理" in result.output


def test_agents_lists_the_team_with_a_counter_line() -> None:
    _spawn("t-1", agent_id="one@team")
    _spawn("t-2", agent_id="two@team", task_type="local_bash")
    result = CliRunner().invoke(cli.app, ["iterate", "agents"])
    assert result.exit_code == 0, result.output
    assert "在跑 2" in result.output
    assert "t-1" in result.output and "t-2" in result.output
    assert "one@team" in result.output


def test_agents_json_is_machine_readable() -> None:
    _spawn("t-json", agent_id="reader@team")
    result = CliRunner().invoke(cli.app, ["iterate", "agents", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total"] == 1
    assert payload["agents"][0]["agent_id"] == "reader@team"
    assert payload["agents"][0]["running"] is True


def test_agents_tail_zero_hides_output() -> None:
    _with_output("t-quiet", "visible output\n")
    result = CliRunner().invoke(cli.app, ["iterate", "agents", "--tail", "0"])
    assert result.exit_code == 0, result.output
    assert "visible output" not in result.output


def test_agents_shows_the_output_tail_of_a_worker() -> None:
    _with_output("t-chatty", "scanning round 3\n")
    result = CliRunner().invoke(cli.app, ["iterate", "agents"])
    assert result.exit_code == 0, result.output
    assert "scanning round 3" in result.output


def test_agents_status_filter() -> None:
    _spawn("t-live", agent_id="live@team")
    _terminal("t-dead", "failed")
    result = CliRunner().invoke(cli.app, ["iterate", "agents", "--status", "failed"])
    assert result.exit_code == 0, result.output
    assert "t-dead" in result.output
    assert "t-live" not in result.output


def test_agents_rejects_an_unknown_status() -> None:
    result = CliRunner().invoke(cli.app, ["iterate", "agents", "--status", "exploding"])
    assert result.exit_code != 0
    assert "unknown status" in result.output


def test_agents_limit_truncates_and_reports_it() -> None:
    for index in range(4):
        _spawn(f"t-{index}", agent_id=f"w{index}@team")
    result = CliRunner().invoke(cli.app, ["iterate", "agents", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert "未显示" in result.output


# ---------------------------------------------------------------------------
# --stop
# ---------------------------------------------------------------------------


def test_stop_without_confirm_is_refused() -> None:
    _spawn("t-guard", agent_id="guarded@team")
    result = CliRunner().invoke(cli.app, ["iterate", "agents", "--stop", "t-guard"])
    assert result.exit_code == 2
    assert "--confirm" in result.output
    # And nothing was killed.
    assert get_task_manager().get_task("t-guard").status == "running"


def test_stop_requires_the_confirm_flag_to_kill() -> None:
    calls: list[int] = []

    async def _abort() -> None:
        calls.append(1)

    _spawn("t-stop", agent_id="stuck@team")
    get_task_manager()._in_process_aborts["t-stop"] = _abort
    result = CliRunner().invoke(
        cli.app, ["iterate", "agents", "--stop", "stuck@team", "--confirm"]
    )
    assert result.exit_code == 0, result.output
    assert "t-stop" in result.output
    assert calls == [1]


def test_stop_unknown_agent_reports_not_found() -> None:
    """An unknown id is a truthful answer, not a failure of the command."""
    result = CliRunner().invoke(
        cli.app, ["iterate", "agents", "--stop", "nobody@nowhere", "--confirm"]
    )
    assert result.exit_code == 0, result.output
    assert "nobody@nowhere" in result.output


def test_stop_stale_record_without_a_handle_is_named_as_such() -> None:
    """A record that says running but has nothing to signal is not a success."""
    _spawn("t-ghost", agent_id="ghost@team")
    result = CliRunner().invoke(
        cli.app, ["iterate", "agents", "--stop", "ghost@team", "--confirm"]
    )
    assert result.exit_code == 1
    assert "旧记录" in result.output


def test_stop_json_reports_the_outcome() -> None:
    async def _abort() -> None:
        pass

    _spawn("t-json", agent_id="json@team")
    get_task_manager()._in_process_aborts["t-json"] = _abort
    result = CliRunner().invoke(
        cli.app, ["iterate", "agents", "--stop", "json@team", "--confirm", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "stopped"
    assert payload["status_now"] == "killed"
    assert payload["id"] == "t-json"
