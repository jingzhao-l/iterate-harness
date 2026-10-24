"""WebUI route tests for /api/v1/agents (design §17.3 P9).

The roster is the operator's answer to "which worker is stuck and why"; the
shutdown route is the intervention channel that goes with it. Every assertion
here exercises the real routes through the real app, with a real task manager
record behind each row.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.swarm.roster import _TRUNCATION_MARKER
from iterate_harness.tasks.manager import (
    _pid_alive,
    get_task_manager,
    reset_task_manager,
)
from iterate_harness.web.api import create_app
from iterate_harness.web.security import AuditLog


@pytest.fixture(autouse=True)
def _clean_task_manager():
    """Isolate every test from whichever harness run left a manager behind."""
    reset_task_manager()
    yield
    reset_task_manager()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _spawn(task_id: str, *, agent_id: str = "", task_type: str = "in_process_teammate") -> Path:
    """Register one tracked worker and return its output file."""
    manager = get_task_manager()
    record = manager.register_in_process_task(
        task_id,
        task_type=task_type,
        description=f"worker {task_id}",
        cwd=".",
        agent_id=agent_id,
        team=agent_id.split("@")[-1] if agent_id else "",
    )
    return record.output_file


# ---------------------------------------------------------------------------
# GET /agents
# ---------------------------------------------------------------------------


def test_agents_route_is_empty_for_a_fresh_manager(client: TestClient, tmp_path: Path) -> None:
    response = client.get("/api/v1/agents", params={"project_root": str(tmp_path)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["agents"] == []
    assert payload["running"] == 0
    assert payload["truncated"] is False
    assert payload["generated_at"]


def test_agents_route_lists_running_first_with_identity(client: TestClient, tmp_path: Path) -> None:
    output_file = _spawn("t-teammate", agent_id="reviewer@team1")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("checking tests\n", encoding="utf-8")
    _spawn("t-shell", task_type="local_bash")

    payload = client.get("/api/v1/agents", params={"project_root": str(tmp_path)}).json()
    assert payload["total"] == 2
    ids = [agent["id"] for agent in payload["agents"]]
    assert "t-teammate" in ids and "t-shell" in ids
    rows = {agent["id"]: agent for agent in payload["agents"]}
    assert rows["t-teammate"]["kind"] == "in_process"
    assert rows["t-teammate"]["agent_id"] == "reviewer@team1"
    assert rows["t-teammate"]["running"] is True
    assert rows["t-shell"]["kind"] == "shell"
    assert rows["t-shell"]["agent_id"] == ""
    assert "checking tests" in rows["t-teammate"]["tail"]


def test_agents_route_reports_a_terminal_worker_without_counting_it_running(
    client: TestClient, tmp_path: Path
) -> None:
    _spawn("t-done", agent_id="done@team1")
    record = get_task_manager().get_task("t-done")
    assert record is not None
    record.status = "failed"
    record.ended_at = 9000.0
    record.return_code = 3

    payload = client.get("/api/v1/agents", params={"project_root": str(tmp_path)}).json()
    assert payload["running"] == 0
    assert payload["failed"] == 1
    assert payload["agents"][0]["return_code"] == 3
    assert payload["agents"][0]["ended_at"] is not None


def test_agents_route_filter_by_status(client: TestClient, tmp_path: Path) -> None:
    _spawn("t-a", agent_id="a@t")
    _spawn("t-b", agent_id="b@t")
    record = get_task_manager().get_task("t-b")
    assert record is not None
    record.status = "completed"

    payload = client.get(
        "/api/v1/agents", params={"project_root": str(tmp_path), "status": "completed"}
    ).json()
    assert payload["total"] == 1
    assert payload["agents"][0]["id"] == "t-b"


def test_agents_route_tail_cap_is_enforced(client: TestClient, tmp_path: Path) -> None:
    """A chatty worker must not turn a roster request into megabytes."""
    output_file = _spawn("t-chatty", agent_id="chatty@t")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("x" * 50_000, encoding="utf-8")

    payload = client.get(
        "/api/v1/agents", params={"project_root": str(tmp_path), "tail": 512}
    ).json()
    agent = payload["agents"][0]
    assert agent["output_bytes"] == 50_000
    assert len(agent["tail"].encode("utf-8")) <= 512 + 64
    assert _TRUNCATION_MARKER.split("(")[0] in agent["tail"]


def test_agents_route_tail_zero_omits_tails(client: TestClient, tmp_path: Path) -> None:
    output_file = _spawn("t-silent", agent_id="s@t")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("some output\n", encoding="utf-8")

    payload = client.get(
        "/api/v1/agents", params={"project_root": str(tmp_path), "tail": 0}
    ).json()
    agent = payload["agents"][0]
    assert agent["tail"] == ""
    assert agent["output_bytes"] > 0


def test_agents_route_truncates_at_the_limit_and_says_so(client: TestClient, tmp_path: Path) -> None:
    for index in range(4):
        _spawn(f"t-{index}", agent_id=f"w{index}@t")

    payload = client.get(
        "/api/v1/agents", params={"project_root": str(tmp_path), "limit": 2}
    ).json()
    assert payload["total"] == 4
    assert payload["truncated"] is True
    assert len(payload["agents"]) == 2


def test_agents_route_rejects_bad_status_values(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/v1/agents", params={"project_root": str(tmp_path), "status": "exploding"}
    )
    assert response.status_code == 422


def test_agents_route_rejects_out_of_range_tail(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/v1/agents", params={"project_root": str(tmp_path), "tail": 999_999}
    )
    assert response.status_code == 422


def test_agents_route_requires_a_valid_project_root(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/v1/agents", params={"project_root": str(tmp_path / "missing")}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /agents/shutdown
# ---------------------------------------------------------------------------


def test_shutdown_requires_confirm(client: TestClient, tmp_path: Path) -> None:
    _spawn("t-guard", agent_id="guarded@t")
    response = client.post(
        "/api/v1/agents/shutdown", params={"project_root": str(tmp_path), "agent_id": "t-guard"}
    )
    assert response.status_code == 422
    assert "confirm=true" in response.json()["detail"]
    # And nothing was killed.
    assert get_task_manager().get_task("t-guard").status == "running"


def test_shutdown_rejects_a_blank_agent_id(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/v1/agents/shutdown",
        params={"project_root": str(tmp_path), "agent_id": "", "confirm": "true"},
    )
    assert response.status_code == 422


def test_shutdown_stops_a_running_worker_and_audits_it(client: TestClient, tmp_path: Path) -> None:
    calls = []

    async def _abort() -> None:
        calls.append(1)

    get_task_manager().register_in_process_task(
        "t-stop",
        task_type="in_process_teammate",
        description="stuck worker",
        cwd=".",
        agent_id="stuck@team9",
        abort=_abort,
    )

    response = client.post(
        "/api/v1/agents/shutdown",
        params={"project_root": str(tmp_path), "agent_id": "stuck@team9", "confirm": "true"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "stopped"
    assert payload["id"] == "t-stop"
    assert payload["agent_id"] == "stuck@team9"
    assert payload["status_now"] == "killed"
    assert calls == [1]

    entries = AuditLog(tmp_path).entries()
    assert entries, "a shutdown must leave an audit line"
    entry = entries[-1]
    assert entry["action"] == "agent.shutdown"
    assert entry["target"] == "t-stop"
    assert entry["summary"]["status_now"] == "killed"


def test_shutdown_reports_an_unknown_worker(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/v1/agents/shutdown",
        params={"project_root": str(tmp_path), "agent_id": "nobody@nowhere", "confirm": "true"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_found"
    assert "nobody@nowhere" in payload["message"]


def test_shutdown_reports_an_already_terminal_worker(client: TestClient, tmp_path: Path) -> None:
    _spawn("t-terminal", agent_id="finished@t")
    record = get_task_manager().get_task("t-terminal")
    assert record is not None
    record.status = "completed"
    record.ended_at = 1000.0

    response = client.post(
        "/api/v1/agents/shutdown",
        params={"project_root": str(tmp_path), "agent_id": "t-terminal", "confirm": "true"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "already_terminal"
    assert payload["status_now"] == "completed"


def test_shutdown_returns_an_error_shape_when_the_manager_fails(
    client: TestClient, tmp_path: Path
) -> None:
    """A failed stop is reported to the operator, never raised as a 500."""
    _spawn("t-frozen", agent_id="frozen@t")

    async def _boom() -> None:
        raise RuntimeError("signal refused")

    get_task_manager()._in_process_aborts["t-frozen"] = _boom

    response = client.post(
        "/api/v1/agents/shutdown",
        params={"project_root": str(tmp_path), "agent_id": "t-frozen", "confirm": "true"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "error"
    assert "signal refused" in payload["message"]
    assert get_task_manager().get_task("t-frozen").status == "running"


# ---------------------------------------------------------------------------
# A real subprocess, not a hand-built record: this is the end-to-end proof
# that the roster sees a worker the engine actually spawned.
# ---------------------------------------------------------------------------


async def test_roster_lists_a_real_subprocess_task_with_its_live_output(
    tmp_path: Path,
) -> None:
    """Spawn a real process and read it back through the HTTP route.

    Everything above registers records by hand; this one goes through
    ``create_shell_task`` so a regression that loses the link between the task
    manager and a live worker fails here rather than only when an operator
    opens the page during a hung run.
    """
    manager = get_task_manager()
    record = await manager.create_shell_task(
        command='echo "warming up"; sleep 30; echo "done"',
        description="warmup shell worker",
        cwd=str(tmp_path),
    )
    try:
        await asyncio.sleep(0.4)
        with TestClient(create_app()) as client:
            response = client.get(
                "/api/v1/agents", params={"project_root": str(tmp_path), "tail": 512}
            )
        assert response.status_code == 200, response.text
        view = response.json()
        rows = {row["id"]: row for row in view["agents"]}
        assert record.id in rows, "the spawned worker must appear in the roster"
        row = rows[record.id]
        assert row["kind"] == "shell"
        assert row["running"] is True
        assert row["status"] == "running"
        assert row["duration_s"] > 0
        assert row["output_bytes"] > 0
        # The operator's whole point is "what did it last say".
        assert "warming up" in row["tail"]
        assert view["running"] >= 1
    finally:
        stopped = await manager.stop_task(record.id)
        assert stopped.status == "killed"


async def test_shutdown_route_stops_a_real_subprocess(tmp_path: Path) -> None:
    """The intervention channel kills the actual process group.

    The stop is asserted on its *observable* effect — the OS process is gone and
    the record says killed — rather than on one particular outcome string: a
    stop raced by the watcher's own done-callback can legitimately arrive back
    as ``already_terminal`` instead of ``stopped``, and both mean the worker is
    no longer running. What must never happen is a reported *failure* for a stop
    that did happen.
    """
    manager = get_task_manager()
    record = await manager.create_shell_task(
        command="sleep 30",
        description="long shell worker",
        cwd=str(tmp_path),
    )
    process = manager._processes[record.id]
    pid = process.pid
    assert pid and pid > 0, "the worker must actually have a pid"

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/agents/shutdown",
            params={"project_root": str(tmp_path), "agent_id": record.id, "confirm": "true"},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] in {"stopped", "already_terminal"}, payload
    assert payload["id"] == record.id
    assert payload["status_now"] in {"killed", "failed"}, payload
    assert get_task_manager().get_task(record.id).status in {"killed", "failed"}
    assert not _pid_alive(pid), "the OS process must actually be gone"


async def test_roster_keeps_a_finished_subprocess_as_completed(tmp_path: Path) -> None:
    """A worker that exited on its own stays in the roster as completed."""
    manager = get_task_manager()
    record = await manager.create_shell_task(
        command="echo finished",
        description="short shell worker",
        cwd=str(tmp_path),
    )
    # Wait for the watcher to settle the record; a short worker exits before we
    # would otherwise observe it, and the roster reports live state.
    deadline = time.monotonic() + 10
    while record.status == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert record.status != "running", "the worker must have settled"

    with TestClient(create_app()) as client:
        view = client.get(
            "/api/v1/agents", params={"project_root": str(tmp_path)}
        ).json()
    row = {r["id"]: r for r in view["agents"]}[record.id]
    assert row["running"] is False
    assert row["status"] == "completed"
    assert row["ended_at"] is not None
    assert row["return_code"] == 0
    assert row["duration_s"] >= 0
    assert view["completed"] >= 1
