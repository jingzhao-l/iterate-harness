"""Atomic transaction buffer for mutating file operations (design §20.3.2).

Code mode wraps every mutating file tool in a tiny transaction: the file's
original bytes are snapshotted *before* the edit, and on a failed post-check
the edit is rolled back by restoring the snapshot. A commit discards the
snapshot once the edit is verified good, so only the edits that actually broke
an invariant are ever reverted (fail-fast, no cross-edit cascade).

The buffer is per-query-run state owned by :class:`DefensiveKernel`; it is
never shared across turns.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import uuid
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

# Unique sentinel that cannot collide with real file content.
_MISSING_SENTINEL = b"\x00__ITERATE_MISSING_" + uuid.UUID(int=0).hex.encode() + b"_\x00"


class FileTransactionBuffer:
    """Snapshot/commit/rollback for a set of tracked files."""

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root).resolve()
        #: Original file bytes keyed by resolved absolute path.
        self._snapshots: dict[Path, bytes] = {}
        #: In-flight mutation count per resolved path. A snapshot is a SHARED
        #: safety net for every mutation pending on that path: while two
        #: parallel edits of the same file are in flight, the snapshot taken
        #: by the first must stay available to the second's rollback — the
        #: old first-commit-wins rule dropped it as soon as the first edit
        #: committed, so the second edit's rollback found nothing and the
        #: atomic-mutation guarantee silently degraded to "partial write
        #: kept".
        self._pending: dict[Path, int] = {}
        #: Last ACCEPTED content per resolved path: updated on every commit and
        #: used as the restore target for a sibling mutation's rollback. Two
        #: writes to the same file in one turn must not let the second one's
        #: rollback undo the first one's *committed* edit (restoring the shared
        #: pre-first-edit snapshot), while the first was already reported as a
        #: success to the model.
        self._committed: dict[Path, bytes] = {}
        #: Guards the snapshot map so two concurrent mutations of the SAME
        #: path (parallel file tools inside one turn, or tool calls running on
        #: a worker thread) cannot interleave a read with a write to
        #: ``_snapshots`` — the first snapshot must win atomically and a
        #: rollback must never observe a half-written map entry.
        self._lock = threading.Lock()

    def snapshot(self, path: str | Path) -> None:
        """Record the original bytes of ``path`` before a mutation.

        Files that do not exist yet (a brand-new file being written) are
        remembered as a "create" so a rollback can remove them again. A path
        that is already snapshotted is left untouched (first snapshot wins),
        but the in-flight counter still increments: only the LAST pending
        mutation releases the snapshot.
        """
        resolved = self._resolve(path)
        with self._lock:
            if resolved not in self._snapshots:
                try:
                    self._snapshots[resolved] = resolved.read_bytes()
                except OSError:
                    # Missing file -> remember as absent so rollback removes it.
                    self._snapshots[resolved] = _MISSING_SENTINEL
            self._pending[resolved] = self._pending.get(resolved, 0) + 1

    def commit(self, path: str | Path) -> None:
        """Accept the edit: mark the current content as the last-good state.

        The snapshot only disappears once the FINAL pending mutation for the
        path commits, so a sibling edit of the same file can still roll back if
        it fails afterwards. Each commit records the file's current bytes as
        the restore target for such a sibling rollback — restoring the shared
        original instead would silently undo an edit that was already reported
        as committed.
        """
        resolved = self._resolve(path)
        with self._lock:
            if resolved not in self._snapshots:
                return
            try:
                self._committed[resolved] = resolved.read_bytes()
            except OSError:
                # The file vanished after the "successful" edit; treat the
                # committed state as absence so a rollback removes it.
                self._committed[resolved] = _MISSING_SENTINEL
            remaining = self._pending.get(resolved, 0) - 1
            if remaining > 0:
                self._pending[resolved] = remaining
                return
            self._pending.pop(resolved, None)
            self._snapshots.pop(resolved, None)
            self._committed.pop(resolved, None)

    def rollback(
        self, paths: str | Path | Iterable[str | Path] | None = None
    ) -> list[Path]:
        """Restore tracked files to their snapshots and clear matching state.

        When ``paths`` is given, only those files are restored; their
        snapshots are dropped while edits for other pending paths stay
        tracked (fail-fast without a cross-edit cascade).  When omitted, every
        tracked file is restored (and the buffer is cleared).

        Returns the list of paths that were actually restored (or removed for
        files that did not exist at snapshot time).

        Each restore is atomic: bytes are written to a sibling temp file and
        swapped in with ``os.replace`` so a crash mid-rollback can never leave
        a half-written file behind.
        """
        restored: list[Path] = []
        if paths is None:
            with self._lock:
                targets = [self._resolve(p) for p in self._snapshots]
        elif isinstance(paths, (str, Path)):
            targets = [self._resolve(paths)]
        else:
            targets = [self._resolve(p) for p in paths]
        for resolved in targets:
            with self._lock:
                original = self._snapshots.get(resolved)
                if original is None:
                    continue
                # Scoped rollback restores the last ACCEPTED state when a
                # sibling mutation on this path already committed; the shared
                # original snapshot is only the fallback when nothing on this
                # path was accepted yet (otherwise a failing second edit would
                # silently revert the first, already-successful edit).
                target = original if paths is None else self._committed.get(resolved, original)
            try:
                if target == _MISSING_SENTINEL:
                    if resolved.exists():
                        resolved.unlink()
                        restored.append(resolved)
                else:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp_name = tempfile.mkstemp(
                        dir=resolved.parent,
                        prefix=f".{resolved.name}.",
                        suffix=".rollback",
                    )
                    try:
                        with os.fdopen(fd, "wb") as tmp_file:
                            tmp_file.write(target)
                            tmp_file.flush()
                            os.fsync(tmp_file.fileno())
                        os.replace(tmp_name, resolved)
                    except BaseException:
                        try:
                            os.unlink(tmp_name)
                        except OSError:
                            pass
                        raise
                    restored.append(resolved)
            except OSError as exc:
                # Keep the snapshot entry on a failed restore so a later
                # rollback call can retry — dropping it here would leave the
                # invariant-violating edit effectively committed-by-default.
                log.warning("defensive rollback failed for %s: %s", resolved, exc)
                continue
            with self._lock:
                if paths is None:
                    # Full rollback: every pending mutation on this path is
                    # terminated by the restore, so drop snapshot and count.
                    self._snapshots.pop(resolved, None)
                    self._pending.pop(resolved, None)
                    self._committed.pop(resolved, None)
                    continue
                # Scoped rollback: one pending mutation ended here; the
                # snapshot stays available for any sibling mutation of the
                # same path that is still in flight.
                remaining = self._pending.get(resolved, 0) - 1
                if remaining > 0:
                    self._pending[resolved] = remaining
                    continue
                self._pending.pop(resolved, None)
                self._snapshots.pop(resolved, None)
                self._committed.pop(resolved, None)
        return restored

    @property
    def pending(self) -> list[Path]:
        """Return the paths currently tracked (edits not yet committed)."""
        with self._lock:
            return list(self._snapshots)

    @property
    def is_empty(self) -> bool:
        """Return True when no edit is pending rollback."""
        with self._lock:
            return not self._snapshots

    def _resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._project_root / candidate
        return candidate.resolve()
