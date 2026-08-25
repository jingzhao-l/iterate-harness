"""Regression tests for RunManager / chat-start defect fixes.

Covers:
- ``_resume`` / ``_stop`` on an already-resolved pending future must not
  raise ``InvalidStateError`` (double-click continue/stop -> HTTP 500).
- ``RunManager.start`` must pre-validate the kickoff *synchronously* so an
  invalid ref / clean worktree fails fast as a 4xx instead of being
  swallowed by the background run task (which returned 200).
- The background ``_run_loop`` finally block must only clear the ``_stopping``
  flag it placed itself, so an old task finishing after a new run started
  cannot clobber the new run's stop request.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import iterate_harness.web.run_manager as run_manager_module
from iterate_harness.web.api import create_app
from iterate_harness.web.run_manager import RunManager, RunManagerError, run_manager


@pytest.fixture()
async def manager():
    mgr = RunManager()
    yield mgr
    await mgr.reset()


@pytest.fixture()
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
async def _reset_run_manager():
    yield
    await run_manager.reset()


# ---------------------------------------------------------------------------
# Defect 1: repeated set_result on a done future -> InvalidStateError -> 500
# ---------------------------------------------------------------------------


class TestResumeStopOnResolvedFuture:
    async def test_resume_skips_already_resolved_future(self, manager: RunManager):
        """Regression: a second "continue" click must not raise
        InvalidStateError when the pending future was already resolved by the
        first click (the select channel's finally may not have run yet)."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        future.set_result("resume")  # already resolved by a previous resume
        manager.waiting_for = "user_select"
        manager._request_registry["r1"] = future

        with pytest.raises(RunManagerError):
            await manager.control("resume")
        # The stale future keeps its result; nothing exploded.
        assert future.result() == "resume"

    async def test_resume_mixed_done_and_pending_futures(self, manager: RunManager):
        """When a stale done future is followed by a live one, the live one is
        resolved instead of erroring on the stale entry."""
        stale: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        stale.set_result("resume")
        live: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        manager.waiting_for = "user_select"
        manager._request_registry["stale"] = stale
        manager._request_registry["live"] = live

        result = await manager.control("resume")
        assert result["ok"] is True
        assert await asyncio.wait_for(live, timeout=0.5) == "resume"
        assert stale.result() == "resume"  # untouched

    async def test_stop_skips_already_resolved_future(self, manager: RunManager):
        """Regression: a repeated "stop" while the select future is already
        resolved must return ok instead of raising InvalidStateError."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        future.set_result("stop")
        manager.waiting_for = "user_select"
        manager._request_registry["r1"] = future

        result = await manager.control("stop")
        assert result["ok"] is True
        assert future.result() == "stop"


# ---------------------------------------------------------------------------
# Defect 2: kickoff validation errors swallowed by the background task
# ---------------------------------------------------------------------------


class TestStartPrevalidation:
    async def test_start_prevalidates_kickoff_and_rolls_back(
        self, manager: RunManager, tmp_path, monkeypatch
    ):
        """Invalid input must raise from start() (so the route can return 4xx)
        and leave the manager idle, not ``starting`` with a doomed task."""

        def boom(project_root, mode, changed, ref):
            raise RunManagerError("无效的 --ref：bogus")

        monkeypatch.setattr(manager, "_build_kickoff", boom)
        with pytest.raises(RunManagerError, match="bogus"):
            await manager.start(str(tmp_path), "run", True, "bogus")
        assert manager.state == "idle"
        assert manager.run_id == ""

    async def test_start_calls_kickoff_once_before_spawning_task(
        self, manager: RunManager, tmp_path, monkeypatch
    ):
        calls: list[str] = []

        def spy_kickoff(project_root, mode, changed, ref):
            calls.append(mode)
            return "kickoff-prompt", 3

        monkeypatch.setattr(manager, "_build_kickoff", spy_kickoff)
        # Stub out the background loop so no engine is launched.
        async def fake_loop(project_root, mode, changed, ref, run_id):
            calls.append("loop")

        monkeypatch.setattr(manager, "_run_loop", fake_loop)
        run_id = await manager.start(str(tmp_path), "review", False, "HEAD")
        # The kickoff was built synchronously (fail-fast validation)…
        assert calls[:1] == ["review"]
        assert run_id == manager.run_id
        # …and the background loop was actually scheduled.
        for _ in range(20):
            if calls == ["review", "loop"]:
                break
            await asyncio.sleep(0.01)
        assert calls == ["review", "loop"]

    def test_start_bad_ref_returns_400_not_200(self, client: TestClient, tmp_path, monkeypatch):
        """The /chat/start route must surface kickoff validation as 400 —
        previously the error was raised inside the background task, so the
        request returned 200 with a doomed run."""

        def boom(project_root, mode, changed, ref):
            raise RunManagerError("无效的 --ref：bogus")

        monkeypatch.setattr(run_manager_module.run_manager, "_build_kickoff", boom)
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run", "changed": True, "ref": "bogus"},
        )
        assert body.status_code == 400
        assert "bogus" in body.json()["detail"]

    def test_start_clean_worktree_returns_400(self, client: TestClient, tmp_path, monkeypatch):
        def boom(project_root, mode, changed, ref):
            raise RunManagerError("相对 HEAD 没有变更文件（工作区干净）")

        monkeypatch.setattr(run_manager_module.run_manager, "_build_kickoff", boom)
        body = client.post(
            "/api/v1/chat/start",
            params={"project_root": str(tmp_path)},
            json={"mode": "run", "changed": True, "ref": "HEAD"},
        )
        assert body.status_code == 400
        assert "干净" in body.json()["detail"]


# ---------------------------------------------------------------------------
# Defect 3: old task's finally clearing another run's _stopping flag
# ---------------------------------------------------------------------------


class TestStoppingFlagOwnership:
    async def test_flag_not_cleared_by_other_run(self, manager: RunManager):
        manager._stopping = True
        manager._stopping_by = "old-run"
        await manager._clear_stopping_if_owned("new-run")
        assert manager._stopping is True  # new run's stop request survives

    async def test_flag_cleared_by_owning_run(self, manager: RunManager):
        manager._stopping = True
        manager._stopping_by = "run-1"
        await manager._clear_stopping_if_owned("run-1")
        assert manager._stopping is False
        assert manager._stopping_by == ""

    async def test_flag_cleared_when_not_set(self, manager: RunManager):
        await manager._clear_stopping_if_owned("run-1")
        assert manager._stopping is False
        assert manager._stopping_by == ""

    async def test_ask_user_select_consumes_flag_and_owner(self, manager: RunManager):
        manager._stopping = True
        manager._stopping_by = "run-1"
        # _ask_user_select() with _stopping set returns "stop" immediately.
        result = await manager._ask_user_select("menu", [{"value": "stop", "label": "停止"}])
        assert result == "stop"
        assert manager._stopping is False
        assert manager._stopping_by == ""

    def test_reset_clears_flag_and_owner(self):
        mgr = RunManager()
        mgr._stopping = True
        mgr._stopping_by = "run-x"
        mgr._reset("")
        assert mgr._stopping is False
        assert mgr._stopping_by == ""
