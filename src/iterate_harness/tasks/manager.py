"""Background task manager."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import replace
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from iterate_harness.config.paths import get_tasks_dir
from iterate_harness.tasks.types import TaskRecord, TaskStatus, TaskType
from iterate_harness.utils.shell import create_shell_subprocess

log = logging.getLogger(__name__)
_TASK_RESTART_NOTICE = "[IterateHarness] Agent task restarted; prior interactive context was not preserved.\n"

#: On-disk cap for one task's output file. Reads consume the *tail* of the
#: file, so a runaway worker is trimmed from the front instead of growing
#: without bound (disk-exhaustion guard). The file oscillates between the cap
#: and 2× the cap between trims.
_OUTPUT_CAP_BYTES = 8 * 1024 * 1024

#: Grace period between the cooperative stop signal and SIGKILL.
_STOP_GRACE_SECONDS = 3.0
#: How long ``stop_task`` waits for the watcher to publish the final record.
_WATCHER_DRAIN_SECONDS = 3.0
#: Upper bound on retained (terminal) task records. Older terminal records are
#: evicted so a long swarm session cannot grow the manager without limit.
_MAX_TERMINAL_RECORDS = 200


def _terminate_signal() -> signal.Signals:
    """Return the cooperative stop signal for this platform."""
    return signal.SIGTERM if hasattr(signal, "SIGTERM") else signal.SIGINT


def _signal_process_tree(
    process: asyncio.subprocess.Process,
    sig: signal.Signals,
    *,
    ignore_missing: bool = False,
) -> None:
    """Signal a task's whole process group, not just the direct child.

    Tasks are spawned with ``start_new_session=True``, so each one leads its
    own process group. Signalling only ``process`` would leave every grandchild
    (a dev server, a watcher, a ``bash -c`` helper) running after the task is
    reported "killed" — unowned code still mutating the repo and burning tokens.
    """
    pid = process.pid
    if pid is None:
        return
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        if ignore_missing:
            return
        # No process group (already reaped, or the platform refused): fall back
        # to the direct child.
        try:
            process.send_signal(sig)
        except (ProcessLookupError, OSError, ValueError) as exc:
            if not ignore_missing:
                log.debug("Could not signal task process %s: %s", pid, exc)


def _trim_output_front(path: Path) -> None:
    """Drop the oldest bytes of ``path`` so it holds at most ``_OUTPUT_CAP_BYTES``."""
    try:
        size = path.stat().st_size
        excess = size - _OUTPUT_CAP_BYTES
        if excess <= 0:
            return
        with path.open("r+b") as handle:
            handle.seek(excess)
            remainder = handle.read()
            handle.seek(0)
            handle.write(remainder)
            handle.truncate()
    except OSError:
        pass  # best-effort trim; the next write will re-attempt


def _encode_task_worker_payload(data: str) -> bytes:
    """Serialize one worker input as a single JSON line.

    Plain-text prompts may contain embedded newlines, so they cannot be written
    directly to a readline()-based worker protocol. We wrap them in a JSON
    object with a ``text`` field, while preserving already-structured payloads
    emitted by teammate backends.
    """

    stripped = data.rstrip("\n")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        framed = stripped
    elif "\n" not in stripped and "\r" not in stripped:
        framed = stripped
    else:
        framed = json.dumps({"text": stripped}, ensure_ascii=False)
    return (framed + "\n").encode("utf-8")

CompletionListener = Callable[[TaskRecord], Awaitable[None] | None]


class BackgroundTaskManager:
    """Manage shell and agent subprocess tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._waiters: dict[str, asyncio.Task[None]] = {}
        self._output_locks: dict[str, asyncio.Lock] = {}
        self._input_locks: dict[str, asyncio.Lock] = {}
        self._generations: dict[str, int] = {}
        self._completion_listeners: dict[str, CompletionListener] = {}
        # In-process tasks have no subprocess; each one carries an async abort
        # callback (the swarm backend's graceful shutdown) so stop_task and
        # write_to_task can route to it instead of a stdin pipe.
        self._in_process_aborts: dict[str, Callable[[], Awaitable[None]]] = {}

    async def create_shell_task(
        self,
        *,
        command: str | None = None,
        description: str,
        cwd: str | Path,
        task_type: TaskType = "local_bash",
        env: dict[str, str] | None = None,
        argv: list[str] | None = None,
    ) -> TaskRecord:
        """Start a background command.

        Either ``command`` (a shell-evaluated string) or ``argv`` (a direct
        argv list) must be supplied. The ``argv`` form bypasses shell
        invocation entirely — it spawns the executable directly via
        ``asyncio.create_subprocess_exec(*argv)`` — which is the right choice
        for teammate spawning on Windows: Git Bash cannot reliably exec
        Windows-pathed binaries (e.g. ``C:\\Users\\...\\python.exe``) when it
        is itself launched via ``create_subprocess_exec`` with that path
        embedded in a ``-lc`` string, even though the same shell call works
        interactively. Bypassing the shell sidesteps that entire class of
        platform-quoting bug.

        ``env`` is merged with ``os.environ`` when the subprocess is launched,
        so callers should pass only the variables they want to add or
        override.
        """
        if command is None and argv is None:
            raise ValueError("create_shell_task requires either command or argv")
        if command is not None and argv is not None:
            raise ValueError("create_shell_task accepts only one of command or argv")
        task_id = _task_id(task_type)
        output_path = get_tasks_dir() / f"{task_id}.log"
        record = TaskRecord(
            id=task_id,
            type=task_type,
            status="running",
            description=description,
            cwd=str(Path(cwd).resolve()),
            output_file=output_path,
            command=command,
            created_at=time.time(),
            started_at=time.time(),
            env=dict(env) if env is not None else None,
            argv=list(argv) if argv is not None else None,
        )
        output_path.write_text("", encoding="utf-8")
        self._tasks[task_id] = record
        self._output_locks[task_id] = asyncio.Lock()
        self._input_locks[task_id] = asyncio.Lock()
        try:
            await self._start_process(task_id)
        except Exception:
            # A spawn failure (bad command, missing interpreter, cwd gone,
            # permission denied) must not leave a permanently "running" ghost
            # record behind: list_tasks would keep showing it and callers
            # would block on a waiter that never resolves. Mark it failed
            # once and reap all per-task state so the manager returns to a
            # clean state.
            self._tasks[task_id].status = "failed"
            self._tasks[task_id].ended_at = time.time()
            self._tasks[task_id].return_code = -1
            self._waiters.pop(task_id, None)
            self._output_locks.pop(task_id, None)
            self._input_locks.pop(task_id, None)
            self._generations.pop(task_id, None)
            raise
        return record

    async def create_agent_task(
        self,
        *,
        prompt: str,
        description: str,
        cwd: str | Path,
        task_type: TaskType = "local_agent",
        model: str | None = None,
        api_key: str | None = None,
        command: str | None = None,
        env: dict[str, str] | None = None,
        argv: list[str] | None = None,
    ) -> TaskRecord:
        """Start a local agent task as a subprocess.

        Prefer ``argv`` (direct exec, no shell) over ``command`` (shell-
        evaluated) for teammate spawn — see :meth:`create_shell_task` for
        the cross-platform reasoning. ``env`` is forwarded to
        :meth:`create_shell_task` and ultimately merged with ``os.environ``
        at process spawn time.
        """
        if command is None and argv is None:
            effective_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not effective_api_key:
                raise ValueError(
                    "Local agent tasks require ANTHROPIC_API_KEY or an explicit command/argv override"
                )
            argv = ["python", "-m", "iterate_harness"]
            if model:
                argv.extend(["--model", model])
            # Pass the key through the (merged) subprocess environment rather
            # than argv: `--api-key <secret>` is visible to every local user
            # via `ps` while it runs.
            env = dict(env or {})
            env["ANTHROPIC_API_KEY"] = effective_api_key

        record = await self.create_shell_task(
            command=command,
            description=description,
            cwd=cwd,
            task_type=task_type,
            env=env,
            argv=argv,
        )
        updated = replace(record, prompt=prompt)
        if task_type != "local_agent":
            updated.metadata["agent_mode"] = task_type
        self._tasks[record.id] = updated
        await self.write_to_task(record.id, prompt)
        return updated

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Return one task record."""
        return self._tasks.get(task_id)

    def list_tasks(self, *, status: TaskStatus | None = None) -> list[TaskRecord]:
        """Return all tasks, optionally filtered by status."""
        tasks = list(self._tasks.values())
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return sorted(tasks, key=lambda item: item.created_at, reverse=True)

    def update_task(
        self,
        task_id: str,
        *,
        description: str | None = None,
        progress: int | None = None,
        status_note: str | None = None,
    ) -> TaskRecord:
        """Update mutable task metadata used for coordination and UI display."""
        task = self._require_task(task_id)
        if description is not None and description.strip():
            task.description = description.strip()
        if progress is not None:
            task.metadata["progress"] = str(progress)
        if status_note is not None:
            note = status_note.strip()
            if note:
                task.metadata["status_note"] = note
            else:
                task.metadata.pop("status_note", None)
        return task

    async def stop_task(self, task_id: str) -> TaskRecord:
        """Terminate a running task (and its whole process group)."""
        task = self._require_task(task_id)
        process = self._processes.get(task_id)
        if process is None:
            if task.status in {"completed", "failed", "killed"}:
                return task
            abort = self._in_process_aborts.get(task_id)
            if abort is not None:
                # In-process teammate: request cancellation through the swarm
                # backend's graceful shutdown, then finalize as killed (same
                # observable result as the subprocess stop semantics).
                try:
                    await abort()
                except Exception:
                    log.exception("In-process abort for task %s failed", task_id)
                if task.status not in {"completed", "failed", "killed"}:
                    task.status = "killed"
                    task.ended_at = time.time()
                self._in_process_aborts.pop(task_id, None)
                await self._notify_completion_listeners(task)
                return task
            raise ValueError(f"Task {task_id} is not running")

        # Mark the record killed BEFORE signalling the process. The watcher task
        # resumes on process exit and publishes the terminal state; if it got
        # there first it would overwrite "killed" with "failed" (a SIGTERM
        # exit code is non-zero) and fire the completion listeners with a
        # misleading failure — which unregisters agent listeners and makes the
        # coordinator wait forever for a task it deliberately stopped.
        task.status = "killed"
        task.ended_at = time.time()

        _signal_process_tree(process, _terminate_signal())
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=_STOP_GRACE_SECONDS)
        except asyncio.TimeoutError:
            _signal_process_tree(process, signal.SIGKILL, ignore_missing=True)
            await process.wait()
        await _close_process_stdin(process)

        # Let the watcher publish the final record (return_code + listeners)
        # before returning, so callers never observe a half-finalized task.
        waiter = self._waiters.get(task_id)
        if waiter is not None and not waiter.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(waiter), timeout=_WATCHER_DRAIN_SECONDS
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                log.debug("Task %s watcher did not settle in time after stop", task_id)
        return self._require_task(task_id)

    async def write_to_task(self, task_id: str, data: str) -> None:
        """Write one line to task stdin, auto-resuming local agents when needed."""
        task = self._require_task(task_id)
        if task.type == "in_process_teammate" and (
            task_id in self._in_process_aborts or task.metadata.get("agent_id")
        ):
            # In-process teammate: there is no stdin pipe. Route the payload to
            # the agent's file-based mailbox, which its query loop drains
            # between turns — the swarm backend's messaging contract.
            await self._write_in_process_mailbox(task, data)
            return
        payload = _encode_task_worker_payload(data)
        # ``setdefault`` (not ``[...]``): a task whose spawn failed keeps its
        # record but has no lock left, and the coordinator's recovery path
        # (send_message right after a failed spawn) must not blow up with a
        # bare KeyError.
        async with self._input_locks.setdefault(task_id, asyncio.Lock()):
            process = await self._ensure_writable_process(task)
            stdin = process.stdin
            if stdin is None:
                raise ValueError(f"Task {task_id} does not accept input") from None
            stdin.write(payload)
            try:
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                if task.type not in {"local_agent", "remote_agent", "in_process_teammate"}:
                    raise ValueError(f"Task {task_id} does not accept input") from None
                process = await self._restart_agent_task(task)
                stdin = process.stdin
                if stdin is None:
                    raise ValueError(f"Task {task_id} does not accept input") from None
                stdin.write(payload)
                await stdin.drain()

    async def _write_in_process_mailbox(self, task: TaskRecord, data: str) -> None:
        """Route an input line to an in-process teammate's file-based mailbox."""
        agent_id = task.metadata.get("agent_id") or ""
        if not agent_id:
            raise ValueError(
                f"Task {task.id} is an in-process teammate without an identity; cannot deliver input"
            )
        from iterate_harness.swarm.mailbox import MailboxMessage, TeammateMailbox

        team = task.metadata.get("team") or (agent_id.split("@", 1)[1] if "@" in agent_id else "")
        message = MailboxMessage(
            id=uuid4().hex,
            type="user_message",
            sender="leader",
            recipient=agent_id,
            payload={"content": data},
            timestamp=time.time(),
        )
        mailbox = TeammateMailbox(team_name=team, agent_id=agent_id)
        await mailbox.write(message)
        log.debug("Routed input to in-process teammate %s via mailbox", agent_id)

    def read_task_output(self, task_id: str, *, max_bytes: int = 12000) -> str:
        """Return the tail of a task's output file (bounded read, never loads
        the whole file for files larger than ``max_bytes``)."""
        task = self._require_task(task_id)
        try:
            size = task.output_file.stat().st_size
        except OSError:
            return ""
        if size == 0:
            return ""
        with task.output_file.open("r", encoding="utf-8", errors="replace") as handle:
            if size > max_bytes:
                handle.seek(-max_bytes, os.SEEK_END)
                # The seek may land mid-UTF-8-sequence; 'replace' handles it.
                return handle.read()
            return handle.read()

    def register_completion_listener(self, listener: CompletionListener) -> Callable[[], None]:
        """Register a callback fired whenever a task reaches a terminal state."""
        listener_id = uuid4().hex
        self._completion_listeners[listener_id] = listener

        def _unregister() -> None:
            self._completion_listeners.pop(listener_id, None)

        return _unregister

    def register_in_process_task(
        self,
        task_id: str,
        *,
        task_type: TaskType,
        description: str,
        cwd: str,
        prompt: str | None = None,
        agent_id: str = "",
        team: str = "",
        abort: Callable[[], Awaitable[None]] | None = None,
    ) -> TaskRecord:
        """Register a task that executes in-process (no subprocess).

        The in-process swarm backend (:class:`~iterate_harness.swarm.in_process.InProcessBackend`)
        runs teammates as asyncio Tasks inside the harness; they never appear
        in ``_processes``/``_waiters``, so without a record here the console's
        task tools could not list, poll, nudge, or stop them. Registering a
        record makes an in-process teammate look exactly like a subprocess
        teammate to the operation surface:

        - ``task_list`` / ``task_get`` see it and watch its status transitions.
        - ``write_to_task`` routes input to its file-based mailbox.
        - ``stop_task`` invokes *abort* (the backend's graceful shutdown) and
          finalizes the record as ``killed``.

        Args:
            task_id: The backend-assigned id (``in_process_<hex>``).
            task_type: Record label (``in_process_teammate`` for swarm spawns).
            description: Short human-readable description.
            cwd: Working directory of the in-process task.
            prompt: The teammate's kickoff prompt (recorded for diagnostics).
            agent_id: Fully-qualified identity (``name@team``) — enables the
                mailbox write path.
            team: Team name (derived from *agent_id* when omitted).
            abort: Async callback that requests cancellation of the task.
        """
        record = TaskRecord(
            id=task_id,
            type=task_type,
            status="running",
            description=description,
            cwd=cwd,
            output_file=get_tasks_dir() / f"{task_id}.log",
            prompt=prompt,
            created_at=time.time(),
        )
        if agent_id:
            record.metadata["agent_id"] = agent_id
        if team:
            record.metadata["team"] = team
        self._tasks[task_id] = record
        if abort is not None:
            self._in_process_aborts[task_id] = abort
        log.debug("Registered in-process task %s (type=%s)", task_id, task_type)
        return record

    async def mark_in_process_terminal(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        return_code: int | None = None,
    ) -> None:
        """Publish the terminal state of an in-process task.

        Mirror of the subprocess watcher's finalization: a deliberately
        ``killed`` status is preserved (a stop must not be overwritten by the
        task's own exit path), the end time is stamped, the abort mapping is
        dropped, and the completion listeners fire so hooks (SUBAGENT_STOP)
        behave identically to subprocess teammates.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return
        if task.status == "killed":
            return
        task.status = status
        task.ended_at = time.time()
        if return_code is not None:
            task.return_code = return_code
        self._in_process_aborts.pop(task_id, None)
        await self._notify_completion_listeners(task)
        self._evict_old_terminal_records()

    async def _watch_process(
        self,
        task_id: str,
        process: asyncio.subprocess.Process,
        generation: int,
    ) -> None:
        reader = asyncio.create_task(self._copy_output(task_id, process))
        return_code = await process.wait()
        try:
            await reader
        except Exception:
            # Output copy failed (e.g. output file deleted mid-run). Never let
            # that strand the task as a zombie "running" record — the exit
            # code still drives the terminal status below.
            log.exception("Output copy failed for task %s; finalizing on exit code", task_id)
        await _close_process_stdin(process)

        current_generation = self._generations.get(task_id)
        if current_generation != generation:
            return

        task = self._tasks[task_id]
        task.return_code = return_code
        if task.status != "killed":
            task.status = "completed" if return_code == 0 else "failed"
        task.ended_at = time.time()
        await self._notify_completion_listeners(task)
        self._processes.pop(task_id, None)
        self._waiters.pop(task_id, None)
        self._evict_old_terminal_records()

    def _evict_old_terminal_records(self) -> None:
        """Drop the oldest terminal records once the cap is exceeded.

        A long swarm session spawns one record per agent; without a bound the
        manager (and ``task_list``) grows without limit. Only *terminal*
        records are evicted, and only after their process is gone, so a live
        task can never be dropped.
        """
        terminal = [
            task
            for task in self._tasks.values()
            if task.status in {"completed", "failed", "killed"} and task.id not in self._processes
        ]
        if len(terminal) <= _MAX_TERMINAL_RECORDS:
            return
        terminal.sort(key=lambda item: item.ended_at or item.created_at)
        for task in terminal[: len(terminal) - _MAX_TERMINAL_RECORDS]:
            self._tasks.pop(task.id, None)
            self._output_locks.pop(task.id, None)
            self._input_locks.pop(task.id, None)

    async def _copy_output(self, task_id: str, process: asyncio.subprocess.Process) -> None:
        if process.stdout is None:
            return
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                return
            async with self._output_locks.setdefault(task_id, asyncio.Lock()):
                path = self._tasks[task_id].output_file
                # Bound the on-disk output under the same lock that guards
                # every append: a runaway worker must never fill the disk and
                # the trim must never race a concurrent writer on another
                # iteration (append rewrites the file in place). Once the file
                # passes the cap, drop the oldest bytes so it hovers around
                # ``_OUTPUT_CAP_BYTES``; ``read_task_output`` reads the *tail*,
                # so the newest diagnostics must survive.
                with path.open("ab") as handle:
                    handle.write(chunk)
                if path.stat().st_size > _OUTPUT_CAP_BYTES * 2:
                    _trim_output_front(path)

    def _require_task(self, task_id: str) -> TaskRecord:
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"No task found with ID: {task_id}")
        return task

    async def _start_process(self, task_id: str) -> asyncio.subprocess.Process:
        task = self._require_task(task_id)
        if task.command is None and task.argv is None:
            raise ValueError(f"Task {task_id} does not have a command or argv to run")

        generation = self._generations.get(task_id, 0) + 1
        self._generations[task_id] = generation
        # Merge task-specific env vars on top of the parent process environment
        # so the child sees both. Passing ``None`` lets the OS inherit env
        # directly, which is the legacy behaviour for plain shell tasks.
        merged_env: dict[str, str] | None
        if task.env:
            merged_env = {**os.environ, **task.env}
        else:
            merged_env = None

        if task.argv is not None:
            # Direct-exec route. No shell. Used for teammate spawn so we
            # don't have to round-trip Windows paths through Git Bash, which
            # cannot reliably exec ``C:\\...\\python.exe`` when launched
            # itself via ``asyncio.create_subprocess_exec`` (see #230).
            process = await asyncio.create_subprocess_exec(
                *task.argv,
                cwd=str(Path(task.cwd).resolve()),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=merged_env,
                # Own process group: a stop must reach every descendant, and a
                # Ctrl-C at the terminal must not race the harness' own
                # teardown into a half-signalled group.
                start_new_session=True,
            )
        else:
            assert task.command is not None
            process = await create_shell_subprocess(
                task.command,
                cwd=task.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=merged_env,
                start_new_session=True,
            )
        self._processes[task_id] = process
        self._waiters[task_id] = asyncio.create_task(
            self._watch_process(task_id, process, generation)
        )
        return process

    async def _ensure_writable_process(
        self,
        task: TaskRecord,
    ) -> asyncio.subprocess.Process:
        process = self._processes.get(task.id)
        if process is not None and process.stdin is not None and process.returncode is None:
            # ``returncode`` is only set after the watcher's ``wait()``
            # resolves, so for a while after the child exits the record still
            # "looks" writable while its stdin pipe is already dead. Writing
            # there succeeds (the OS buffers the line) and the payload is
            # silently lost — no BrokenPipeError, so the restart path below
            # never fires and the follow-up message evaporates. Resolve the
            # exit state synchronously: if the process is really gone the
            # shared ``wait()`` future returns now and we fall through to the
            # restart, giving the follow-up a fresh stdin.
            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), timeout=0)
            except asyncio.TimeoutError:
                # Still genuinely running.
                return process
            # Process exited behind our back; fall through to restart.
            process = self._processes.get(task.id)
        if task.type not in {"local_agent", "remote_agent", "in_process_teammate"}:
            raise ValueError(f"Task {task.id} does not accept input")
        return await self._restart_agent_task(task)

    async def _restart_agent_task(self, task: TaskRecord) -> asyncio.subprocess.Process:
        if task.command is None and task.argv is None:
            raise ValueError(f"Task {task.id} does not have a restart command or argv")

        waiter = self._waiters.get(task.id)
        if waiter is not None and not waiter.done():
            await waiter

        restart_count = int(task.metadata.get("restart_count", "0")) + 1
        task.metadata["restart_count"] = str(restart_count)
        task.metadata["status_note"] = "Task restarted; prior interactive context was not preserved."
        task.status = "running"
        task.started_at = time.time()
        task.ended_at = None
        task.return_code = None
        with task.output_file.open("ab") as handle:
            handle.write(_TASK_RESTART_NOTICE.encode("utf-8"))
        try:
            return await self._start_process(task.id)
        except Exception:
            # Same rule as the initial spawn: never leave a record that says
            # "running" with no process behind it. Such a ghost can never
            # reach a terminal state, so task_stop raises "not running" and
            # every waiter hangs.
            task.status = "failed"
            task.ended_at = time.time()
            task.return_code = -1
            task.metadata["status_note"] = "Restart failed: the task process could not be started."
            self._waiters.pop(task.id, None)
            self._output_locks.pop(task.id, None)
            self._input_locks.pop(task.id, None)
            self._generations.pop(task.id, None)
            self._notify_terminal_sync(task)
            raise

    def _notify_terminal_sync(self, task: TaskRecord) -> None:
        """Fire completion listeners for a task that never got to run.

        Used by the spawn-failure paths, where no watcher exists to do it.
        Listeners are sync/async callables; only the sync ones can run here —
        an async listener is scheduled so the loop can await it later.
        """
        snapshot = replace(task, metadata=dict(task.metadata))
        for listener_id, listener in list(self._completion_listeners.items()):
            try:
                maybe_awaitable = listener(snapshot)
                if maybe_awaitable is not None:
                    asyncio.ensure_future(maybe_awaitable)
            except Exception:
                log.exception("Task completion listener %s failed for task %s", listener_id, task.id)

    async def _notify_completion_listeners(self, task: TaskRecord) -> None:
        snapshot = replace(task, metadata=dict(task.metadata))
        for listener_id, listener in list(self._completion_listeners.items()):
            try:
                maybe_awaitable = listener(snapshot)
                if maybe_awaitable is not None:
                    await maybe_awaitable
            except Exception:
                log.exception("Task completion listener %s failed for task %s", listener_id, task.id)

    def close(self) -> None:
        """Best-effort cleanup for any tracked subprocesses and watcher tasks."""
        for waiter in list(self._waiters.values()):
            waiter.cancel()
        self._waiters.clear()

        for process in list(self._processes.values()):
            stdin = process.stdin
            if stdin is not None and not stdin.is_closing():
                try:
                    stdin.close()
                except RuntimeError as exc:
                    log.debug("Could not close stdin for process %r: %s", process.pid, exc)
            if process.returncode is None:
                try:
                    process.kill()
                except (ProcessLookupError, RuntimeError) as exc:
                    log.debug("Could not kill process %r during cleanup: %s", process.pid, exc)
        self._processes.clear()

    async def aclose(self) -> None:
        """Asynchronously shut down tracked subprocesses and waiters."""
        processes = list(self._processes.values())
        waiters = list(self._waiters.values())

        for process in processes:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError as exc:
                    log.debug("Could not kill process %r during aclose: %s", process.pid, exc)
            await _close_process_stdin(process)

        for process in processes:
            if process.returncode is None:
                try:
                    await process.wait()
                except ProcessLookupError as exc:
                    log.debug("Could not wait for process %r during aclose: %s", process.pid, exc)

        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)

        self._processes.clear()
        self._waiters.clear()


_DEFAULT_MANAGER: BackgroundTaskManager | None = None
_DEFAULT_MANAGER_KEY: str | None = None


def get_task_manager() -> BackgroundTaskManager:
    """Return the singleton task manager."""
    global _DEFAULT_MANAGER, _DEFAULT_MANAGER_KEY
    current_key = str(get_tasks_dir().resolve())
    if _DEFAULT_MANAGER is None or _DEFAULT_MANAGER_KEY != current_key:
        if _DEFAULT_MANAGER is not None:
            _DEFAULT_MANAGER.close()
        _DEFAULT_MANAGER = BackgroundTaskManager()
        _DEFAULT_MANAGER_KEY = current_key
    return _DEFAULT_MANAGER


def reset_task_manager() -> None:
    """Reset the singleton task manager, closing tracked subprocesses first."""
    global _DEFAULT_MANAGER, _DEFAULT_MANAGER_KEY
    if _DEFAULT_MANAGER is not None:
        _DEFAULT_MANAGER.close()
    _DEFAULT_MANAGER = None
    _DEFAULT_MANAGER_KEY = None


async def shutdown_task_manager() -> None:
    """Async reset that fully reaps tracked subprocesses before clearing state."""
    global _DEFAULT_MANAGER, _DEFAULT_MANAGER_KEY
    if _DEFAULT_MANAGER is not None:
        await _DEFAULT_MANAGER.aclose()
    _DEFAULT_MANAGER = None
    _DEFAULT_MANAGER_KEY = None


def _task_id(task_type: TaskType) -> str:
    prefixes = {
        "local_bash": "b",
        "local_agent": "a",
        "remote_agent": "r",
        "in_process_teammate": "t",
    }
    return f"{prefixes[task_type]}{uuid4().hex[:8]}"


async def _close_process_stdin(process: asyncio.subprocess.Process) -> None:
    stdin = process.stdin
    if stdin is None or stdin.is_closing():
        return
    stdin.close()
    try:
        await stdin.wait_closed()
    except (BrokenPipeError, ConnectionResetError):
        pass
