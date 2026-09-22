"""Track spawned bridge sessions for UI and commands."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from iterate_harness.config.paths import get_data_dir
from iterate_harness.bridge.session_runner import SessionHandle, spawn_session

log = logging.getLogger(__name__)

#: Upper bound for a single session's capture file. A long-running bridge
#: session can otherwise produce an unbounded ``.log`` on disk (the read API
#: truncates for display, but the file itself kept growing). When the cap is
#: crossed the capture is trimmed in place to its trailing
#: :data:`BRIDGE_LOG_TAIL_BYTES`.
BRIDGE_LOG_MAX_BYTES = 64 * 1024 * 1024
BRIDGE_LOG_TAIL_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class BridgeSessionRecord:
    """UI-safe bridge session snapshot."""

    session_id: str
    command: str
    cwd: str
    pid: int
    status: str
    started_at: float
    output_path: str


class BridgeSessionManager:
    """Manage bridge-run child sessions and capture their output."""

    def __init__(self, max_completed: int = 20) -> None:
        self._sessions: dict[str, SessionHandle] = {}
        self._commands: dict[str, str] = {}
        self._output_paths: dict[str, Path] = {}
        self._copy_tasks: dict[str, asyncio.Task[None]] = {}
        self._max_completed = max(1, max_completed)
        # Sessions the caller explicitly stopped/killed: their records are
        # released once the copy task winds down, like before.
        self._stopped: set[str] = set()

    async def spawn(self, *, session_id: str, command: str, cwd: str | Path) -> SessionHandle:
        if not session_id or session_id in self._sessions:
            raise ValueError(
                f"Duplicate or empty bridge session_id: {session_id!r} — "
                "generate a unique id (see BridgeSessionManager.spawn)."
            )
        handle = await spawn_session(session_id=session_id, command=command, cwd=cwd)
        self._sessions[session_id] = handle
        self._commands[session_id] = command
        output_dir = get_data_dir() / "bridge"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{session_id}.log"
        output_path.write_text("", encoding="utf-8")
        self._output_paths[session_id] = output_path
        self._copy_tasks[session_id] = asyncio.create_task(self._copy_output(session_id, handle))
        return handle

    def list_sessions(self) -> list[BridgeSessionRecord]:
        items: list[BridgeSessionRecord] = []
        for session_id, handle in self._sessions.items():
            process = handle.process
            if process.returncode is None:
                status = "running"
            elif process.returncode == 0:
                status = "completed"
            else:
                status = "failed"
            items.append(
                BridgeSessionRecord(
                    session_id=session_id,
                    command=self._commands.get(session_id, ""),
                    cwd=str(handle.cwd),
                    pid=process.pid or 0,
                    status=status,
                    started_at=handle.started_at,
                    output_path=str(self._output_paths[session_id]),
                )
            )
        return sorted(items, key=lambda item: item.started_at, reverse=True)

    def read_output(self, session_id: str, *, max_bytes: int = 12000) -> str:
        path = self._output_paths.get(session_id)
        if path is None or not path.exists():
            return ""
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_bytes:
            return content[-max_bytes:]
        return content

    async def stop(self, session_id: str) -> None:
        handle = self._sessions.get(session_id)
        if handle is None:
            raise ValueError(f"Unknown bridge session: {session_id}")
        task = self._copy_tasks.get(session_id)
        await handle.kill()
        # Mark the session as user-stopped so its copy-task finally block
        # evicts the record instead of preserving it among completed sessions.
        self._stopped.add(session_id)
        if task is not None:
            # Cancel so the copy task's finally block runs and drops this
            # session's entries from every manager dict.
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001 - shutdown cleanup
                log.debug("Ignoring error while awaiting cancelled copy task %s: %s", session_id, exc)
        # Idempotent fallback cleanup in case the copy task never started.
        self._stopped.discard(session_id)
        self._evict_session(session_id)

    async def _copy_output(self, session_id: str, handle: SessionHandle) -> None:
        path = self._output_paths[session_id]
        try:
            if handle.process.stdout is not None:
                # Open the output file once for the whole copy rather than on
                # every 4KB chunk; flushes keep read_output() seeing the newest
                # bytes while the process is still running.
                with path.open("ab") as stream:
                    while True:
                        if path.stat().st_size > BRIDGE_LOG_MAX_BYTES:
                            # Trim the capture to its tail in place so a
                            # runaway session cannot balloon disk usage.
                            self._trim_log(path)
                        chunk = await handle.process.stdout.read(4096)
                        if not chunk:
                            break
                        stream.write(chunk)
                        stream.flush()
            await handle.process.wait()
        finally:
            # The copy finished and the process has exited. Explicitly killed
            # sessions are evicted immediately; naturally completed sessions
            # are preserved so the UI can read their output, bounded by the
            # retention cap below.
            self._copy_tasks.pop(session_id, None)
            if session_id in self._stopped:
                self._stopped.discard(session_id)
                self._evict_session(session_id)
            else:
                self._prune_completed()

    @staticmethod
    def _trim_log(path: Path) -> None:
        """Keep only the trailing segment of an oversized capture file."""
        try:
            with path.open("rb+") as stream:
                size = stream.seek(0, 2)
                if size <= BRIDGE_LOG_MAX_BYTES:
                    return
                keep = min(BRIDGE_LOG_TAIL_BYTES, size)
                stream.seek(size - keep)
                tail = stream.read()
                stream.seek(0)
                stream.write(tail)
                stream.truncate()
        except OSError:
            # Never let a disk read/write issue tear down the copy loop.
            return

    def _prune_completed(self) -> None:
        """Drop oldest finished sessions beyond the retention cap.

        A session whose process has not actually exited yet is never pruned.
        At least one completed record is always retained.
        """
        finished = sorted(
            (sid for sid, handle in self._sessions.items() if handle.process.returncode is not None),
            key=lambda sid: self._sessions[sid].started_at,
            reverse=True,
        )
        for session_id in finished[self._max_completed:]:
            self._evict_session(session_id)

    def _evict_session(self, session_id: str) -> None:
        self._copy_tasks.pop(session_id, None)
        self._sessions.pop(session_id, None)
        self._commands.pop(session_id, None)
        self._output_paths.pop(session_id, None)


_DEFAULT_MANAGER: BridgeSessionManager | None = None


def get_bridge_manager() -> BridgeSessionManager:
    """Return the singleton bridge manager."""
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = BridgeSessionManager()
    return _DEFAULT_MANAGER

