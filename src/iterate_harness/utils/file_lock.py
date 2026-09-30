"""Cross-platform exclusive file-lock helpers.

Used to serialise read-modify-write sequences on shared JSON registries
(credentials, settings, cron, memory index, swarm mailbox). Pair with
:func:`iterate_harness.utils.fs.atomic_write_text` to make each critical section
both race-free and crash-safe.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from typing import Iterator

from iterate_harness.platforms import PlatformName, get_platform


class SwarmLockError(RuntimeError):
    """Base error for file-lock failures."""


class SwarmLockUnavailableError(SwarmLockError):
    """Raised when file locking is unavailable on the current platform."""


def sidecar_lock_path(target: str | Path) -> Path:
    """Return a lock path for ``target`` that never pollutes the target's repo.

    A ``<file>.lock`` sibling is the obvious choice, but a write/edit tool
    would then leave an untracked file next to every file it touched — noise
    in ``git status``, and a stray match for any glob over the project.
    Hashing the *resolved absolute* target into the shared data dir keeps the
    same cross-process mutual exclusion (every process derives the identical
    path) while keeping the working tree clean.
    """
    from iterate_harness.config.paths import get_data_dir

    resolved = str(Path(target).expanduser().resolve())
    digest = hashlib.sha256(resolved.encode("utf-8", "surrogateescape")).hexdigest()[:32]
    return get_data_dir() / "locks" / f"{digest}.lock"


@contextmanager
def exclusive_file_lock(
    lock_path: Path,
    *,
    platform_name: PlatformName | None = None,
) -> Iterator[None]:
    """Acquire an exclusive file lock for the duration of the context."""
    resolved_platform = platform_name or get_platform()
    if resolved_platform == "windows":
        with _exclusive_windows_lock(lock_path):
            yield
        return
    if resolved_platform in {"macos", "linux", "wsl"}:
        with _exclusive_posix_lock(lock_path):
            yield
        return
    raise SwarmLockUnavailableError(
        f"file locking is not supported on platform {resolved_platform!r}"
    )


@contextmanager
def _exclusive_posix_lock(lock_path: Path) -> Iterator[None]:
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive_windows_lock(lock_path: Path) -> Iterator[None]:
    import msvcrt

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        # msvcrt.locking requires a byte range to exist and the file be open
        # in binary mode. Lock the first byte for the lifetime of the
        # critical section.
        lock_file.seek(0)
        # Use fstat (the already-open handle) rather than stat() by path so a
        # concurrent delete of the lock file cannot raise mid-critical-section.
        if os.fstat(lock_file.fileno()).st_size == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
