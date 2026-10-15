"""Regression tests for the WebUI intervention-channel hardening pass.

Covers the run-manager side of the reviewed defects:

- ``start()`` rejects on every non-terminal state *and* while the previous
  task's cleanup is still live (fix 1: sandbox/MCP runtime + lease leak).
- A stop arriving during ``start()``'s await window is honoured, not lost
  (fix 2), both at the route level and inside ``_run_loop``.
- Blocking work (kickoff build / lease probe / audit writes) runs off the
  event loop (fix 5).
- A pause-menu timeout on an operator-paused run never auto-resumes (fix 6).
- ``_stop`` with a done-only request registry recovers instead of pinning
  ``stopping`` (fix 7).
- ``send_message`` during ``pausing``/``stopping`` reports the accurate
  state (fix 8).
- Chat history stays readable after reset and the journal is capped on
  write (fix 9).
- Permission approve/deny decisions are audited (fix 10).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading

import pytest

import iterate_harness.web.run_manager as run_manager_module
from iterate_harness.web.hub import hub
from iterate_harness.web.run_manager import RunConflictError, RunManager, RunManagerError


@pytest.fixture()
async def manager():
    mgr = RunManager()
    yield mgr
    await mgr.reset()


def _drain(queue: asyncio.Queue) -> list:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


# ---------------------------------------------------------------------------
# Fix 1: start() must block on every non-terminal state + a live old task
# ---------------------------------------------------------------------------


class TestStartStateGuard:
    @pytest.mark.parametrize(
        "state", ["starting", "running", "paused", "pausing", "stopping"]
    )
    async def test_rejects_every_non_terminal_state(
        self, manager: RunManager, tmp_path, state: str
    ):
        manager.state = state  # type: ignore[assignment]
        with pytest.raises(RunConflictError):
            await manager.start(str(tmp_path), "review", False, "HEAD")
        assert manager._task is None

    async def test_rejects_while_previous_task_cleanup_live(
        self, manager: RunManager, tmp_path
    ):
        """``stopped`` is published *before* the old task's ``finally``
        closes the runtime / releases the lease. Starting in that window
        reset ``_bundle`` under the old task (sandbox/MCP leak) and let its
        lease release unlink the new run's lease file — so a live (not-done)
        task is a conflict even when the state already reads ``stopped``."""

        async def hanging_loop() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(hanging_loop())
        manager._task = task
        manager.state = "stopped"
        try:
            with pytest.raises(RunConflictError):
                await manager.start(str(tmp_path), "review", False, "HEAD")
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_allows_start_after_task_fully_done(self, manager: RunManager, tmp_path, monkeypatch):
        """A terminal state with a *done* task is the normal end-of-run
        situation and must remain startable."""

        async def finished_loop() -> None:
            return None

        task = asyncio.create_task(finished_loop())
        await task
        manager._task = task
        manager.state = "stopped"
        monkeypatch.setattr(manager, "_build_kickoff", lambda p, m, c, r: ("k", 1))

        async def fake_loop(*args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(manager, "_run_loop", fake_loop)
        run_id = await manager.start(str(tmp_path), "review", False, "HEAD")
        assert run_id == manager.run_id


# ---------------------------------------------------------------------------
# Fix 2: stop arriving during start()'s await window is not lost
# ---------------------------------------------------------------------------


class TestStopDuringStartWindow:
    async def test_stop_arriving_during_start_cancels_launch(
        self, manager: RunManager, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(manager, "_build_kickoff", lambda p, m, c, r: ("k", 3))
        original_publish = manager._publish_chat
        injected = {"stop": False}

        async def publish_then_stop(role: str, content: str, kind: str = "text"):
            entry = await original_publish(role, content, kind=kind)
            if not injected["stop"]:
                # Simulate the operator's stop landing during start()'s await
                # window — exactly once.
                injected["stop"] = True
                await manager._stop()
            return entry

        monkeypatch.setattr(manager, "_publish_chat", publish_then_stop)
        run_id = await manager.start(str(tmp_path), "review", False, "HEAD")
        await asyncio.sleep(0.05)  # give a wrongly-created task time to appear
        assert run_id
        # No run task was created and the manager did not end pinned on
        # "stopping" — the stop cancelled the start entirely.
        assert manager._task is None
        assert manager.state == "stopped"
        assert manager._stopping is False

    async def test_run_loop_bails_when_stop_already_requested(
        self, manager: RunManager, tmp_path
    ):
        manager._stopping = True
        manager._stopping_by = "run-x"
        await manager._run_loop(str(tmp_path), "review", False, "HEAD", "run-x")
        assert manager.state == "stopped"
        assert manager._stopping is False
        assert manager._stopping_by == ""
        # Bailed before claiming the lease: no lease file was written.
        assert not (tmp_path / ".iterate" / "run-lease.json").exists()


# ---------------------------------------------------------------------------
# Fix 5: blocking work off the event loop
# ---------------------------------------------------------------------------


class TestBlockingWorkOffLoop:
    async def test_kickoff_build_runs_in_worker_thread(
        self, manager: RunManager, tmp_path, monkeypatch
    ):
        seen: set[int] = set()

        def spy_kickoff(project_root: str, mode: str, changed: bool, ref: str):
            seen.add(threading.get_ident())
            return "kickoff", 3

        monkeypatch.setattr(manager, "_build_kickoff", spy_kickoff)

        async def fake_loop(*args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(manager, "_run_loop", fake_loop)
        await manager.start(str(tmp_path), "review", False, "HEAD")
        assert seen and threading.get_ident() not in seen

    async def test_foreign_driver_probe_runs_in_worker_thread(
        self, manager: RunManager, tmp_path, monkeypatch
    ):
        seen: set[int] = set()

        def probe(_root: str):
            seen.add(threading.get_ident())
            return None

        monkeypatch.setattr(run_manager_module, "foreign_driver", probe)
        monkeypatch.setattr(manager, "_build_kickoff", lambda p, m, c, r: ("k", 1))

        async def fake_loop(*args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(manager, "_run_loop", fake_loop)
        await manager.start(str(tmp_path), "review", False, "HEAD")
        assert seen and threading.get_ident() not in seen

    async def test_audit_record_runs_in_worker_thread(
        self, manager: RunManager, tmp_path, monkeypatch
    ):
        manager.project_root = str(tmp_path)
        seen: set[int] = set()

        class SpyAudit:
            def __init__(self, _root: object) -> None:
                pass

            def record(self, action: str, target: str, *, summary=None) -> None:
                seen.add(threading.get_ident())

        monkeypatch.setattr(run_manager_module, "AuditLog", SpyAudit)
        await manager._audit("run.start", "review")
        assert seen and threading.get_ident() not in seen


# ---------------------------------------------------------------------------
# Fix 6: operator-paused run must not auto-resume on menu timeout
# ---------------------------------------------------------------------------


class TestOperatorPauseTimeout:
    async def test_operator_pause_timeout_stops_instead_of_resuming(
        self, manager: RunManager, monkeypatch
    ):
        monkeypatch.setattr(run_manager_module, "_QUESTION_TIMEOUT", 0.05)
        manager._pause_requested = True
        options = [
            {"value": "resume", "label": "继续", "description": "keep"},
            {"value": "stop", "label": "停止", "description": "halt"},
        ]
        queue = await hub.subscribe()
        try:
            task = asyncio.create_task(manager._ask_user_select("下一轮怎么走？", options))
            for _ in range(50):
                if manager.waiting_for == "user_select":
                    break
                await asyncio.sleep(0.01)
            result = await asyncio.wait_for(task, timeout=1.0)
        finally:
            await hub.unsubscribe(queue)
        # NOT "resume": an operator-paused run falls back to the engine's
        # clean stop instead of continuing unattended.
        assert result == "stop"
        # The pause intent spans exactly one menu presentation.
        assert manager._pause_requested is False
        # A visible error-kind notice explains why the run stopped.
        notices = [
            event.data["content"]
            for event in _drain(queue)
            if event.type == "chat-message" and event.data.get("kind") == "error"
        ]
        assert any("超时" in notice and "停止" in notice for notice in notices)

    async def test_unattended_menu_timeout_keeps_first_option_default(
        self, manager: RunManager, monkeypatch
    ):
        """Without an operator pause the documented safe default (first
        option, "resume") still applies."""
        monkeypatch.setattr(run_manager_module, "_QUESTION_TIMEOUT", 0.05)
        options = [
            {"value": "resume", "label": "继续", "description": "keep"},
            {"value": "stop", "label": "停止", "description": "halt"},
        ]
        task = asyncio.create_task(manager._ask_user_select("选择？", options))
        for _ in range(50):
            if manager.waiting_for == "user_select":
                break
            await asyncio.sleep(0.01)
        assert await asyncio.wait_for(task, timeout=1.0) == "resume"

    async def test_pause_flag_cleared_by_reset(self, manager: RunManager):
        manager._pause_requested = True
        await manager.reset()
        assert manager._pause_requested is False


# ---------------------------------------------------------------------------
# Fix 7: _stop with a done-only registry must not pin "stopping"
# ---------------------------------------------------------------------------


class TestStopDoneOnlyRegistry:
    @staticmethod
    def _done_future() -> asyncio.Future[str]:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        future.set_result("stop")
        return future

    async def test_idle_state_reports_no_run(self, manager: RunManager):
        manager.waiting_for = "user_select"
        manager._request_registry["r1"] = self._done_future()
        with pytest.raises(RunManagerError, match="无需停止"):
            await manager.control("stop")
        assert manager.state == "idle"
        assert manager._stopping is False

    async def test_stale_paused_state_recovers_to_stopped(
        self, manager: RunManager
    ):
        """A "paused" whose task and futures are all gone can never leave
        "stopping" behind: recover the truth instead of pinning the console."""
        manager.state = "paused"
        manager.waiting_for = "user_select"
        manager._request_registry["r1"] = self._done_future()
        with pytest.raises(RunManagerError, match="无需停止"):
            await manager.control("stop")
        assert manager.state == "stopped"
        assert manager._stopping is False

    async def test_start_allowed_after_done_only_stop_recovery(
        self, manager: RunManager, tmp_path, monkeypatch
    ):
        manager.state = "paused"
        manager.waiting_for = "user_select"
        manager._request_registry["r1"] = self._done_future()
        with pytest.raises(RunManagerError):
            await manager.control("stop")
        monkeypatch.setattr(manager, "_build_kickoff", lambda p, m, c, r: ("k", 1))

        async def fake_loop(*args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(manager, "_run_loop", fake_loop)
        run_id = await manager.start(str(tmp_path), "review", False, "HEAD")
        assert run_id == manager.run_id
        assert manager.state == "starting"


# ---------------------------------------------------------------------------
# Fix 8: send_message during pausing/stopping is accurate
# ---------------------------------------------------------------------------


class TestSendMessageDuringTransitions:
    async def test_pausing_reports_pausing(self, manager: RunManager):
        manager.state = "pausing"
        with pytest.raises(RunManagerError, match="暂停中"):
            await manager.send_message("你好")

    async def test_stopping_reports_stopping(self, manager: RunManager):
        manager.state = "stopping"
        with pytest.raises(RunManagerError, match="停止中"):
            await manager.send_message("你好")


# ---------------------------------------------------------------------------
# Fix 9: history readable after reset + journal cap
# ---------------------------------------------------------------------------


class TestHistoryAfterReset:
    async def test_history_readable_after_reset(self, manager: RunManager, tmp_path):
        manager._chat_dir = tmp_path / ".iterate"
        await manager._publish_chat("user", "继续", kind="decision")
        await manager.reset()
        entries = manager.history()
        assert [entry["content"] for entry in entries] == ["继续"]
        assert manager._chat_dir == tmp_path / ".iterate"

    async def test_new_project_root_switches_journal(
        self, manager: RunManager, tmp_path
    ):
        manager._chat_dir = tmp_path / "a" / ".iterate"
        other = tmp_path / "b"
        manager._reset(str(other))
        assert manager._chat_dir == other / ".iterate"


class TestJournalCap:
    async def test_journal_capped_on_write(
        self, manager: RunManager, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(run_manager_module, "_HISTORY_LIMIT", 10)
        manager._chat_dir = tmp_path / ".iterate"
        for i in range(30):
            await manager._publish_chat("system", f"msg {i}", kind="status")
        chat_file = tmp_path / ".iterate" / "web-chat.jsonl"
        lines = chat_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 10
        # The newest entries survive; the oldest were rotated away.
        entries = manager.history()
        assert [entry["content"] for entry in entries] == [
            f"msg {i}" for i in range(20, 30)
        ]


# ---------------------------------------------------------------------------
# Fix 10: permission decisions are audited
# ---------------------------------------------------------------------------


class TestPermissionAudit:
    async def _arm_permission(self, manager: RunManager) -> asyncio.Future[bool]:
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        manager.state = "paused"
        manager.waiting_for = "permission"
        manager.permission_tool = "bash"
        manager.permission_reason = "run pytest"
        manager._request_registry["r1"] = future
        return future

    async def test_approve_is_audited(self, manager: RunManager, tmp_path):
        manager.project_root = str(tmp_path)
        manager.run_id = "run-9"
        future = await self._arm_permission(manager)
        await manager.send_message("approve")
        assert await asyncio.wait_for(future, timeout=0.5) is True
        audit = (tmp_path / ".iterate" / "web-audit.jsonl").read_text(encoding="utf-8")
        assert "run.permission" in audit
        assert "approve" in audit
        assert "bash" in audit
        assert "run-9" in audit

    async def test_deny_is_audited(self, manager: RunManager, tmp_path):
        manager.project_root = str(tmp_path)
        manager.run_id = "run-10"
        future = await self._arm_permission(manager)
        await manager.send_message("拒绝")
        assert await asyncio.wait_for(future, timeout=0.5) is False
        audit = (tmp_path / ".iterate" / "web-audit.jsonl").read_text(encoding="utf-8")
        assert "run.permission" in audit
        assert "deny" in audit
