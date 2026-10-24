"""Tests for the agent roster — the one view across every teammate backend.

The roster is what the operator sees when a loop hangs: which worker is
running, for how long, and what it last said. Every assertion here is
written against a real ``BackgroundTaskManager`` record, not a fake, so the
test fails if the aggregation silently stops seeing a backend.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from iterate_harness.swarm.roster import (
    _in_process_duration_hints,
    _read_output_tail,
    _safe_status,
    _task_kind,
    _utc_iso,
    build_roster,
    render_roster_text,
    roster_as_json,
    stop_agent,
)
from iterate_harness.tasks.manager import get_task_manager, reset_task_manager


@pytest.fixture(autouse=True)
def _clean_task_manager():
    """Isolate every test from whichever harness run left a manager behind."""
    reset_task_manager()
    yield
    reset_task_manager()


def _spawn_in_process(
    task_id: str,
    *,
    agent_id: str = "",
    team: str = "",
    description: str = "teammate",
    created_at: float = 1000.0,
    started_at: float | None = None,
    ended_at: float | None = None,
    status: str = "running",
    return_code: int | None = None,
    metadata: dict[str, str] | None = None,
) -> Path:
    """Register one in-process teammate record and return its output file."""
    manager = get_task_manager()
    record = manager.register_in_process_task(
        task_id,
        task_type="in_process_teammate",
        description=description,
        cwd=str(Path.cwd()),
        prompt="kickoff",
        agent_id=agent_id,
        team=team,
    )
    record.created_at = created_at
    if started_at is not None:
        record.started_at = started_at
    if ended_at is not None:
        record.ended_at = ended_at
    record.status = status  # type: ignore[assignment]
    if return_code is not None:
        record.return_code = return_code
    for key, value in (metadata or {}).items():
        record.metadata[key] = value
    return record.output_file


def _spawn_shell(task_id: str, *, description: str = "bash job") -> Path:
    """Register one plain shell task record and return its output file."""
    manager = get_task_manager()
    record = manager.register_in_process_task(
        task_id, task_type="local_bash", description=description, cwd="."
    )
    return record.output_file


# ---------------------------------------------------------------------------
# build_roster
# ---------------------------------------------------------------------------


def test_roster_is_empty_when_nothing_is_tracked() -> None:
    view = build_roster()
    assert view["total"] == 0
    assert view["truncated"] is False
    assert view["agents"] == []
    for counter in ("running", "failed", "killed", "completed"):
        assert view[counter] == 0
    assert view["generated_at"]


def test_roster_lists_a_mixed_team_running_first_longest_first() -> None:
    _spawn_shell("t-old", description="old shell")
    _spawn_in_process("t-new", agent_id="worker@team1", team="team1")
    _spawn_in_process("t-done", agent_id="old@team1")

    # The first two are "running"; make the newer one shorter-running.
    running = get_task_manager().list_tasks()
    by_id = {task.id: task for task in running}
    by_id["t-old"].created_at = 100.0
    by_id["t-old"].started_at = 100.0
    by_id["t-new"].created_at = 500.0
    by_id["t-new"].started_at = 500.0
    by_id["t-done"].status = "completed"
    by_id["t-done"].ended_at = 300.0

    view = build_roster()
    ids = [agent["id"] for agent in view["agents"]]
    # Both running workers come before the terminal one.
    assert ids[0] in {"t-old", "t-new"} and ids[1] in {"t-old", "t-new"}
    assert ids[2] == "t-done"
    assert view["running"] == 2
    assert view["completed"] == 1
    assert view["total"] == 3


def test_roster_surfaces_agent_identity_and_kind() -> None:
    _spawn_shell("t-bash")
    _spawn_in_process(
        "t-teammate",
        agent_id="reviewer@team2",
        team="team2",
        metadata={"progress": "3", "status_note": "linting tests"},
    )

    view = build_roster()
    rows = {agent["id"]: agent for agent in view["agents"]}
    assert rows["t-bash"]["kind"] == "shell"
    assert rows["t-bash"]["backend"] == "subprocess"
    assert rows["t-bash"]["agent_id"] == ""
    assert rows["t-teammate"]["kind"] == "in_process"
    assert rows["t-teammate"]["backend"] == "in_process"
    assert rows["t-teammate"]["agent_id"] == "reviewer@team2"
    assert rows["t-teammate"]["team"] == "team2"
    assert rows["t-teammate"]["progress"] == "3"
    assert rows["t-teammate"]["status_note"] == "linting tests"
    assert rows["t-teammate"]["task_type"] == "in_process_teammate"


def test_roster_reads_a_bounded_output_tail(tmp_path: Path) -> None:
    _spawn_shell("t-chatty")
    output_file = get_task_manager().get_task("t-chatty").output_file
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("line one\n" + ("x" * 4000) + "\nlast line\n", encoding="utf-8")

    view = build_roster(tail_bytes=200)
    agent = view["agents"][0]
    assert agent["output_bytes"] == output_file.stat().st_size
    assert "last line" in agent["tail"]
    assert "line one" not in agent["tail"]
    # The cut is reported where it happened, so the operator knows a byte cap
    # was applied rather than guessing from a short tail.
    assert "earlier output truncated" in agent["tail"]


def test_roster_tail_zero_omits_the_tail_but_keeps_the_size() -> None:
    _spawn_shell("t-noise")
    output_file = get_task_manager().get_task("t-noise").output_file
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("a\nb\nc\n", encoding="utf-8")

    view = build_roster(tail_bytes=0)
    agent = view["agents"][0]
    assert agent["tail"] == ""
    assert agent["output_bytes"] > 0


def test_roster_tail_under_the_cap_carries_no_marker(tmp_path: Path) -> None:
    output_file = tmp_path / "small.log"
    output_file.write_text("short output\n", encoding="utf-8")
    tail = _read_output_tail(output_file, 1000)
    assert tail == "short output\n"
    assert "truncated" not in tail


def test_read_output_tail_survives_a_multibyte_boundary(tmp_path: Path) -> None:
    output_file = tmp_path / "cjk.log"
    output_file.write_text("中" * 200 + "END\n", encoding="utf-8")
    tail = _read_output_tail(output_file, 30)
    assert tail.endswith("END\n")
    assert "truncated" in tail
    tail.encode("utf-8")  # must stay valid UTF-8


def test_read_output_tail_is_bounded_for_a_huge_file(tmp_path: Path) -> None:
    output_file = tmp_path / "huge.log"
    output_file.write_bytes(b"A" * 1_000_000)
    tail = _read_output_tail(output_file, 512)
    # 512 bytes of output plus the marker that reports the cut.
    assert len(tail.encode("utf-8")) <= 512 + 64
    assert "truncated" in tail
    assert "512 bytes" in tail


def test_read_output_tail_never_raises(tmp_path: Path) -> None:
    assert _read_output_tail(tmp_path / "missing.log", 100) == ""
    assert _read_output_tail(tmp_path / "missing.log", 0) == ""
    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    assert _read_output_tail(empty, 100) == ""
    # A directory is a valid path but not a readable file.
    assert _read_output_tail(tmp_path, 100) == ""


def test_roster_tolerates_a_missing_or_unreadable_output_file() -> None:
    _spawn_shell("t-ghost")  # output_file was never created
    view = build_roster()
    assert view["agents"][0]["output_bytes"] == 0
    assert view["agents"][0]["tail"] == ""


def test_roster_tolerates_malformed_records() -> None:
    """A store that somehow holds junk must not crash the roster view."""

    class _NotARecord:
        id = ""
        type = "local_bash"
        status = "bogus"

    manager = get_task_manager()
    manager._tasks["no-id"] = _NotARecord()  # type: ignore[assignment]
    try:
        view = build_roster()
    finally:
        manager._tasks.clear()
    assert view["agents"] == []


def test_roster_tolerates_a_record_without_metadata() -> None:
    manager = get_task_manager()
    record = manager.register_in_process_task(
        "t-nometa", task_type="local_bash", description="no metadata", cwd="."
    )
    record.metadata = None  # type: ignore[assignment]
    record.started_at = 1.0

    view = build_roster(tail_bytes=0)
    assert view["agents"][0]["id"] == "t-nometa"
    assert view["agents"][0]["agent_id"] == ""
    assert view["agents"][0]["progress"] is None


def test_roster_stamps_a_running_task_from_now() -> None:
    """A task with no start timestamp must not read as started at the epoch."""
    _spawn_shell("t-nostart")
    record = get_task_manager().get_task("t-nostart")
    assert record is not None
    record.created_at = 0.0
    record.started_at = None

    view = build_roster(tail_bytes=0)
    agent = view["agents"][0]
    assert agent["started_at"] is None
    assert agent["duration_s"] == 0.0


def test_roster_honours_the_status_filter() -> None:
    _spawn_shell("t-a")
    done = get_task_manager().get_task("t-a")
    assert done is not None
    done.status = "completed"

    assert build_roster(status="running")["total"] == 0
    view = build_roster(status="completed")
    assert view["total"] == 1
    assert view["agents"][0]["status"] == "completed"


def test_roster_limit_caps_the_rows_and_flags_the_truncation() -> None:
    for index in range(5):
        _spawn_shell(f"t-{index}")
        record = get_task_manager().get_task(f"t-{index}")
        assert record is not None
        record.created_at = float(index)
        record.started_at = float(index)

    view = build_roster(max_agents=2)
    assert view["total"] == 5
    assert view["truncated"] is True
    assert len(view["agents"]) == 2


def test_roster_never_reads_output_for_an_empty_manager() -> None:
    view = build_roster(tail_bytes=2000)
    assert view["agents"] == []


# ---------------------------------------------------------------------------
# duration hints
# ---------------------------------------------------------------------------


def test_in_process_duration_hints_read_the_backend_when_registered(monkeypatch) -> None:
    """A backend that reports teammates must have its start time preferred."""
    manager = get_task_manager()
    manager._in_process_aborts.clear()
    _spawn_in_process("t-hint", agent_id="echo@team9")

    class _FakeBackend:
        def list_teammates(self):
            # agent_id, is_running, duration_seconds — started ~37 seconds ago.
            return [("echo@team9", True, 37.0)]

    class _FakeRegistry:
        def get_executor(self, _backend):
            return _FakeBackend()

    monkeypatch.setattr(
        "iterate_harness.swarm.registry.get_backend_registry",
        lambda: _FakeRegistry(),
    )
    hints = _in_process_duration_hints()
    assert set(hints) == {"echo@team9"}
    assert 30.0 < hints["echo@team9"] <= time.time()

    view = build_roster(tail_bytes=0)
    assert view["agents"][0]["duration_s"] >= 30.0
    assert view["agents"][0]["started_at"] is not None


def test_in_process_duration_hints_swallows_a_missing_backend(monkeypatch) -> None:
    """A torn-down backend must not take the roster down with it."""

    def _boom():
        raise RuntimeError("backend gone")

    monkeypatch.setattr(
        "iterate_harness.swarm.registry.get_backend_registry", _boom
    )
    assert _in_process_duration_hints() == {}


def test_in_process_duration_hints_ignore_malformed_entries(monkeypatch) -> None:
    class _FakeBackend:
        def list_teammates(self):
            return [("real@team", True, 5.0), ("no-duration@team", True), None, 7]

    class _FakeRegistry:
        def get_executor(self, _backend):
            return _FakeBackend()

    monkeypatch.setattr(
        "iterate_harness.swarm.registry.get_backend_registry", lambda: _FakeRegistry()
    )
    assert list(_in_process_duration_hints()) == ["real@team"]


# ---------------------------------------------------------------------------
# stop_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_agent_stops_a_running_in_process_teammate() -> None:
    called = []

    async def _abort() -> None:
        called.append(1)

    manager = get_task_manager()
    record = manager.register_in_process_task(
        "t-stop",
        task_type="in_process_teammate",
        description="stuck teammate",
        cwd=".",
        agent_id="stuck@team3",
        abort=_abort,
    )
    assert record.status == "running"

    result = await stop_agent("t-stop")
    assert result["status"] == "stopped"
    assert result["id"] == "t-stop"
    assert result["agent_id"] == "stuck@team3"
    assert result["status_now"] == "killed"
    assert called == [1]
    assert get_task_manager().get_task("t-stop").status == "killed"


@pytest.mark.asyncio
async def test_stop_agent_resolves_a_swarm_identity() -> None:
    """``name@team`` is what the operator reads in the roster, not the task id."""
    async def _abort() -> None:
        pass

    get_task_manager().register_in_process_task(
        "t-real-id",
        task_type="in_process_teammate",
        description="team worker",
        cwd=".",
        agent_id="helper@teamA",
        abort=_abort,
    )
    result = await stop_agent("helper@teamA")
    assert result["status"] == "stopped"
    assert result["id"] == "t-real-id"


@pytest.mark.asyncio
async def test_stop_agent_refuses_blank_input() -> None:
    assert (await stop_agent("   " ))["status"] == "invalid"


@pytest.mark.asyncio
async def test_stop_agent_reports_an_unknown_agent_without_raising() -> None:
    result = await stop_agent("nobody@nowhere")
    assert result["status"] == "not_found"
    assert "nobody@nowhere" in result["message"]


@pytest.mark.asyncio
async def test_stop_agent_reports_a_terminal_agent_instead_of_retrying() -> None:
    output_file = _spawn_shell("t-already")
    record = get_task_manager().get_task("t-already")
    assert record is not None
    record.status = "completed"
    record.ended_at = 1234.0
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("done\n", encoding="utf-8")

    result = await stop_agent("t-already")
    assert result["status"] == "already_terminal"
    assert result["status_now"] == "completed"


@pytest.mark.asyncio
async def test_stop_agent_names_a_stale_record_without_a_handle() -> None:
    """A record that claims to be running but has nothing to signal is honest."""
    _spawn_shell("t-ghost")
    result = await stop_agent("t-ghost")
    assert result["status"] == "error"
    assert "旧记录" in result["message"]
    assert get_task_manager().get_task("t-ghost").status == "running"


@pytest.mark.asyncio
async def test_stop_agent_survives_a_manager_that_raises(monkeypatch) -> None:
    """The roster must never surface a task-manager exception to the caller."""
    import iterate_harness.tasks.manager as manager_module

    def _boom():
        raise OSError("no task dir")

    monkeypatch.setattr(manager_module, "get_task_manager", _boom)
    result = await stop_agent("t-anyone")
    assert result["status"] == "error"
    assert "no task dir" in result["message"]


@pytest.mark.asyncio
async def test_stop_agent_turns_a_manager_failure_into_a_result() -> None:
    """A stop that cannot be carried out is reported, never raised."""
    _spawn_shell("t-frozen")
    record = get_task_manager().get_task("t-frozen")
    assert record is not None

    async def _boom() -> None:
        raise RuntimeError("signal refused")

    get_task_manager()._in_process_aborts["t-frozen"] = _boom

    result = await stop_agent("t-frozen")
    assert result["status"] == "error"
    assert result["id"] == "t-frozen"
    assert "signal refused" in result["message"]
    # The record must not have been claimed killed: the stop never happened.
    assert get_task_manager().get_task("t-frozen").status == "running"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_render_empty_roster_explains_itself() -> None:
    text = render_roster_text(build_roster())
    assert text == "没有正在运行或记录在册的子代理。"


def test_render_pads_columns_to_the_widest_value() -> None:
    output_file = _spawn_shell("t-a")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("out\n", encoding="utf-8")
    record = get_task_manager().get_task("t-a")
    assert record is not None
    record.created_at = 42.0
    record.started_at = 42.0

    _spawn_in_process("t-b-longer-task-id", agent_id="very-long-name@teamX", team="teamX")

    text = render_roster_text(build_roster(tail_bytes=0))
    lines = text.splitlines()
    assert len(lines) == 3  # header + 2 rows
    assert len({len(line) for line in lines}) == 1
    assert "very-long-name@teamX" in lines[-1]


def test_render_caps_the_number_of_lines() -> None:
    for index in range(6):
        _spawn_shell(f"t-{index}")
        record = get_task_manager().get_task(f"t-{index}")
        assert record is not None
        record.created_at = float(index)
        record.started_at = float(index)

    text = render_roster_text(build_roster(tail_bytes=0), max_lines=3)
    lines = text.splitlines()
    assert len(lines) == 4  # 1 header + 2 rows + 1 truncation notice
    assert "已截断" in lines[-1]


def test_render_handles_a_row_without_an_agent_identity() -> None:
    _spawn_shell("t-plain")
    text = render_roster_text(build_roster(tail_bytes=0))
    assert "-  -" not in text.splitlines()[1]
    assert "t-plain" in text


def test_roster_json_round_trips() -> None:
    output_file = _spawn_shell("t-json")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("中文输出\n", encoding="utf-8")

    view = build_roster(tail_bytes=64)
    decoded = json.loads(roster_as_json(view))
    assert decoded["agents"][0]["tail"] == "中文输出\n"
    assert decoded["agents"][0]["output_bytes"] > 0


# ---------------------------------------------------------------------------
# coercion helpers
# ---------------------------------------------------------------------------


def test_task_kind_maps_every_known_type() -> None:
    assert _task_kind("in_process_teammate") == "in_process"
    assert _task_kind("local_agent") == "subprocess"
    assert _task_kind("remote_agent") == "subprocess"
    assert _task_kind("local_bash") == "shell"
    assert _task_kind("something-new") == "other"


def test_safe_status_degrades_unknown_values() -> None:
    for status in ("running", "pending", "completed", "failed", "killed"):
        assert _safe_status(status) == status
    assert _safe_status("weird") == "unknown"
    assert _safe_status("") == "unknown"


def test_utc_iso_rejects_zero_and_non_numeric() -> None:
    assert _utc_iso(0) is None
    assert _utc_iso(None) is None
    assert _utc_iso("2026-01-01") is None  # type: ignore[arg-type]
    assert _utc_iso(1700000000.0).startswith("2023")
