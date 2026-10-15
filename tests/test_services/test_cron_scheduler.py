"""Tests for the cron scheduler daemon."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from iterate_harness.services import cron_scheduler
from iterate_harness.services.cron_scheduler import (
    _jobs_due,
    _prune_history_if_needed,
    _register_pid,
    append_history,
    execute_job,
    get_history_path,
    get_pid_path,
    load_history,
    read_pid,
    run_scheduler_loop,
    start_daemon,
    stop_scheduler,
)


@pytest.fixture(autouse=True)
def _tmp_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect data and log directories to temp."""
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir()
    logs_dir.mkdir()
    monkeypatch.setattr("iterate_harness.services.cron_scheduler.get_data_dir", lambda: data_dir)
    monkeypatch.setattr("iterate_harness.services.cron_scheduler.get_logs_dir", lambda: logs_dir)
    # Also redirect the cron registry used by the scheduler
    monkeypatch.setattr(
        "iterate_harness.services.cron.get_cron_registry_path",
        lambda: data_dir / "cron_jobs.json",
    )


class TestHistory:
    def test_empty_history(self) -> None:
        assert load_history() == []

    def test_append_and_load(self) -> None:
        append_history({"name": "j1", "status": "success"})
        append_history({"name": "j2", "status": "failed"})
        entries = load_history()
        assert len(entries) == 2
        assert entries[0]["name"] == "j1"

    def test_filter_by_name(self) -> None:
        append_history({"name": "j1", "status": "success"})
        append_history({"name": "j2", "status": "success"})
        entries = load_history(job_name="j1")
        assert len(entries) == 1
        assert entries[0]["name"] == "j1"

    def test_limit(self) -> None:
        for i in range(10):
            append_history({"name": f"j{i}", "status": "success"})
        entries = load_history(limit=3)
        assert len(entries) == 3
        # Should be the last 3
        assert entries[0]["name"] == "j7"


