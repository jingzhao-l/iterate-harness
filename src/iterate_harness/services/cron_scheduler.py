"""Background cron scheduler daemon.

Runs as a standalone process (``ih cron start``) or can be embedded via
:func:`run_scheduler_loop`.  Every tick it reads the cron registry, checks
which enabled jobs are due, executes them, and records results in a history
log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iterate_harness.config.paths import get_data_dir, get_logs_dir
from iterate_harness.services.cron import (
    load_cron_jobs,
    mark_job_run,
    validate_cron_expression,
)
from iterate_harness.sandbox import SandboxUnavailableError
from iterate_harness.utils.file_lock import SwarmLockUnavailableError, exclusive_file_lock
from iterate_harness.utils.fs import atomic_write_text
from iterate_harness.utils.shell import create_shell_subprocess

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 30
"""How often the scheduler checks for due jobs."""

DEFAULT_JOB_TIMEOUT_SECONDS = 300
"""Fallback job timeout when the registry entry carries no ``timeout``."""

MAX_JOB_TIMEOUT_SECONDS = 7200
"""Upper bound for a per-job ``timeout`` override (guards typos)."""

MAX_HISTORY_ENTRIES = 5000
"""Global cap on execution-history lines kept in ``cron_history.jsonl``."""

MAX_HISTORY_BYTES = 5 * 1024 * 1024
"""Approximate size threshold at which ``cron_history.jsonl`` is pruned."""

_START_POLL_INTERVAL_SECONDS = 0.05
"""How often ``start_daemon`` checks that the child claimed the pid file."""

_START_POLL_TIMEOUT_SECONDS = 2.0
"""How long ``start_daemon`` waits before declaring the child failed to start."""

_STOP_KILL_POLL_ATTEMPTS = 10
"""Confirmation polls after SIGKILL before the pid file is removed."""

_STOP_KILL_POLL_INTERVAL_SECONDS = 0.1
"""Delay between the post-SIGKILL confirmation polls."""

_DAEMON_ENTRY_CODE = (
    "from iterate_harness.services.cron_scheduler import _run_daemon; _run_daemon()"
)
"""The argv every ``start_daemon`` child is started with.

