"""Tests for the cron scheduler daemon."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
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
    is_scheduler_running,
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
    """Return the pid of a child that has already been reaped.

    Read the name honestly: this is a *hint*, not a guarantee. Linux re-issues a number as soon as its
    process is gone, which is precisely the assumption `cron_scheduler` used to make about its own pid
    file — see `TestPidClaimIsVerified`, whose first case is this helper's live foreign twin.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _foreign_live_child() -> tuple[subprocess.Popen[bytes], int]:
    """Start a throwaway long-lived child that is NOT the scheduler, and return it with its pid.

    "Not the scheduler" is the point. A pid file that names a process like this one is a stale claim, and
    the only correct response to it is to release the file — signalling that process would be a different
    program's death. On a CI runner the number can belong to the process hosting the step, which is how
    `bucket 06`'s legs stopped reporting without ever finishing.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    # Wait until the kernel exposes the child's own command line, not the parent's
    # pre-exec copy or a momentarily empty /proc entry: `_verifiable_claim_for`
    # records whatever `_pid_cmdline` answers, and a claim recorded before exec
    # cannot verify afterwards. Measured on the 3.11 CI bucket: a claim written
    # with `cmdline: null` made `_register_pid` release the live child's file and
    # claim it for the test process, turning the guard's test red at random.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        line = cron_scheduler._pid_cmdline(proc.pid)
        if line and "time.sleep(60)" in line:
            return proc, proc.pid
        if proc.poll() is not None:
            break
        time.sleep(0.02)
    proc.terminate()
    proc.wait()
    raise AssertionError("the foreign child never exposed a readable command line")


def _write_pid_file(pid: int | str) -> Path:
    """Write the legacy bare-integer pid file: a number, and nothing that identifies its owner."""
    path = get_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")
    return path


def _write_pid_claim(pid: int, cmdline: str | None) -> Path:
    """Write the current claim format, recording the command line the checker will compare against."""
    path = get_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "cmdline": cmdline}) + "\n", encoding="utf-8")
    return path


def _verifiable_claim_for(pid: int) -> Path:
    """Write a claim that *does* verify for *pid*, by recording what the checker itself will read.

    Used by the unit tests whose subject is `start_daemon`'s polling or `_register_pid`'s race, not the
    fingerprint. Those tests need a live, trustworthy claim; the fingerprint's own behaviour is what
    `TestPidClaimIsVerified` measures.
    """
    return _write_pid_claim(pid, cron_scheduler._pid_cmdline(pid))


def _real_scheduler_daemon(timeout: float = 20.0) -> tuple[subprocess.Popen[bytes], int]:
    """Spawn the daemon the way `start_daemon` does, in this test's data dir, and wait for its claim.

    The child gets `ITERATE_DATA_DIR`/`ITERATE_LOGS_DIR` in its own environment because the autouse
    fixture's monkeypatching is in-process only: without those two variables the spawned daemon would
    claim in the real user data directory, which is a production write, not a test.
    """
    code = cron_scheduler._DAEMON_ENTRY_CODE
    env = {
        **os.environ,
        "ITERATE_DATA_DIR": str(cron_scheduler.get_data_dir()),
        "ITERATE_LOGS_DIR": str(cron_scheduler.get_logs_dir()),
    }
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL, env=env)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if read_pid() == proc.pid:
            return proc, proc.pid
        if proc.poll() is not None:
            log = cron_scheduler.get_logs_dir() / "cron_scheduler.log"
            tail = log.read_text(encoding="utf-8", errors="replace")[-500:] if log.exists() else ""
            raise AssertionError(
                f"the spawned daemon exited with {proc.returncode} before claiming the pid file: {tail}"
            )
        time.sleep(0.05)
    proc.terminate()
    raise AssertionError(f"the spawned daemon did not claim the pid file within {timeout:g}s")


def _stop_daemon_child(proc: subprocess.Popen[bytes]) -> None:
    """Ensure the spawned child is gone, whatever the assertion in the body did."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _claimed_pid(path: Path) -> int | None:
    """The pid a pid file claims, reading the current JSON claim or the legacy bare integer.

    Both formats exist in the wild: everything before the identity check wrote a number, and a CLI that
    has been upgraded has to be able to stop a daemon that an older one started. A test that only read one
    of the two would be blind to exactly that.
    """
    raw = path.read_text(encoding="utf-8").strip()
    try:
        claim = json.loads(raw)
    except json.JSONDecodeError:
        return int(raw)
    if isinstance(claim, dict):
        return int(claim["pid"])
    return None




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
            # `run` and `SubprocessError` are part of what this module actually uses: the identity probes
            # call `subprocess.run` to ask `ps` a question. A stand-in that cannot answer takes the probe's
            # "cannot tell" path, and then the test measures the degradation instead of the guard.
            run=subprocess.run,
            DEVNULL=subprocess.DEVNULL,
            STDOUT=subprocess.STDOUT,
            TimeoutExpired=subprocess.TimeoutExpired,
            SubprocessError=subprocess.SubprocessError,
        ),
    )
    return captured


