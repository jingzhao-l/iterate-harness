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
from pathlib import Path

log = logging.getLogger(__name__)


class FileTransactionBuffer:
    """Snapshot/commit/rollback for a set of tracked files."""

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root).resolve()
        #: Original file bytes keyed by resolved absolute path.
        self._snapshots: dict[Path, bytes] = {}

    def snapshot(self, path: str | Path) -> None:
        """Record the original bytes of ``path`` before a mutation.

        Files that do not exist yet (a brand-new file being written) are
        remembered as a "create" so a rollback can remove them again. A path
        that is already snapshotted is left untouched (first snapshot wins).
        """
        resolved = self._resolve(path)
        if resolved in self._snapshots:
            return
        try:
            self._snapshots[resolved] = resolved.read_bytes()
        except OSError:
            # Missing file → remember as absent so rollback removes it.
            self._snapshots[resolved] = b"\x00__ITERATE_MISSING__\x00"

    def commit(self, path: str | Path) -> None:
        """Accept the edit: drop the snapshot for ``path`` (verified good)."""
        resolved = self._resolve(path)
        self._snapshots.pop(resolved, None)

    def rollback(self) -> list[Path]:
        """Restore every tracked file to its snapshot and clear the buffer.

        Returns the list of paths that were actually restored (or removed for
        files that did not exist at snapshot time).
        """
        restored: list[Path] = []
        for resolved, original in self._snapshots.items():
            try:
                if original == b"\x00__ITERATE_MISSING__\x00":
                    if resolved.exists():
                        resolved.unlink()
                        restored.append(resolved)
                else:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                    resolved.write_bytes(original)
                    restored.append(resolved)
            except OSError as exc:
                log.warning("defensive rollback failed for %s: %s", resolved, exc)
        self._snapshots.clear()
        return restored

    @property
    def pending(self) -> list[Path]:
        """Return the paths currently tracked (edits not yet committed)."""
        return list(self._snapshots)

    @property
    def is_empty(self) -> bool:
        """Return True when no edit is pending rollback."""
        return not self._snapshots

    def _resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._project_root / candidate
        return candidate.resolve()