class TestJobsDue:
    def test_due_job(self) -> None:
        now = datetime.now(timezone.utc)
        past = (now - timedelta(minutes=5)).isoformat()
        jobs = [
            {"name": "j1", "schedule": "* * * * *", "enabled": True, "next_run": past},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 1

    def test_future_job_not_due(self) -> None:
        now = datetime.now(timezone.utc)
        future = (now + timedelta(hours=1)).isoformat()
        jobs = [
            {"name": "j1", "schedule": "* * * * *", "enabled": True, "next_run": future},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 0

    def test_disabled_job_not_due(self) -> None:
        now = datetime.now(timezone.utc)
        past = (now - timedelta(minutes=5)).isoformat()
        jobs = [
            {"name": "j1", "schedule": "* * * * *", "enabled": False, "next_run": past},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 0

    def test_invalid_schedule_skipped(self) -> None:
        now = datetime.now(timezone.utc)
        past = (now - timedelta(minutes=5)).isoformat()
        jobs = [
            {"name": "j1", "schedule": "not valid", "enabled": True, "next_run": past},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 0

    def test_missing_next_run_skipped(self) -> None:
        now = datetime.now(timezone.utc)
        jobs = [
            {"name": "j1", "schedule": "* * * * *", "enabled": True},
        ]
        due = _jobs_due(jobs, now)
        assert len(due) == 0


class TestExecuteJob:
    @pytest.mark.asyncio
    async def test_successful_job(self) -> None:
        job = {"name": "echo-test", "command": "echo hello", "cwd": "/tmp"}
        entry = await execute_job(job)
        assert entry["status"] == "success"
        assert entry["returncode"] == 0
        assert "hello" in entry["stdout"]

    @pytest.mark.asyncio
    async def test_failing_job(self) -> None:
        job = {"name": "fail-test", "command": "exit 1", "cwd": "/tmp"}
        entry = await execute_job(job)
        assert entry["status"] == "failed"
        assert entry["returncode"] == 1

    @pytest.mark.asyncio
    async def test_timeout_job(self) -> None:
        with patch("iterate_harness.services.cron_scheduler.asyncio.wait_for") as mock_wait:
            import asyncio

            mock_wait.side_effect = asyncio.TimeoutError()

            # Need to mock create_subprocess_exec to return a mock process
            mock_process = AsyncMock()
            mock_process.communicate = Mock(return_value=object())
            mock_process.kill = Mock()
            mock_process.wait = AsyncMock()
            with patch(
                "iterate_harness.utils.shell.asyncio.create_subprocess_exec",
                return_value=mock_process,
            ):
                job = {"name": "slow-test", "command": "sleep 999", "cwd": "/tmp"}
                entry = await execute_job(job)
                assert entry["status"] == "timeout"


class TestSchedulerLoop:
    @pytest.mark.asyncio
    async def test_once_mode_with_no_jobs(self) -> None:
        """Scheduler loop in once-mode should complete without error when no jobs exist."""
        await run_scheduler_loop(once=True)

    @pytest.mark.asyncio
    async def test_once_mode_fires_due_job(self) -> None:
        """Scheduler loop should fire a job that is due."""
        from iterate_harness.services.cron import upsert_cron_job

        upsert_cron_job({"name": "test-once", "schedule": "* * * * *", "command": "echo fired"})

        # Force next_run to the past so it's immediately due
        from iterate_harness.services.cron import load_cron_jobs, save_cron_jobs

        jobs = load_cron_jobs()
        now = datetime.now(timezone.utc)
        jobs[0]["next_run"] = (now - timedelta(minutes=1)).isoformat()
        save_cron_jobs(jobs)

        await run_scheduler_loop(once=True)

        entries = load_history(job_name="test-once")
        assert len(entries) == 1


class TestHistoryPruning:
    def test_prune_keeps_entries_below_cap(self, tmp_path: Path) -> None:
        path = get_history_path()
        entries = [f'{{"name": "j", "line": {i}}}' for i in range(100)]
        path.write_text("\n".join(entries) + "\n", encoding="utf-8")
        _prune_history_if_needed(path)
        loaded = load_history(limit=999)
        assert len(loaded) == 100  # below cap — no pruning occurred

    def test_prune_trims_when_file_exceeds_size_cap(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(cron_scheduler, "MAX_HISTORY_BYTES", 1)  # force prune
        monkeypatch.setattr(cron_scheduler, "MAX_HISTORY_ENTRIES", 5)
        path = get_history_path()
        entries = [f'{{"name": "j", "line": {i}}}' for i in range(20)]
        path.write_text("\n".join(entries) + "\n", encoding="utf-8")
        _prune_history_if_needed(path)
        loaded = load_history(limit=999)
        assert len(loaded) == 5
        # Most recent entries are kept
        assert loaded[-1]["line"] == 19
        assert loaded[0]["line"] == 15


def _dead_pid() -> int:
    """Return a pid that is guaranteed to be gone (a reaped child)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _live_pid() -> tuple[subprocess.Popen[bytes], int]:
    """Start a throwaway long-lived child and return it with its pid."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    return proc, proc.pid


def _write_pid_file(pid: int | str) -> Path:
    path = get_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")
    return path


class _FakeProcess:
    """Minimal stand-in for ``subprocess.Popen`` in start_daemon tests."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _patch_popen(monkeypatch: pytest.MonkeyPatch, factory) -> list[dict[str, object]]:
    """Route ``cron_scheduler.subprocess.Popen`` through *factory*."""
    captured: list[dict[str, object]] = []

    def _fake_popen(cmd: list[str], **kwargs: object) -> _FakeProcess:
        captured.append(kwargs)
        proc = factory(cmd)
        assert isinstance(proc, _FakeProcess)
        return proc

    monkeypatch.setattr(
        cron_scheduler,
        "subprocess",
        SimpleNamespace(
            Popen=_fake_popen,
            DEVNULL=subprocess.DEVNULL,
            STDOUT=subprocess.STDOUT,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    return captured


class TestRegisterPid:
    def test_claims_pid_file_left_by_a_dead_scheduler(self) -> None:
        """A stale pid file must not block startup, and must be overwritten."""
        path = _write_pid_file(_dead_pid())

        assert _register_pid() is True
        assert int(path.read_text(encoding="utf-8").strip()) == os.getpid()

    def test_refuses_pid_file_held_by_a_live_scheduler(self) -> None:
        proc, pid = _live_pid()
        try:
            path = _write_pid_file(pid)

            assert _register_pid() is False
            # Losing the race must not clobber the winner's pid file.
            assert int(path.read_text(encoding="utf-8").strip()) == pid
        finally:
            proc.terminate()
            proc.wait()

    def test_reclaims_unparseable_pid_file(self) -> None:
        path = _write_pid_file("not-a-pid")

        assert _register_pid() is True
        assert int(path.read_text(encoding="utf-8").strip()) == os.getpid()

    def test_keeps_a_pid_file_already_written_by_this_process(self) -> None:
        path = _write_pid_file(os.getpid())

        assert _register_pid() is True
        assert int(path.read_text(encoding="utf-8").strip()) == os.getpid()


class TestSchedulerLoopPidFile:
    @pytest.mark.asyncio
    async def test_loop_leaves_a_foreign_pid_file_untouched(self) -> None:
        """The early 'already running' return must not remove the winner's
        pid file (the pre-fix ``finally: remove_pid()`` clobbered it)."""
        proc, pid = _live_pid()
        try:
            path = _write_pid_file(pid)

            await run_scheduler_loop(once=True)

            assert path.exists()
            assert int(path.read_text(encoding="utf-8").strip()) == pid
            assert load_history() == []  # no jobs were executed by the loser
        finally:
            proc.terminate()
            proc.wait()

    @pytest.mark.asyncio
    async def test_loop_releases_the_pid_file_it_claimed(self) -> None:
        path = _write_pid_file(os.getpid())

        await run_scheduler_loop(once=True)

        assert not path.exists()


class TestStartDaemon:
    def test_returns_once_the_child_claims_the_pid_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # read_pid() probes the pid with os.kill(pid, 0), so the claimed pid
        # has to refer to a live process — use our own.
        pid = os.getpid()

        def _spawn(_cmd: list[str]) -> _FakeProcess:
            # Simulate the child running _run_daemon and claiming the file.
            _write_pid_file(pid)
            return _FakeProcess(pid)

        captured = _patch_popen(monkeypatch, _spawn)

        assert start_daemon() == pid

        assert len(captured) == 1
        kwargs = captured[0]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.STDOUT
        stdout = kwargs["stdout"]
        assert str(getattr(stdout, "name", "")).endswith("cron_scheduler.log")

    def test_refuses_to_start_when_a_scheduler_is_already_running(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proc, pid = _live_pid()
        try:
            _write_pid_file(pid)

            def _spawn(_cmd: list[str]) -> _FakeProcess:  # pragma: no cover
                raise AssertionError("Popen must not be called when a scheduler is running")

            _patch_popen(monkeypatch, _spawn)

            with pytest.raises(RuntimeError, match="Scheduler already running"):
                start_daemon()
        finally:
            proc.terminate()
            proc.wait()

    def test_terminates_a_child_that_never_writes_the_pid_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cron_scheduler, "_START_POLL_TIMEOUT_SECONDS", 0.0)
        child = _FakeProcess(9999)
        _patch_popen(monkeypatch, lambda _cmd: child)

        with pytest.raises(RuntimeError, match="did not start"):
            start_daemon()

        assert child.terminated

    def test_terminates_a_child_when_another_pid_appears(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        child = _FakeProcess(9998)

        def _spawn(_cmd: list[str]) -> _FakeProcess:
            # A different live scheduler won the race while we were spawning.
            _write_pid_file(os.getpid())
            return child

        _patch_popen(monkeypatch, _spawn)

        with pytest.raises(RuntimeError, match="Scheduler already running"):
            start_daemon()

        assert child.terminated

    def test_log_output_is_redirected_to_the_scheduler_log(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pid = os.getpid()

        def _spawn(_cmd: list[str]) -> _FakeProcess:
            _write_pid_file(pid)
            return _FakeProcess(pid)

        _patch_popen(monkeypatch, _spawn)
        start_daemon()

        log_path = cron_scheduler.get_logs_dir() / "cron_scheduler.log"
        assert log_path.exists()


class TestStopScheduler:
    @staticmethod
    def _patch_process_table(monkeypatch: pytest.MonkeyPatch, dies_on_sigkill: bool) -> list[int]:
        """Patch ``os``/``time`` so pid 4242 behaves as a real process would.

        ``dies_on_sigkill=False`` models a process that survives SIGKILL.
        Returns the list of signal numbers that were sent.
        """
        state = {"alive": True}
        sent: list[int] = []

        def _kill(pid: int, sig: int) -> None:
            assert pid == 4242
            if sig == 0:
                if not state["alive"]:
                    raise OSError("No such process")
                return
            sent.append(sig)
            if sig == signal.SIGTERM:
                return  # the scheduler ignores SIGTERM until jobs drain
            if dies_on_sigkill:
                state["alive"] = False

        monkeypatch.setattr(
            cron_scheduler,
            "os",
            SimpleNamespace(kill=_kill, getpid=lambda: 4242),
        )
        monkeypatch.setattr(
            cron_scheduler,
            "time",
            SimpleNamespace(sleep=lambda _s: None, monotonic=lambda: 0.0),
        )
        return sent

    def test_removes_the_pid_file_once_the_process_dies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _write_pid_file(4242)
        sent = self._patch_process_table(monkeypatch, dies_on_sigkill=True)

        assert stop_scheduler() is True

        assert not path.exists()
        assert signal.SIGTERM in sent

    def test_keeps_the_pid_file_when_the_process_survives_sigkill(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Dropping the pid file for a still-dying daemon makes it
        unstoppable: the file must stay until the process is really gone."""
        path = _write_pid_file(4242)
        sent = self._patch_process_table(monkeypatch, dies_on_sigkill=False)

        with caplog.at_level("WARNING", logger="iterate_harness.services.cron_scheduler"):
            assert stop_scheduler() is False

        assert path.exists()
        assert int(path.read_text(encoding="utf-8").strip()) == 4242
        assert signal.SIGKILL in sent
        assert any("survived SIGKILL" in record.getMessage() for record in caplog.records)

    def test_returns_false_when_the_process_is_already_gone(self) -> None:
        path = _write_pid_file(_dead_pid())

        assert stop_scheduler() is False
        assert not path.exists()

    def test_returns_false_without_a_pid_file(self) -> None:
        assert read_pid() is None
        assert stop_scheduler() is False