Two things read this string: ``start_daemon`` passes it to the interpreter, and
:func:`claim_matches_process` falls back to it for the bare-integer pid files that every version before
this one wrote — those files carry a number but no recorded command line, so the standard spawn shape is
the only identity available to them. Keeping one copy is what stops the spawn and the check from drifting
apart.
"""


def _job_timeout(job: dict[str, Any]) -> int:
    """Resolve the job's timeout (clamped to ``[1, MAX]``; bad values fall
    back to the default)."""
    try:
        value = int(job.get("timeout", DEFAULT_JOB_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_JOB_TIMEOUT_SECONDS
    return max(1, min(value, MAX_JOB_TIMEOUT_SECONDS))


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def get_history_path() -> Path:
    """Return the path to the cron execution history file."""
    return get_data_dir() / "cron_history.jsonl"


def append_history(entry: dict[str, Any]) -> None:
    """Append one execution record to the history log."""
    path = get_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    _prune_history_if_needed(path)


def _prune_history_if_needed(path: Path) -> None:
    """Truncate ``cron_history.jsonl`` when it grows beyond the entry/byte cap.

    Pruning is done under an exclusive file lock so concurrent daemon restarts
    or overlapping scheduler processes do not corrupt the file.  The lock is
    acquired only when the file exists and exceeds the byte threshold — no I/O
    on the common append path.
    """
    try:
        if not path.exists() or path.stat().st_size < MAX_HISTORY_BYTES:
            return
    except OSError:
        return
    with exclusive_file_lock(path.parent / ".history_prune.lock"):
        # Re-check under the lock — another process may have already pruned.
        try:
            if not path.exists() or path.stat().st_size < MAX_HISTORY_BYTES:
                return
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        tail = [line for line in lines if line.strip()][-MAX_HISTORY_ENTRIES:]
        atomic_write_text(path, "\n".join(tail) + "\n")
        logger.info("Pruned cron_history.jsonl to %d entries", len(tail))


def load_history(*, limit: int = 50, job_name: str | None = None) -> list[dict[str, Any]]:
    """Load the most recent execution history entries."""
    path = get_history_path()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if job_name and entry.get("name") != job_name:
            continue
        entries.append(entry)
    return entries[-limit:]


# ---------------------------------------------------------------------------
# PID file helpers
# ---------------------------------------------------------------------------

def get_pid_path() -> Path:
    """Return the scheduler PID file path."""
    return get_data_dir() / "cron_scheduler.pid"


def _pid_state(pid: int) -> str | None:
    """The kernel's one-letter process state for *pid*, or None when it cannot be read.

    Linux answers from ``/proc/<pid>/stat``; everything else from ``ps -o stat=``. The short-option form
    is deliberate: ``ps --ww`` was measured (run 40199977015, in this repo's own CI) replying
    ``error: unknown gnu long option`` on procps, and an unguarded ``set -e`` step turned ten green
    buckets red over it.

    A probe must not be able to break the thing it is probing, so the fallback is a broad one: this module
    patches ``subprocess`` wholesale in its own tests, and an ``AttributeError`` escaping through
    ``except (OSError, subprocess.SubprocessError)`` was measured here taking seven green tests down with
    it. The answer is "unknown", never an exception.
    """
    stat_file = Path(f"/proc/{pid}/stat")
    if stat_file.exists():
        try:
            raw = stat_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # "<pid> (<comm>) <state> ..." and <comm> may itself contain ")" and spaces.
        tail = raw.rpartition(") ")[2]
        state = tail.split(" ", 1)[0].strip()
        return state or None
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - a failed probe reports "unknown", see the docstring
        return None
    if out.returncode != 0:
        return None
    state = out.stdout.strip()
    return state[:1] or None



def process_is_live(pid: int) -> bool:
    """True only while *pid* names a running process — a zombie is not alive.

    ``os.kill(pid, 0)`` succeeds for a zombie, so a confirmation loop built on that probe alone reports a
    dead process as un-killable. Measured locally before this function existed: ``stop_scheduler`` had
    already killed its target (exit status ``-15``) and still logged ``pid ... survived SIGKILL``,
    because the unreaped child kept answering signal 0. On Linux the state comes from procfs, elsewhere
    from ``ps``; where neither can be read the probe falls back to signal 0 — the old behaviour — and
    says so in the log instead of pretending the check ran.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    state = _pid_state(pid)
    if state is None:
        logger.debug("Cannot read the process state of pid=%d; treating signal 0 as the answer", pid)
        return True
    return "Z" not in state


def _pid_cmdline(pid: int) -> str | None:
    """The command line *pid* was started with, or None when this platform does not expose it.

    Same rule as :func:`_pid_state`: a probe that raises is worse than a probe that answers "unknown", and
    this module's own tests replace ``subprocess`` wholesale.
    """
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.exists():
        try:
            return cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except OSError:
            return None
    try:
        out = subprocess.run(
            ["ps", "-ww", "-o", "args=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - a failed probe reports "unknown", see the docstring
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _collapse(text: str) -> str:
    """One space between tokens, no leading or trailing space: the shape both sides of a comparison
    have to agree on, because procfs separates argv with NULs and ``ps`` prints what the kernel kept."""
    return " ".join(text.split())


def _argv_tail(text: str) -> str:
    """The command line with its first token removed: what survives an interpreter rewriting argv[0].

    Measured here: a child spawned as ``.venv/bin/python -c "import time; time.sleep(60)"`` answers ``ps``
    with that path immediately, and later answers it with
    ``/Library/Frameworks/Python.framework/.../Python -c import time; time.sleep(60)`` — the framework
    interpreter replaces argv[0] with its own real path after startup. A whole-line comparison therefore
    rejects claims from processes that never changed, which is not the thing being guarded against; the
    tail is what both readings agree on. A line with no tail at all (a bare executable, no arguments) has
    nothing stable to compare, and the caller falls back to the whole string.
    """
    return text.split(" ", 1)[1] if " " in text else ""


def claim_matches_process(pid: int, recorded_cmdline: str | None) -> bool:
    """Whether the process behind *pid* is the one that wrote the claim, judged by its command line.

    A pid file is a claim about a number, and Linux re-issues numbers as soon as a process is reaped, so
    the number alone says nothing about who lives there now. The claim therefore records the command line
    the claiming process had, and this function compares it with the command line the kernel reports for
    that number today — as an argv tail, because argv[0] is not stable (see :func:`_argv_tail`).

    Note what this proves and what it does not: it proves *continuity* — the process there now is the
    process that wrote the file. It cannot prove legitimacy, because the only writer of a claim is a
    process claiming the file for itself. Continuity is exactly the question a signal has to answer.

    Before this existed, ``stop_scheduler`` signalled whatever the file named. Measured locally: a live
    sleep child that had never seen this daemon was written into the file, and ``stop_scheduler`` sent it
    SIGTERM and then SIGKILL — it exited with status ``-15``, the log claimed it had "survived SIGKILL",
    and the pid file was still there 13.5 s later. On a CI runner the same call, driven by a test's
    recycled pid (``_dead_pid()`` in ``tests/test_services/test_cron_scheduler.py`` assumes a reaped
    child's number can never be live again), is how the process that hosts the step dies — which is
    exactly the reading ``bucket 06`` produced: no step completion, no log, no artifact, and only
    GitHub's server-side timeout ends the job.

    Two documented degradations, both louder than a silent pass:
      * where no command line can be read (Windows has neither procfs nor ``ps``), the claim is accepted,
        because the alternative is a daemon that can never be stopped from another process;
      * a legacy file that holds only a number has no recorded command line, so it is checked against
        ``_DAEMON_ENTRY_CODE`` — the argv every ``start_daemon`` child is started with.
    """
    line = _pid_cmdline(pid)
    if line is None:
        logger.warning(
            "Cannot read the command line of pid=%d; accepting the pid file's claim unverified", pid
        )
        return True
    if recorded_cmdline:
        recorded_tail = _argv_tail(_collapse(recorded_cmdline))
        live_tail = _argv_tail(line)
        if recorded_tail and live_tail:
            return recorded_tail == live_tail
        return _collapse(recorded_cmdline) in line
    return _DAEMON_ENTRY_CODE in line


def read_pid_claim() -> tuple[int, str | None] | None:
    """Return ``(pid, recorded_cmdline)`` from the pid file, or None if it carries no usable claim.

    Two formats are accepted: the current JSON claim, and the bare-integer file every version before this
    one wrote. A file that is neither is treated the way an unparseable pid always has been — no claim.
    """
    path = get_pid_path()
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        claim = json.loads(raw)
    except json.JSONDecodeError:
        claim = None
    if isinstance(claim, dict):
        try:
            pid = int(claim["pid"])
        except (KeyError, TypeError, ValueError):
            return None
        recorded = claim.get("cmdline")
        return pid, recorded if isinstance(recorded, str) else None
    try:
        return int(raw), None
    except ValueError:
        return None


def read_pid() -> int | None:
    """Read the PID of a running scheduler, or None.

    Two things have to hold before a number counts as "the scheduler is running": the process must be live
    and not a zombie (see :func:`process_is_live`), and it must be the process that wrote the claim (see
    :func:`claim_matches_process`). A file that fails either test is stale and is released, because
    keeping a stranger's claim both blocks ``ih cron start`` and arms ``stop_scheduler`` against it.
    """
    path = get_pid_path()
    claim = read_pid_claim()
    if claim is None:
        return None
    pid, recorded = claim
    if not process_is_live(pid):
        logger.debug("Removed stale scheduler PID file (pid=%d)", pid)
        path.unlink(missing_ok=True)
        return None
    if not claim_matches_process(pid, recorded):
        logger.warning(
            "Scheduler PID file names pid=%d, which is not the process that claimed it; releasing the "
            "claim instead of signalling it",
            pid,
        )
        path.unlink(missing_ok=True)
        return None
    return pid



def write_pid() -> None:
    """Claim the pid file for this process, recording the command line that goes with the number.

    The number alone is not a claim: it can be re-issued to another process after this one exits, and
    anything reading the file later would then be pointing a signal at a stranger (see
    :func:`claim_matches_process`). ``sys.argv`` cannot serve as the fingerprint — for a ``python -c …``
    start it is just ``['-c']`` — so the recorded line comes from the same source the checker reads, which
    is what makes the two sides comparable. Where that source does not exist (Windows) the claim is
    written without a fingerprint and the checker says it could not verify, rather than pretending to.
    """
    path = get_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    claim = {"pid": os.getpid(), "cmdline": _pid_cmdline(os.getpid())}
    atomic_write_text(path, json.dumps(claim) + "\n")


def _claim_pid_file() -> bool:
    """Claim the pid file for this process; False when a live scheduler holds it."""
    existing = read_pid()
    if existing is not None and existing != os.getpid():
        return False
    write_pid()
    return True


def _register_pid() -> bool:
    """Atomically claim the scheduler PID file for this process.

    Two ``ih cron start`` invocations racing each other both spawn a child;
    without this check the loser overwrites the winner's pid file and the
    scheduler becomes unstoppable (``stop_scheduler`` kills a pid that is not
    the daemon, and the real daemon keeps running with no pid file). Returns
    ``False`` when a live scheduler already owns the file — the caller must
    then exit instead of running a second loop.
    """
    pid_path = get_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = pid_path.with_suffix(pid_path.suffix + ".lock")
    try:
        with exclusive_file_lock(lock_path):
            return _claim_pid_file()
    except SwarmLockUnavailableError as exc:
        # Degrade to an unlocked claim rather than refusing to run: this is
        # strictly better than the pre-lock behaviour every platform had.
        logger.error("File locking unavailable (%s); claiming the pid file unlocked", exc)
        return _claim_pid_file()


def remove_pid() -> None:
    """Remove the PID file."""
    get_pid_path().unlink(missing_ok=True)


def is_scheduler_running() -> bool:
    """Return True if a scheduler process is alive."""
    return read_pid() is not None


def stop_scheduler() -> bool:
    """Send SIGTERM to the running scheduler. Returns True if killed.

    Every probe here is :func:`process_is_live`, not ``os.kill(pid, 0)``: the difference is what the
    confirmation loop reports about a daemon that died but has not been reaped yet. And the escalation to
    SIGKILL re-checks identity first, because the pid we signalled can be re-issued while we wait — a
    number that was our daemon for those 12 seconds may belong to an unrelated process by the time the
    second signal would leave.
    """
    pid = read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        remove_pid()
        return False
    # The scheduler's own signal handler cancels in-flight jobs with a 10 s
    # timeout, so we must wait at least that long before SIGKILL to avoid
    # orphaning child processes.
    for _ in range(60):
        if not process_is_live(pid):
            remove_pid()
            return True
        time.sleep(0.2)
    if read_pid() != pid:
        # The daemon is gone and something else answers to this number now — or the claim has stopped
        # verifying for any other reason. Either way SIGKILL would aim at a process this file no longer
        # describes, so release the claim and report the daemon as stopped.
        logger.warning(
            "pid=%d no longer carries this daemon's claim after SIGTERM; releasing the pid file "
            "without escalating",
            pid,
        )
        remove_pid()
        return True
    # Force kill after the scheduler had ample time to shut down cleanly.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        logger.debug("SIGKILL failed for scheduler pid=%d: %s", pid, exc)
    # SIGKILL delivery is asynchronous: confirm the process is really gone
    # before dropping the pid file. Removing it early would make the still-
    # dying daemon unstoppable (a subsequent start would spawn a second loop
    # alongside the one that has not finished tearing down yet).
    for _ in range(_STOP_KILL_POLL_ATTEMPTS):
        if not process_is_live(pid):
            remove_pid()
            return True
        time.sleep(_STOP_KILL_POLL_INTERVAL_SECONDS)
    logger.warning(
        "Scheduler pid=%d survived SIGKILL; leaving the pid file in place", pid
    )
    return False



# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

#: Upper bound on buffered job output. The history entry keeps only the last
#: 2000 characters, so a job that dumps a huge build log must be drained (to
#: avoid back-pressure) but not accumulated: reading the whole thing into
#: memory is how one chatty job OOMs the scheduler.
_MAX_JOB_OUTPUT_BYTES = 256 * 1024


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    """SIGKILL a job's whole process group — but only a group this call created.

    ``execute_job`` spawns with ``start_new_session=True``, which makes the child the group leader, so
    ``getpgid(pid) == pid`` is the proof that the group belongs to this job. Without that proof the
    signal goes to whatever group the number happens to identify: the stub in
    ``tests/test_services/test_cron_scheduler.py::TestExecuteJob`` carries a mock pid, and a mock pid
    reaches ``os.getpgid`` as ``1`` — the machine's own group, with the runner's step host inside it. A
    number is not ownership.

    Anything that is not a usable pid (a stub, a negative value) is refused before it can reach the
    kernel, and the direct child is signalled instead.
    """
    pid = process.pid
    if isinstance(pid, int) and pid > 0:
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
                return
            logger.debug(
                "pid=%d leads group %d, which this job did not create; signalling the child only",
                pid,
                os.getpgid(pid),
            )
        except (ProcessLookupError, PermissionError, OSError):
            pass
    else:
        logger.debug("Job process carries no usable pid (%r); not signalling any group", pid)
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass


async def _read_bounded(
    stream: asyncio.StreamReader | None, cap: int
) -> bytes:
    """Drain ``stream`` to EOF, keeping at most ``cap`` bytes."""
    if stream is None:
        return b""
    buffer = bytearray()
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return bytes(buffer)
        if len(buffer) < cap:
            buffer.extend(chunk[: cap - len(buffer)])


async def execute_job(job: dict[str, Any]) -> dict[str, Any]:
    """Run a single cron job and return a history entry."""
    name = job["name"]
    command = job["command"]
    cwd = Path(job.get("cwd") or ".").expanduser()
    started_at = datetime.now(timezone.utc)

    logger.info("Executing cron job %r: %s", name, command)
    timeout = _job_timeout(job)
    process: asyncio.subprocess.Process | None = None
    stdout = stderr = b""
    stdout_task: asyncio.Task[bytes] | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    try:
        process = await create_shell_subprocess(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group: a timeout or shutdown must kill the job's
            # whole tree (npm/node children, watchers), not just the shell.
            start_new_session=True,
        )
        # Bounded readers instead of ``communicate()``: drain both pipes to
        # avoid back-pressure, keep at most ``_MAX_JOB_OUTPUT_BYTES`` in RAM.
        stdout_task = asyncio.create_task(_read_bounded(process.stdout, _MAX_JOB_OUTPUT_BYTES))
        stderr_task = asyncio.create_task(_read_bounded(process.stderr, _MAX_JOB_OUTPUT_BYTES))
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _kill_process_group(process)
            for reader in (stdout_task, stderr_task):
                if reader is not None and not reader.done():
                    reader.cancel()
            raise
        stdout = await stdout_task
        stderr = await stderr_task
    except asyncio.CancelledError:
        # Scheduler shutdown cancelled this job: never leave its children
        # running unattended (the module docstring promises reaping).
        if process is not None and process.returncode is None:
            _kill_process_group(process)
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        raise
    except asyncio.TimeoutError:
        try:
            if process is not None and process.returncode is None:
                _kill_process_group(process)
                await process.wait()
        except Exception as exc:  # noqa: BLE001 - best-effort process cleanup
            logger.debug("Could not kill timed-out job process %r: %s", name, exc)
        entry = {
            "name": name,
            "command": command,
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": -1,
            "status": "timeout",
            "stdout": "",
            "stderr": f"Job timed out after {timeout}s",
        }
        mark_job_run(name, success=False)
        append_history(entry)
        return entry
    except SandboxUnavailableError as exc:
        entry = {
            "name": name,
            "command": command,
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": -1,
            "status": "error",
            "stdout": "",
            "stderr": str(exc),
        }
        mark_job_run(name, success=False)
        append_history(entry)
        return entry
    except Exception as exc:
        entry = {
            "name": name,
            "command": command,
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": -1,
            "status": "error",
            "stdout": "",
            "stderr": str(exc),
        }
        mark_job_run(name, success=False)
        append_history(entry)
        return entry

    success = process.returncode == 0
    entry = {
        "name": name,
        "command": command,
        "started_at": started_at.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "returncode": process.returncode,
        "status": "success" if success else "failed",
        "stdout": (stdout.decode("utf-8", errors="replace")[-2000:] if stdout else ""),
        "stderr": (stderr.decode("utf-8", errors="replace")[-2000:] if stderr else ""),
    }
    mark_job_run(name, success=success)
    append_history(entry)
    logger.info("Job %r finished: %s (rc=%s)", name, entry["status"], process.returncode)
    return entry


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------

def _jobs_due(jobs: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Return jobs whose next_run is at or before *now*."""
    due: list[dict[str, Any]] = []
    for job in jobs:
        if not job.get("enabled", True):
            continue
        schedule = job.get("schedule", "")
        if not validate_cron_expression(schedule):
            continue
        next_run_str = job.get("next_run")
        if not next_run_str:
            continue
        try:
            next_run = datetime.fromisoformat(next_run_str)
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if next_run <= now:
            due.append(job)
    return due


async def run_scheduler_loop(*, once: bool = False) -> None:
    """Main scheduler loop.  Runs until SIGTERM or *once* is True (test mode).

    In-flight jobs are cancelled on shutdown so child processes are not left
    orphaned by a force-kill.  A bounded wait (``_SHUTDOWN_TIMEOUT_SECONDS``)
    gives the loop a chance to tear down cleanly before the outer
    ``stop_scheduler`` SIGKILL arrives.
    """
    _SHUTDOWN_TIMEOUT_SECONDS = 10
    shutdown = asyncio.Event()
    in_flight: set[asyncio.Task[dict[str, Any]]] = set()

    def _on_signal() -> None:
        logger.info("Received shutdown signal")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_signal)

    if not _register_pid():
        logger.error(
            "Another cron scheduler already holds the pid file (pid=%s); exiting",
            read_pid(),
        )
        return
    logger.info("Cron scheduler started (pid=%d, tick=%ds)", os.getpid(), TICK_INTERVAL_SECONDS)

    try:
        while not shutdown.is_set():
            now = datetime.now(timezone.utc)
            jobs = load_cron_jobs()
            due = _jobs_due(jobs, now)

            if due:
                logger.info("Tick: %d job(s) due", len(due))
                # Track in-flight tasks so they can be cancelled on shutdown
                tasks = {asyncio.create_task(execute_job(job)) for job in due}
                in_flight.update(tasks)
                # A plain ``await gather(...)`` is uninterruptible: the tick
                # loop could not observe the shutdown signal until every due
                # job finished, so a SIGTERM during a 20-minute run left the
                # scheduler alive and its child unreaped until the external
                # SIGKILL landed. Race the jobs against the shutdown event and
                # cancel the stragglers the moment it fires.
                gather_task = asyncio.ensure_future(
                    asyncio.gather(*tasks, return_exceptions=True)
                )
                shutdown_task = asyncio.ensure_future(shutdown.wait())
                try:
                    done, _pending = await asyncio.wait(
                        {gather_task, shutdown_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if shutdown_task in done and not gather_task.done():
                        logger.info(
                            "Shutdown requested with %d job(s) still running; cancelling",
                            sum(1 for t in tasks if not t.done()),
                        )
                        for task in tasks:
                            task.cancel()
                    results = await gather_task
                finally:
                    shutdown_task.cancel()
                    in_flight.difference_update(tasks)
                for result in results:
                    if isinstance(result, BaseException):
                        if isinstance(result, asyncio.CancelledError):
                            logger.info("Cron job cancelled during shutdown")
                            continue
                        logger.error("Unexpected error executing cron job: %s", result)

            if once:
                break

            try:
                await asyncio.wait_for(shutdown.wait(), timeout=TICK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        # Cancel any straggling in-flight tasks so child processes are reaped
        # rather than left orphaned.
        if in_flight:
            logger.info("Cancelling %d in-flight job(s)", len(in_flight))
            for task in in_flight:
                task.cancel()
            await asyncio.wait_for(
                asyncio.gather(*in_flight, return_exceptions=True),
                timeout=_SHUTDOWN_TIMEOUT_SECONDS,
            )
        remove_pid()
        logger.info("Cron scheduler stopped")


# ---------------------------------------------------------------------------
# Daemon entry point (spawned by ``ih cron start``)
# ---------------------------------------------------------------------------

def _run_daemon() -> None:
    """Entry point for the scheduler subprocess."""
    log_file = get_logs_dir() / "cron_scheduler.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(run_scheduler_loop())


def _terminate_child(proc: subprocess.Popen[Any]) -> None:
    """Best-effort termination of a child that failed to become the daemon."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    except OSError as exc:  # already gone / not our child any more
        logger.debug("Could not terminate scheduler child pid=%s: %s", getattr(proc, "pid", "?"), exc)


def start_daemon() -> int:
    """Start the scheduler daemon (cross-platform, subprocess-based).

    Replaces the old ``os.fork()``+``os.setsid()`` pattern which is
    Unix-only.  Spawns ``_run_daemon`` in a fully detached subprocess,
    redirects its output to the scheduler log, and waits until the child has
    actually claimed the pid file — the old fixed ``sleep(0.3)`` both raced
    (returning before the pid file existed, so ``ih cron status`` reported
    "not running" right after a successful start) and hid spawn failures.
    """
    existing = read_pid()
    if existing is not None:
        raise RuntimeError(f"Scheduler already running (pid={existing})")

    log_path = get_logs_dir() / "cron_scheduler.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    code = _DAEMON_ENTRY_CODE

    with log_path.open("ab") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )

    deadline = time.monotonic() + _START_POLL_TIMEOUT_SECONDS
    while True:
        pid = read_pid()
        if pid == proc.pid:
            return proc.pid
        if pid is not None:
            _terminate_child(proc)
            raise RuntimeError(f"Scheduler already running (pid={pid})")
        if time.monotonic() >= deadline:
            _terminate_child(proc)
            raise RuntimeError(
                f"Scheduler daemon did not start: no pid file after "
                f"{_START_POLL_TIMEOUT_SECONDS:g}s (see {log_path})"
            )
        time.sleep(_START_POLL_INTERVAL_SECONDS)


def scheduler_status() -> dict[str, Any]:
    """Return a status dict about the scheduler."""
    pid = read_pid()
    log_path = get_logs_dir() / "cron_scheduler.log"
    jobs = load_cron_jobs()
    enabled = [j for j in jobs if j.get("enabled", True)]
    return {
        "running": pid is not None,
        "pid": pid,
        "total_jobs": len(jobs),
        "enabled_jobs": len(enabled),
        "log_file": str(log_path),
        "history_file": str(get_history_path()),
    }
