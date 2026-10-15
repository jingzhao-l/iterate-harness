"""Tests for background task management."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

import pytest

from iterate_harness.tasks.manager import (
    BackgroundTaskManager,
    _encode_task_worker_payload,
    _MAX_TERMINAL_RECORDS,
)
from iterate_harness.tasks.types import TaskRecord


@pytest.mark.asyncio
async def test_create_shell_task_and_read_output(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_shell_task(
        command="printf 'hello task'",
        description="hello",
        cwd=tmp_path,
    )

    await asyncio.wait_for(manager._waiters[task.id], timeout=30)  # type: ignore[attr-defined]
    updated = manager.get_task(task.id)
    assert updated is not None
    assert updated.status == "completed"
    assert "hello task" in manager.read_task_output(task.id)


@pytest.mark.asyncio
async def test_create_agent_task_with_command_override_and_write(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_agent_task(
        prompt="first",
        description="agent",
        cwd=tmp_path,
        command="while read line; do echo \"got:$line\"; break; done",
    )

    await asyncio.wait_for(manager._waiters[task.id], timeout=30)  # type: ignore[attr-defined]
    assert "got:first" in manager.read_task_output(task.id)


@pytest.mark.asyncio
async def test_create_agent_task_preserves_multiline_prompt(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_agent_task(
        prompt="line 1\nline 2\nline 3",
        description="agent",
        cwd=tmp_path,
        command=(
            "python -u -c \"import sys, json; "
            "print(json.loads(sys.stdin.readline())['text'].replace(chr(10), '|'))\""
        ),
    )

    await asyncio.wait_for(manager._waiters[task.id], timeout=30)  # type: ignore[attr-defined]
    assert "line 1|line 2|line 3" in manager.read_task_output(task.id)


@pytest.mark.asyncio
async def test_write_to_stopped_agent_task_restarts_process(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_agent_task(
        prompt="ready",
        description="agent",
        cwd=tmp_path,
        command="while read line; do echo \"got:$line\"; break; done",
    )
    await asyncio.wait_for(manager._waiters[task.id], timeout=30)  # type: ignore[attr-defined]

    await manager.write_to_task(task.id, "follow-up")
    await asyncio.wait_for(manager._waiters[task.id], timeout=30)  # type: ignore[attr-defined]

    output = manager.read_task_output(task.id)
    assert "got:ready" in output
    assert "[IterateHarness] Agent task restarted; prior interactive context was not preserved." in output
    assert "got:follow-up" in output
    updated = manager.get_task(task.id)
    assert updated is not None
    assert updated.metadata["restart_count"] == "1"
    assert updated.metadata["status_note"] == "Task restarted; prior interactive context was not preserved."


@pytest.mark.asyncio
async def test_create_shell_task_stores_env_on_record(tmp_path: Path, monkeypatch):
    """``env=`` passed to ``create_shell_task`` must land on the
    ``TaskRecord.env`` field so ``_start_process`` can forward it to the
    subprocess. Plumbing this dict instead of baking ``KEY=val`` into the
    command string is the fix for #230."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_shell_task(
        command="printf 'noop'",
        description="env test",
        cwd=tmp_path,
        env={"MY_OH_TEST_VAR": "hello-230"},
    )
    await asyncio.wait_for(manager._waiters[task.id], timeout=30)  # type: ignore[attr-defined]

    stored = manager.get_task(task.id)
    assert stored is not None
    assert stored.env == {"MY_OH_TEST_VAR": "hello-230"}


