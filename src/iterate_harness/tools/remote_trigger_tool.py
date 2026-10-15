"""Tool for triggering local named jobs on demand."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from pydantic import BaseModel, Field

from iterate_harness.services.cron import get_cron_job
from iterate_harness.sandbox import SandboxUnavailableError
from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from iterate_harness.utils.shell import create_shell_subprocess

#: Cap the job output we buffer per stream (stdout and stderr). A runaway job
#: such as ``yes`` would otherwise fill memory with gigabytes before the
#: timeout fires, and the whole buffer would be handed to the model.
_TRIGGER_OUTPUT_MAX_BYTES = 64 * 1024

_TRUNCATION_MARKER = "...[job output truncated]"


class RemoteTriggerToolInput(BaseModel):
    """Arguments for triggering a local named job."""

    name: str = Field(
        max_length=256,
        min_length=1,
        description="Cron job name",
    )
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class RemoteTriggerTool(BaseTool[RemoteTriggerToolInput]):
    """Run a registered cron job immediately."""

    name = "remote_trigger"
    description = "Trigger a configured local cron-style job immediately."
    input_model = RemoteTriggerToolInput

    async def execute(
        self,
        arguments: RemoteTriggerToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        job = get_cron_job(arguments.name)
        if job is None:
            return ToolResult(output=f"Cron job not found: {arguments.name}", is_error=True)

        cwd = Path(job.get("cwd") or context.cwd).expanduser()
        try:
            process = await create_shell_subprocess(
                str(job["command"]),
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Own process group so a timeout killpg reaps grandchildren
                # instead of only the shell wrapper.
                start_new_session=True,
            )
        except SandboxUnavailableError as exc:
            return ToolResult(output=str(exc), is_error=True)
        try:
            stdout, stderr, truncated = await _communicate_bounded(
                process,
                timeout=arguments.timeout_seconds,
                max_bytes=_TRIGGER_OUTPUT_MAX_BYTES,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                output=f"Remote trigger timed out after {arguments.timeout_seconds} seconds",
                is_error=True,
            )
        except asyncio.CancelledError:
            _kill_synchronously(process)
            raise

        parts = []
        if stdout:
            parts.append(stdout.decode("utf-8", errors="replace").rstrip())
        if stderr:
            parts.append(stderr.decode("utf-8", errors="replace").rstrip())
        if truncated:
            parts.append(_TRUNCATION_MARKER)
        body = "\n".join(part for part in parts if part).strip() or "(no output)"
        return ToolResult(
            output=f"Triggered {arguments.name}\n{body}",
            is_error=process.returncode != 0,
            metadata={"returncode": process.returncode, "output_truncated": truncated},
        )


async def _communicate_bounded(
    process: asyncio.subprocess.Process,
    *,
    timeout: float,
    max_bytes: int,
) -> tuple[bytes, bytes, bool]:
    """Collect process output with memory and time bounds.

    Reads both pipes concurrently until EOF while buffering at most
    ``max_bytes`` per stream; the remainder is drained and DISCARDED so the
    child never blocks on a full pipe. Returns ``(stdout, stderr, truncated)``
    where the flag reports that either stream overflowed the buffer. On
    timeout the process is killed (and reaped) before the error propagates.
    """

    async def _read_stream_bounded(
        stream: asyncio.StreamReader | None,
    ) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        chunks = bytearray()
        truncated = False
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            room = max_bytes - len(chunks)
            if room > 0:
                chunks.extend(chunk[:room])
            if len(chunk) > room:
                # Part (or all) of this chunk overflowed the buffer; keep
                # draining so the child never blocks on a full pipe, but
                # discard the remainder and flag the truncation.
                truncated = True
        return bytes(chunks), truncated

    try:
        stdout_pair, stderr_pair, _ = await asyncio.wait_for(
            asyncio.gather(
                _read_stream_bounded(process.stdout),
                _read_stream_bounded(process.stderr),
                process.wait(),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _kill_synchronously(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        raise
    stdout, stdout_truncated = stdout_pair
    stderr, stderr_truncated = stderr_pair
    return stdout, stderr, stdout_truncated or stderr_truncated


def _kill_synchronously(process: asyncio.subprocess.Process) -> None:
    """Kill the child's process group when it leads one, else the child."""
    if process.returncode is not None:
        return
    pid = getattr(process, "pid", None)
    if isinstance(pid, int) and pid > 0:
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = None
        if pgid == pid:
            try:
                os.killpg(pgid, signal.SIGKILL)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
    try:
        process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass
