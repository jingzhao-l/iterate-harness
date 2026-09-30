"""Cross-process run lease for one project's iterate loop.

The console (CLI/TUI) and the auxiliary intervention channel (WebUI) are two
front ends onto *one* engine. A run guard that lives only in the WebUI
process's memory cannot see a loop the CLI started, and vice versa: the
operator starts ``ih iterate run`` in the TUI, opens the WebUI to watch, sees
``idle``, and clicks 启动迭代 — which launches a *second* loop in the server
process. Both write ``.iterate/``, both drive ``git``, neither notices. That
is exactly the "the channel becomes a second driver" failure the split is
meant to prevent, so the guard has to be shared state on disk.

Design:

- The lease is a single small JSON file under ``.iterate/`` holding the
  holder's ``pid``, its role (``console`` / ``webui``), the project root, and
  an ISO timestamp.
- A lease is claimed with an atomic ``O_CREAT|O_EXCL`` create and released in
  a ``finally``, so a crash leaves a *stale* lease rather than no lease.
- Staleness is decided by liveness, not by time: if the recorded pid is gone
  (``os.kill(pid, 0)`` / ``psutil``-free POSIX+Windows check) the lease is
  reclaimed. A lease written by a live process on the *same* host is never
  reclaimed, so a hung loop keeps its claim — correct, because a hung loop is
  still writing.
- The lease also survives the process that took it, so a WebUI attached to a
  CLI-driven project can *report* that the console is driving, which is the
  information the dashboard was missing.
"""

from __future__ import annotations

import json
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from iterate_harness.utils.fs import atomic_write_text

#: Lease file name inside ``.iterate/``.
LEASE_FILENAME = "run-lease.json"

#: Roles that may take the lease.
ROLE_CONSOLE = "console"
ROLE_WEBUI = "webui"

#: A lease whose holder is on this host but whose pid is gone is reclaimed
#: immediately. This grace period only covers the tiny window between writing
#: the pid and the child being reaped, plus NFS attribute lag.
_STALE_GRACE_SECONDS = 5.0

#: A lease from another host is reclaimed after this long, since we cannot
#: check its liveness.
_REMOTE_STALE_SECONDS = 6 * 60 * 60.0


@dataclass(frozen=True)
class RunLease:
    """A held (or foreign) run lease for one project."""

    path: Path
    holder: str
    role: str
    pid: int
    host: str
    acquired_at: float

    @property
    def is_ours(self) -> bool:
        """True when this lease names the current process on this host."""
        return self.pid == os.getpid() and self.host == socket.gethostname()

    def describe(self) -> str:
        """One-line human description for error messages and the dashboard."""
        return f"{self.role} 进程 pid={self.pid}@{self.host}"


def lease_path(project_root: str | Path) -> Path:
    """Return the lease file path for ``project_root``."""
    return Path(project_root) / ".iterate" / LEASE_FILENAME