@pytest.mark.asyncio
async def test_create_shell_task_argv_path_bypasses_shell(tmp_path: Path, monkeypatch):
    """The argv path runs the executable directly via
    ``asyncio.create_subprocess_exec(*argv)`` with no shell. This is the
    fix for #230: bash on Windows cannot exec Windows-pathed binaries
    when launched via ``create_subprocess_exec``, so teammate spawn must
    not route through a shell."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    import sys
    task = await manager.create_shell_task(
        argv=[sys.executable, "-c", "print('argv-route-ok')"],
        description="argv direct exec",
        cwd=tmp_path,
    )
    await asyncio.wait_for(manager._waiters[task.id], timeout=30)  # type: ignore[attr-defined]

    output = manager.read_task_output(task.id)
    assert "argv-route-ok" in output
    stored = manager.get_task(task.id)
    assert stored is not None
    assert stored.argv is not None
    assert stored.command is None


@pytest.mark.asyncio
async def test_create_shell_task_rejects_both_command_and_argv(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()
    with pytest.raises(ValueError, match="only one"):
        await manager.create_shell_task(
            command="printf hi",
            argv=["echo", "hi"],
            description="both",
            cwd=tmp_path,
        )


@pytest.mark.asyncio
async def test_create_shell_task_rejects_neither_command_nor_argv(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()
    with pytest.raises(ValueError, match="either command or argv"):
        await manager.create_shell_task(
            description="empty",
            cwd=tmp_path,
        )


@pytest.mark.asyncio
async def test_start_process_forwards_env_to_subprocess(tmp_path: Path, monkeypatch):
    """End-to-end: env vars passed via ``create_shell_task(env=...)`` must be
    visible to the spawned subprocess. The subprocess prints the value, and
    we read it back from the task output."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_shell_task(
        command='printf "value=%s" "$MY_OH_TEST_VAR"',
        description="env passthrough",
        cwd=tmp_path,
        env={"MY_OH_TEST_VAR": "spawn-230"},
    )
    await asyncio.wait_for(manager._waiters[task.id], timeout=30)  # type: ignore[attr-defined]

    output = manager.read_task_output(task.id)
    assert "value=spawn-230" in output


def test_encode_task_worker_payload_wraps_multiline_text() -> None:
    payload = _encode_task_worker_payload("alpha\nbeta\n")
    assert json.loads(payload.decode("utf-8")) == {"text": "alpha\nbeta"}


def test_encode_task_worker_payload_preserves_structured_messages() -> None:
    raw = '{"text":"follow up","from":"coordinator"}'
    payload = _encode_task_worker_payload(raw)
    assert payload.decode("utf-8") == raw + "\n"