class TestRegisterPid:
    def test_claims_pid_file_left_by_a_dead_scheduler(self) -> None:
        """A stale pid file must not block startup, and must be overwritten."""
        path = _write_pid_file(_dead_pid())

        assert _register_pid() is True
        assert _claimed_pid(path) == os.getpid()

    def test_refuses_pid_file_held_by_a_live_scheduler(self) -> None:
        """A live process whose claim still verifies holds the file, and the loser leaves it alone."""
        proc, pid = _foreign_live_child()
        try:
            path = _verifiable_claim_for(pid)

            assert _register_pid() is False
            # Losing the race must not clobber the winner's pid file.
            assert _claimed_pid(path) == pid
        finally:
            proc.terminate()
            proc.wait()

    def test_reclaims_unparseable_pid_file(self) -> None:
        path = _write_pid_file("not-a-pid")

        assert _register_pid() is True
        assert _claimed_pid(path) == os.getpid()

    def test_keeps_a_pid_file_already_written_by_this_process(self) -> None:
        path = _write_pid_file(os.getpid())

        assert _register_pid() is True
        assert _claimed_pid(path) == os.getpid()


class TestSchedulerLoopPidFile:
    @pytest.mark.asyncio
    async def test_loop_leaves_a_foreign_pid_file_untouched(self) -> None:
        """The early 'already running' return must not remove the winner's
        pid file (the pre-fix ``finally: remove_pid()`` clobbered it)."""
        proc, pid = _foreign_live_child()
        try:
            path = _verifiable_claim_for(pid)

            await run_scheduler_loop(once=True)

            assert path.exists()
            assert _claimed_pid(path) == pid
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
        # read_pid() now insists on two things before it answers: the process is live, and its command
        # line still matches the one recorded when the claim was written. The claimed pid therefore has to
        # be a live process whose claim verifies — this one — rather than any number that survives
        # `os.kill(pid, 0)`.
        pid = os.getpid()

        def _spawn(_cmd: list[str]) -> _FakeProcess:
            # Simulate the child running _run_daemon and claiming the file.
            _verifiable_claim_for(pid)
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
        proc, pid = _foreign_live_child()
        try:
            _verifiable_claim_for(pid)

            def _spawn(_cmd: list[str]) -> _FakeProcess:  # pragma: no cover
                raise AssertionError("Popen must not be called when a scheduler is running")

            _patch_popen(monkeypatch, _spawn)

            with pytest.raises(RuntimeError, match="Scheduler already running"):
                start_daemon()
        finally:
            proc.terminate()
            proc.wait()

    def test_a_stale_claim_does_not_block_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The other side of the same rule: a number that no longer names its claimant is not a scheduler.

        Without the identity check this case is the dangerous one in reverse — a live stranger in the file
        made `ih cron start` refuse while `ih cron stop` armed a signal against it.
        """
        proc, pid = _foreign_live_child()
        try:
            _write_pid_file(pid)

            def _spawn(_cmd: list[str]) -> _FakeProcess:
                _verifiable_claim_for(os.getpid())
                return _FakeProcess(os.getpid())

            captured = _patch_popen(monkeypatch, _spawn)

            assert start_daemon() == os.getpid()
            assert len(captured) == 1, "a stale claim must not keep the daemon from starting"
        finally:
            _stop_daemon_child(proc)

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
            _verifiable_claim_for(os.getpid())
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
            _verifiable_claim_for(pid)
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
        # The identity check asks what lives at the number in the pid file, so this answers it instead of
        # letting the case depend on whether some unrelated process happens to own 4242 today. A test whose
        # result moves with the machine's process table is not a test.
        monkeypatch.setattr(
            cron_scheduler,
            "_pid_cmdline",
            lambda _pid: f"/usr/bin/python3 -c {cron_scheduler._DAEMON_ENTRY_CODE}",
        )
        monkeypatch.setattr(cron_scheduler, "_pid_state", lambda _pid: "S")
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


class TestPidClaimIsVerified:
    """The pid file names a number; a signal may only go to the process that wrote it.

    This class exists because the code before the identity check treated the two as the same thing and
    could therefore SIGTERM a stranger, and because the CI failure it produced was a runner killed from
    inside its own test suite: in the `suite-bisect` run 40249882415, 27 of the 32 solo legs ended green
    and the one that never finished was this module. Each case either proves a refusal happened *and the
    process behind it is still alive*, or proves a real spawned daemon is still stoppable — a guard that
    only ever refuses is a disabled feature, not a fix.
    """

    def test_a_legacy_claim_naming_a_live_stranger_is_released_not_signalled(self) -> None:
        proc, pid = _foreign_live_child()
        try:
            path = _write_pid_file(pid)  # bare integer: a claim with nothing that identifies its owner

            assert read_pid() is None
            assert stop_scheduler() is False
            # The line that carries the fix. Without the identity check this child has already been
            # SIGTERMed by the time stop_scheduler returns, and this reads -15.
            assert proc.poll() is None, "stop_scheduler signalled a process that is not the daemon"
            assert not path.exists(), "the stranger's stale claim was left in place"
        finally:
            _stop_daemon_child(proc)

    def test_a_claim_whose_recorded_cmdline_does_not_match_is_rejected(self) -> None:
        proc, pid = _foreign_live_child()
        try:
            path = _write_pid_claim(pid, "/opt/someother/daemon --serve")

            assert read_pid() is None
            assert stop_scheduler() is False
            assert proc.poll() is None
            assert not path.exists()
        finally:
            _stop_daemon_child(proc)

    def test_a_recorded_path_that_differs_only_in_argv0_still_verifies(self) -> None:
        """Compare the argv tail, because argv[0] is not a stable identity.

        Measured here: a child spawned as ``.venv/bin/python -c …`` answers ``ps`` with that path at first
        and with the framework interpreter's real path a moment later — the interpreter rewrites argv[0]
        after exec. A whole-line comparison refuses claims from processes that never changed, which is how
        the first version of this guard took seven previously green tests down. Both halves are pinned: the
        same tail under a rewritten argv[0] verifies, a different tail does not.
        """
        proc, pid = _foreign_live_child()
        try:
            live = cron_scheduler._pid_cmdline(pid)
            assert live is not None, "this platform has to expose a command line for the check to mean anything"
            head, _, tail = live.partition(" ")
            assert tail, f"the child's command line carries no argv tail to compare ({live!r})"

            _write_pid_claim(pid, f"/an/interpreter/rewritten/by/the/platform {tail}")
            assert read_pid() == pid, "a rewritten argv[0] must not invalidate a live claim"

            _write_pid_claim(pid, f"{head} --some-other-payload entirely")
            assert read_pid() is None, "a different argv tail is not the same process"
            assert proc.poll() is None, "and the process behind the rejected claim is untouched"
        finally:
            _stop_daemon_child(proc)

    def test_the_daemon_that_wrote_the_claim_is_stopped_and_reported_as_stopped(self) -> None:
        """The other side of the guard: a real daemon must stop, and stop reporting itself as un-killable.

        ``stop_scheduler`` used to answer False for a daemon that had in fact exited, because its
        confirmation probe was ``os.kill(pid, 0)`` and an unreaped child keeps answering that. Measured
        before the fix: 13.5 s of polling, ``False``, and an exit status of 0.
        """
        proc, pid = _real_scheduler_daemon()
        try:
            assert read_pid() == pid
            assert is_scheduler_running() is True
            # A real daemon's claim has to carry the fingerprint, or the format the identity check reads
            # would have no writer: reverting `write_pid` to a bare integer keeps every other case green
            # via the legacy fallback, because the daemon's own argv matches the marker.
            claim = json.loads(get_pid_path().read_text(encoding="utf-8"))
            assert claim["pid"] == pid, claim
            assert cron_scheduler._DAEMON_ENTRY_CODE in (claim["cmdline"] or ""), claim

            assert stop_scheduler() is True

            assert proc.wait(timeout=20) is not None
            assert read_pid() is None
            assert not get_pid_path().exists()
        finally:
            _stop_daemon_child(proc)

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows has no zombie state to observe")
    def test_an_exited_child_is_not_reported_alive_before_it_is_reaped(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
        pid = proc.pid
        try:
            deadline = time.monotonic() + 5.0
            state = cron_scheduler._pid_state(pid)
            while time.monotonic() < deadline and state not in ("Z", None):
                time.sleep(0.02)
                state = cron_scheduler._pid_state(pid)
            assert state == "Z", (
                "the premise of this case did not hold: the exited child reported state "
                f"{state!r} instead of staying a zombie until it is reaped"
            )
            # Signal 0 answers for a zombie, which is what made the old probe call this process alive.
            assert os.kill(pid, 0) is None
            assert cron_scheduler.process_is_live(pid) is False
        finally:
            proc.wait(timeout=10)


class TestKillProcessGroupOwnership:
    """``killpg`` may only ever aim at a group this job created."""

    @staticmethod
    def _recording_os(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(
            cron_scheduler,
            "os",
            SimpleNamespace(
                getpgid=os.getpgid,
                killpg=lambda pgid, sig: sent.append((pgid, sig)),
                kill=os.kill,
                getpid=os.getpid,
            ),
        )
        return sent

    def test_a_group_this_job_leads_is_signalled_as_a_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
        )
        try:
            sent = self._recording_os(monkeypatch)
            cron_scheduler._kill_process_group(SimpleNamespace(pid=child.pid, kill=Mock()))
            assert sent == [(child.pid, signal.SIGKILL)], (
                "a child spawned with start_new_session must be killed as the group it leads"
            )
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_a_child_that_shares_the_callers_group_is_never_group_signalled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dangerous shape: ``getpgid(pid)`` answers with the *caller's* group, and killpg empties it."""
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        killed = Mock()
        try:
            sent = self._recording_os(monkeypatch)
            cron_scheduler._kill_process_group(SimpleNamespace(pid=child.pid, kill=killed))
            assert sent == [], "killpg was aimed at a group this job did not create"
            assert killed.called is True
            assert child.poll() is None, "the direct-child fallback must not have killed it either"
        finally:
            _stop_daemon_child(child)

    def test_a_stub_without_a_usable_pid_reaches_neither_the_group_nor_the_kernel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``test_timeout_job`` drives this path with a mock process; a mock pid must not become a signal.

        A MagicMock answers ``__index__`` with 1, so the pre-fix code reached ``os.getpgid(1)`` — the
        machine's own group, with the runner's step host inside it.
        """
        killed = Mock()
        sent = self._recording_os(monkeypatch)
        cron_scheduler._kill_process_group(SimpleNamespace(pid=None, kill=killed))
        assert sent == []
        cron_scheduler._kill_process_group(SimpleNamespace(pid=Mock(__index__=lambda: 1), kill=killed))
        assert sent == []
        assert killed.call_count == 2