def _pid_is_live(pid: int) -> bool:
    """True when ``pid`` names a live process on this host.

    ``os.kill(pid, 0)`` sends signal 0, which performs the permission and
    existence checks without delivering a signal. ``EPERM`` therefore means
    "alive but not ours", which still counts as live.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Windows raises OSError for some invalid handles; be conservative
        # and treat it as alive rather than stealing a live run's lease.
        return True
    return True


def read_lease(project_root: str | Path) -> RunLease | None:
    """Read the current lease, or ``None`` when absent/unreadable."""
    path = lease_path(project_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, UnicodeDecodeError):
        # A half-written or binary lease must not crash the caller: treat it
        # as absent, which the claimant's O_EXCL create will then overwrite.
        return None
    try:
        payload: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return RunLease(
            path=path,
            holder=str(payload.get("holder") or "unknown"),
            role=str(payload.get("role") or "unknown"),
            pid=int(payload.get("pid") or 0),
            host=str(payload.get("host") or ""),
            acquired_at=float(payload.get("acquired_at") or 0.0),
        )
    except (TypeError, ValueError):
        return None


def active_lease(project_root: str | Path) -> RunLease | None:
    """Return the lease if it is *still held*, else ``None`` (reclaiming it).

    Reclaiming removes the file so the next claim's ``O_EXCL`` create cannot
    collide with a lease nobody is enforcing any more.
    """
    lease = read_lease(project_root)
    if lease is None:
        return None
    now = time.time()
    if lease.host == socket.gethostname():
        # Same host: liveness is decidable, so a dead pid frees the lease
        # immediately. The grace window covers the write/reap race.
        if not _pid_is_live(lease.pid) and now - lease.acquired_at > _STALE_GRACE_SECONDS:
            _discard(lease)
            return None
        return lease
    # Another host: fall back to age, since a pid means nothing here.
    if now - lease.acquired_at > _REMOTE_STALE_SECONDS:
        _discard(lease)
        return None
    return lease


def _discard(lease: RunLease) -> None:
    """Remove a lease file, ignoring a concurrent reclaim."""
    try:
        lease.path.unlink()
    except (FileNotFoundError, OSError):
        pass


class RunLeaseConflictError(RuntimeError):
    """Raised when a second iterate run is attempted on a live project."""

    def __init__(self, lease: RunLease) -> None:
        self.lease = lease
        super().__init__(
            f"该项目的 iterate 循环已在运行（{lease.describe()}）。"
            "辅助通道只用于观察与干预，不能并发启动第二个循环；"
            "请先在原终端停止，或等待其结束。"
        )


@contextmanager
def claim_run_lease(
    project_root: str | Path,
    *,
    role: str = ROLE_WEBUI,
) -> Iterator[RunLease]:
    """Claim the project's run lease for the duration of the context.

    Raises :class:`RunLeaseConflictError` when a live lease is held by
    another process, so the caller can answer 409 rather than silently
    starting a second writer.

    The file is created with ``O_CREAT|O_EXCL`` (atomic) and written with
    ``atomic_write_text``; on release it is removed only if it still names
    *this* process, so a lease that was reclaimed and re-taken elsewhere is
    never deleted out from under its new holder.
    """
    path = lease_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = f"{role}:{os.getpid()}@{socket.gethostname()}"

    existing = active_lease(project_root)
    if existing is not None and not existing.is_ours:
        raise RunLeaseConflictError(existing)

    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Lost a race with a concurrent claimer; re-read and decide.
        existing = active_lease(project_root)
        if existing is not None and not existing.is_ours:
            raise RunLeaseConflictError(existing) from None
        # Stale file we already failed to clear (or our own): take it over.
        _discard(read_lease(project_root) or RunLease(path, "?", "?", 0, "", 0.0))
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            existing = active_lease(project_root)
            if existing is not None and not existing.is_ours:
                raise RunLeaseConflictError(existing) from exc
            raise RunLeaseConflictError(
                RunLease(path, "?", "unknown", 0, socket.gethostname(), time.time())
            ) from exc
    except OSError:
        # An unwritable project dir must not block a run outright: without a
        # lease we lose cross-process protection, but the run still works.
        yield RunLease(path, holder, role, os.getpid(), socket.gethostname(), time.time())
        return

    os.close(fd)
    acquired_at = time.time()
    payload = {
        "holder": holder,
        "role": role,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "acquired_at": acquired_at,
    }
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    acquired_at = time.time()
    lease = RunLease(
        path=path,
        holder=holder,
        role=role,
        pid=os.getpid(),
        host=socket.gethostname(),
        acquired_at=acquired_at,
    )
    try:
        yield lease
    finally:
        current = read_lease(project_root)
        if current is not None and current.pid == lease.pid and current.host == lease.host:
            _discard(current)


def foreign_driver(project_root: str | Path) -> RunLease | None:
    """Return a live lease held by *another* process, if any.

    Used for the "the console is driving" banner: the WebUI must be able to
    say who owns the loop rather than reporting ``idle`` and offering to
    start a competing one.
    """
    lease = active_lease(project_root)
    if lease is None or lease.is_ours:
        return None
    return lease


__all__ = [
    "LEASE_FILENAME",
    "ROLE_CONSOLE",
    "ROLE_WEBUI",
    "RunLease",
    "RunLeaseConflictError",
    "active_lease",
    "claim_run_lease",
    "foreign_driver",
    "lease_path",
    "read_lease",
]