@pytest.mark.asyncio
async def test_stop_task(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_shell_task(
        command="sleep 30",
        description="sleeper",
        cwd=tmp_path,
    )
    await manager.stop_task(task.id)
    updated = manager.get_task(task.id)
    assert updated is not None
    assert updated.status == "killed"


@pytest.mark.asyncio
async def test_completion_listener_fires_when_task_finishes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()
    seen: list[tuple[str, str, int | None]] = []
    done = asyncio.Event()

    async def _listener(task):
        seen.append((task.id, task.status, task.return_code))
        done.set()

    manager.register_completion_listener(_listener)

    task = await manager.create_shell_task(
        command="printf 'done'",
        description="listener",
        cwd=tmp_path,
    )

    await asyncio.wait_for(done.wait(), timeout=30)

    assert seen == [(task.id, "completed", 0)]


# ---------------------------------------------------------------------------
# In-process task registration (swarm in-process backend bridge)
# ---------------------------------------------------------------------------


async def _noop_abort() -> None:
    return None


@pytest.mark.asyncio
async def test_register_in_process_task_record(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    record = manager.register_in_process_task(
        "in_process_abc",
        task_type="in_process_teammate",
        description="teammate",
        cwd=str(tmp_path),
        prompt="do the thing",
        agent_id="worker@test",
        team="test",
        abort=_noop_abort,
    )

    assert manager.get_task("in_process_abc") is record
    assert record.status == "running"
    assert record.type == "in_process_teammate"
    assert record.prompt == "do the thing"
    assert record.metadata["agent_id"] == "worker@test"
    assert record.metadata["team"] == "test"
    # No subprocess artifacts are created for an in-process task.
    assert "in_process_abc" not in manager._processes  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_write_to_task_in_process_routes_to_mailbox(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()
    manager.register_in_process_task(
        "in_process_abc",
        task_type="in_process_teammate",
        description="teammate",
        cwd=str(tmp_path),
        agent_id="worker@test",
        team="test",
        abort=_noop_abort,
    )

    await manager.write_to_task("in_process_abc", "do the thing")

    from iterate_harness.swarm.mailbox import TeammateMailbox

    mailbox = TeammateMailbox(team_name="test", agent_id="worker@test")
    messages = await mailbox.read_all(unread_only=False)
    assert any(m.payload.get("content") == "do the thing" for m in messages)


@pytest.mark.asyncio
async def test_stop_task_in_process_calls_abort_and_finalizes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()
    cancelled: list[bool] = []

    async def _abort() -> None:
        cancelled.append(True)

    manager.register_in_process_task(
        "in_process_abc",
        task_type="in_process_teammate",
        description="teammate",
        cwd=str(tmp_path),
        agent_id="worker@test",
        team="test",
        abort=_abort,
    )

    record = await manager.stop_task("in_process_abc")
    assert record.status == "killed"
    assert cancelled == [True]
    assert record.ended_at is not None


@pytest.mark.asyncio
async def test_mark_in_process_terminal_fires_listener_and_keeps_killed(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()
    manager.register_in_process_task(
        "in_process_abc",
        task_type="in_process_teammate",
        description="teammate",
        cwd=str(tmp_path),
        agent_id="worker@test",
        team="test",
        abort=_noop_abort,
    )
    fired: list[str] = []

    async def _listener(task) -> None:
        fired.append(task.status)

    manager.register_completion_listener(_listener)

    await manager.mark_in_process_terminal("in_process_abc", status="completed", return_code=0)
    record = manager.get_task("in_process_abc")
    assert record is not None
    assert record.status == "completed"
    assert record.return_code == 0
    assert fired == ["completed"]

    # A deliberate stop must not be overwritten by the task's own exit path.
    manager.register_in_process_task(
        "in_process_stopped",
        task_type="in_process_teammate",
        description="teammate2",
        cwd=str(tmp_path),
        agent_id="worker2@test",
        team="test",
        abort=_noop_abort,
    )
    await manager.stop_task("in_process_stopped")
    await manager.mark_in_process_terminal("in_process_stopped", status="completed", return_code=0)
    stopped = manager.get_task("in_process_stopped")
    assert stopped is not None
    assert stopped.status == "killed"


@pytest.mark.asyncio
async def test_stop_task_in_process_abort_failure_reraises_and_preserves_record(
    tmp_path: Path, monkeypatch
):
    """Regression test: when the in-process abort raises, ``stop_task`` must
    surface the failure instead of claiming success — and the record (still
    running) plus the abort mapping must survive so a retry can settle it."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    async def _failing_abort() -> None:
        raise RuntimeError("backend refused to stop")

    manager.register_in_process_task(
        "in_process_fail",
        task_type="in_process_teammate",
        description="teammate",
        cwd=str(tmp_path),
        agent_id="worker@test",
        team="test",
        abort=_failing_abort,
    )

    with pytest.raises(RuntimeError, match="backend refused to stop"):
        await manager.stop_task("in_process_fail")

    record = manager.get_task("in_process_fail")
    assert record is not None
    # Not finalized as killed: the stop never happened.
    assert record.status == "running"
    assert record.ended_at is None
    # Abort mapping preserved so a retry can still settle the task.
    assert "in_process_fail" in manager._in_process_aborts  # type: ignore[attr-defined]


def test_evict_old_terminal_records_pops_generations(tmp_path: Path, monkeypatch):
    """Regression test: evicting a terminal record must also drop its
    generation counter, otherwise ``_generations`` grows once per spawned
    task for the whole session even after the record is gone."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    excess = 5
    total = _MAX_TERMINAL_RECORDS + excess
    now = time.time()
    for index in range(total):
        task_id = f"task_{index}"
        record = TaskRecord(
            id=task_id,
            type="local_bash",
            status="completed",
            description="done",
            cwd=str(tmp_path),
            output_file=tmp_path / f"{task_id}.log",
            created_at=now + index,
        )
        record.ended_at = now + index
        manager._tasks[task_id] = record  # type: ignore[attr-defined]
        manager._generations[task_id] = 1  # type: ignore[attr-defined]

    manager._evict_old_terminal_records()  # type: ignore[attr-defined]

    # Oldest ``excess`` records (and their generations) evicted; newest kept.
    assert len(manager._tasks) == _MAX_TERMINAL_RECORDS  # type: ignore[attr-defined]
    assert len(manager._generations) == _MAX_TERMINAL_RECORDS  # type: ignore[attr-defined]
    assert "task_0" not in manager._tasks  # type: ignore[attr-defined]
    assert "task_0" not in manager._generations  # type: ignore[attr-defined]
    assert f"task_{total - 1}" in manager._tasks  # type: ignore[attr-defined]
    assert f"task_{total - 1}" in manager._generations  # type: ignore[attr-defined]
