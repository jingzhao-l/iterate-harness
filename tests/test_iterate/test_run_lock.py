"""Tests for the cross-process run lease (iterate.run_lock).

The lease is the shared guard between the console and the WebUI (auxiliary
intervention channel). These tests cover the atomic claim, the conflict path,
dead-pid reclaim, remote-host fallback, and the "who is driving" read used for
the dashboard banner.
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

import pytest

from iterate_harness.iterate.run_lock import (
    ROLE_CONSOLE,
    ROLE_WEBUI,
    RunLease,
    RunLeaseConflictError,
    active_lease,
    claim_run_lease,
    foreign_driver,
    lease_path,
    read_lease,
)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project root with ``.iterate/`` already present (as a real repo has)."""
    root = tmp_path / "proj"
    (root / ".iterate").mkdir(parents=True)
    return root


def _write_foreign_lease(project: Path, *, pid: int, host: str, acquired_at: float | None = None) -> RunLease:
    path = lease_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "holder": f"console:{pid}@{host}",
        "role": ROLE_CONSOLE,
        "pid": pid,
        "host": host,
        "acquired_at": acquired_at if acquired_at is not None else time.time(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return RunLease(
        path=path,
        holder=payload["holder"],
        role=ROLE_CONSOLE,
        pid=pid,
        host=host,
        acquired_at=payload["acquired_at"],
    )


class TestClaimAndRelease:
    def test_claim_release_leaves_no_lease(self, project: Path) -> None:
        with claim_run_lease(project, role=ROLE_CONSOLE):
            assert read_lease(project) is not None
        assert read_lease(project) is None
        assert not lease_path(project).exists()

    def test_lease_names_holder_and_role(self, project: Path) -> None:
        with claim_run_lease(project, role=ROLE_WEBUI) as lease:
            assert lease.role == ROLE_WEBUI
            assert lease.pid == os.getpid()
            assert lease.host == socket.gethostname()
            assert lease.is_ours
            stored = read_lease(project)
            assert stored is not None and stored.pid == os.getpid()

    def test_release_never_removes_foreign_replacement(self, project: Path) -> None:
        """Exiting must not delete a lease another process took over meanwhile."""
        with claim_run_lease(project, role=ROLE_WEBUI):
            # Simulate: after we crashed logically (or the loop was reclaimed
            # and re-taken by the console), the file names another process.
            _write_foreign_lease(project, pid=424242, host="other-host")
        # The context exit saw a different pid/host and must have kept the file.
        assert read_lease(project) is not None
        stored = read_lease(project)
        assert stored is not None and stored.pid == 424242


class TestConflict:
    def test_live_foreign_lease_conflicts(self, project: Path) -> None:
        _write_foreign_lease(project, pid=424242, host="other-host")
        with pytest.raises(RunLeaseConflictError) as excinfo:
            with claim_run_lease(project, role=ROLE_WEBUI):
                pass  # pragma: no cover
        assert "iterate 循环已在运行" in str(excinfo.value)
        # The foreign lease is left untouched.
        assert read_lease(project) is not None and read_lease(project).pid == 424242

    def test_same_host_live_pid_conflicts(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # A *live* pid on this host (the real python running the test) with a
        # different host string is the strongest "another process drives this"
        # signal we can simulate without threads.
        _write_foreign_lease(project, pid=os.getpid(), host="some-other-host")
        with pytest.raises(RunLeaseConflictError):
            with claim_run_lease(project, role=ROLE_CONSOLE):
                pass  # pragma: no cover

    def test_own_lease_is_reentrant_takeover_not_conflict(self, project: Path) -> None:
        """A lease already naming *this* process is ours: claims take it over."""
        with claim_run_lease(project, role=ROLE_WEBUI):
            with claim_run_lease(project, role=ROLE_WEBUI):
                pass
        assert read_lease(project) is None


class TestStaleness:
    def test_dead_pid_on_same_host_is_reclaimed(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "iterate_harness.iterate.run_lock._pid_is_live",
            lambda pid: False,
        )
        _write_foreign_lease(
            project,
            pid=999999,
            host=socket.gethostname(),
            acquired_at=time.time() - 30,
        )
        assert active_lease(project) is None
        # The stale file is removed so a later O_EXCL claim is clean.
        assert not lease_path(project).exists()
        with claim_run_lease(project, role=ROLE_CONSOLE):
            assert read_lease(project) is not None

    def test_recent_dead_pid_not_reclaimed_during_grace(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "iterate_harness.iterate.run_lock._pid_is_live",
            lambda pid: False,
        )
        _write_foreign_lease(project, pid=999999, host=socket.gethostname(), acquired_at=time.time())
        # Inside the grace window the lease is still treated as held.
        assert active_lease(project) is not None

    def test_foreign_host_expires_by_age(self, project: Path) -> None:
        _write_foreign_lease(
            project,
            pid=111,
            host="remote-box",
            acquired_at=time.time() - 7 * 60 * 60,
        )
        assert active_lease(project) is None

    def test_foreign_host_fresh_lease_is_live(self, project: Path) -> None:
        _write_foreign_lease(project, pid=111, host="remote-box", acquired_at=time.time())
        assert active_lease(project) is not None


class TestForeignDriver:
    def test_foreign_driver_reports_console(self, project: Path) -> None:
        _write_foreign_lease(project, pid=1234, host="another-host")
        driver = foreign_driver(project)
        assert driver is not None
        assert driver.role == ROLE_CONSOLE
        assert driver.pid == 1234
        assert "console" in driver.describe()

    def test_foreign_driver_none_for_own_lease(self, project: Path) -> None:
        with claim_run_lease(project, role=ROLE_WEBUI):
            # Our own held lease is not "driven by another process".
            assert foreign_driver(project) is None

    def test_foreign_driver_none_when_idle(self, project: Path) -> None:
        assert foreign_driver(project) is None


class TestCorruptInput:
    def test_garbage_lease_is_absent(self, project: Path) -> None:
        path = lease_path(project)
        path.write_text("{ not json ", encoding="utf-8")
        assert read_lease(project) is None
        assert active_lease(project) is None

    def test_binary_lease_is_absent(self, project: Path) -> None:
        path = lease_path(project)
        path.write_bytes(b"\x00\x01\xff\xfe")
        assert read_lease(project) is None

    def test_empty_project_has_no_lease(self, tmp_path: Path) -> None:
        assert read_lease(tmp_path / "not-a-project") is None


# ---------------------------------------------------------------------------
# Claim races and half-written payloads (regression)
# ---------------------------------------------------------------------------

_RACE_SCRIPT = """
import sys, time
from pathlib import Path

from iterate_harness.iterate.run_lock import RunLeaseConflictError, claim_run_lease

project, go_file, ready_file, result_file = sys.argv[1:5]
hold = float(sys.argv[5])

Path(ready_file).write_text("ready", encoding="utf-8")
while not Path(go_file).exists():
    time.sleep(0.01)
try:
    with claim_run_lease(project, role="console"):
        Path(result_file).write_text("held", encoding="utf-8")
        time.sleep(hold)
except RunLeaseConflictError:
    Path(result_file).write_text("conflict", encoding="utf-8")
"""


class TestClaimRace:
    def test_two_processes_cannot_hold_the_lease_at_once(self, project: Path) -> None:
        """Two claimants racing on an empty project: exactly one wins.

        The loser must see a *complete* lease (the payload lands atomically
        with the file) and raise a conflict instead of silently discarding
        the winner's not-yet-parsed file and taking over.
        """
        import subprocess
        import sys

        barrier = project / ".iterate"
        go_file = barrier / "go"
        procs = []
        results = []
        for index in range(2):
            ready = barrier / f"ready-{index}"
            result = barrier / f"result-{index}"
            results.append(result)
            procs.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        _RACE_SCRIPT,
                        str(project),
                        str(go_file),
                        str(ready),
                        str(result),
                        "0.5",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )

        deadline = time.time() + 30.0
        while not all((barrier / f"ready-{i}").exists() for i in range(2)):
            assert time.time() < deadline, "race subprocesses never became ready"
            time.sleep(0.02)
        go_file.write_text("go", encoding="utf-8")

        for proc in procs:
            _out, err = proc.communicate(timeout=60)
            assert proc.returncode == 0, err

        assert all(path.exists() for path in results), "a racer wrote no outcome"
        outcomes = sorted(path.read_text(encoding="utf-8") for path in results)
        assert outcomes == ["conflict", "held"], outcomes
        # The winner released on exit; nothing is left behind.
        assert read_lease(project) is None
        assert not lease_path(project).exists()

    def test_in_flight_empty_lease_file_is_not_stolen(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh unparseable lease file belongs to an in-flight claim:
        wait for it, then conflict — never discard it and take over."""
        monkeypatch.setattr(
            "iterate_harness.iterate.run_lock._IN_FLIGHT_WAIT_SECONDS", 0.2
        )
        path = lease_path(project)
        path.write_text("", encoding="utf-8")

        with pytest.raises(RunLeaseConflictError):
            with claim_run_lease(project, role=ROLE_CONSOLE):
                pass  # pragma: no cover

        assert path.exists()
        assert path.read_text(encoding="utf-8") == ""

    def test_stale_unparseable_lease_file_is_reclaimed(self, project: Path) -> None:
        """Garbage old enough to be nobody's in-flight write is reclaimed."""
        path = lease_path(project)
        path.write_text("", encoding="utf-8")
        stale = time.time() - 30
        os.utime(path, (stale, stale))

        with claim_run_lease(project, role=ROLE_CONSOLE) as lease:
            assert lease.pid == os.getpid()
            stored = read_lease(project)
            assert stored is not None
            assert stored.has_holder is True
        assert read_lease(project) is None


class TestReleaseOwnership:
    def test_release_keeps_replacement_with_same_pid_but_other_holder(
        self, project: Path
    ) -> None:
        """Release keys off the holder identity, not just pid+host: a lease
        re-taken by a different holder must survive our exit."""
        path = lease_path(project)
        with claim_run_lease(project, role=ROLE_WEBUI) as lease:
            taken_over = {
                "holder": f"console:{lease.pid}@{lease.host}#taken-over",
                "role": ROLE_CONSOLE,
                "pid": lease.pid,
                "host": lease.host,
                "acquired_at": time.time(),
            }
            path.write_text(json.dumps(taken_over), encoding="utf-8")

        stored = read_lease(project)
        assert stored is not None
        assert stored.holder == taken_over["holder"]

    def test_release_discards_holderless_payload_of_this_process(
        self, project: Path
    ) -> None:
        """A payload without a ``holder`` identity still falls back to
        pid+host so our own file is cleaned up on release."""
        path = lease_path(project)
        with claim_run_lease(project, role=ROLE_CONSOLE):
            payload = json.loads(path.read_text(encoding="utf-8"))
            del payload["holder"]
            path.write_text(json.dumps(payload), encoding="utf-8")

        assert read_lease(project) is None