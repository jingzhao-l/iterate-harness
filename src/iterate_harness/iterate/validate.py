"""Validation runner: execute PRECONFIGURED validation commands.

Python port of ``harness/iterate-plugin/src/tools/validate.ts`` (the pure
core; harness tool registration wires this into the kernel separately).

Security model preserved from the TS plugin: only commands predefined in
``iterate.config.yaml`` ``validation.commands`` may run — the user trusts
exactly these, and nothing else. Matching is EXACT (after trim); this
replaces the old prefix-match whitelist, which let e.g.
``python3 -c "..."`` slip through on a ``python3`` prefix.
"""

from __future__ import annotations

import io
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config_loader import flatten_commands, is_command_allowed, load_effective_config
from .types import ValidationResult

DEFAULT_TIMEOUT_MS = 120_000
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB, mirrors the TS maxBuffer cap

REJECT_NO_CONFIG = (
    "No iterate.config.yaml at project root — running on built-in defaults, "
    "which configure NO trusted validation commands. "
    "Nothing can be validated until you define trusted commands in "
    "`validation.commands`."
)
REJECT_NO_COMMANDS = (
    "No validation.commands configured in iterate.config.yaml. "
    "Nothing can be validated until you define trusted commands in "
    "`validation.commands`."
)
REJECT_NOT_ALLOWED_PREFIX = (
    "Command must exactly match a command predefined in iterate.config.yaml "
    "validation.commands."
)


@dataclass
class ValidationRunResult:
    """Structured result of a ``run_validation`` call.

    ``allowed=False`` means the command was rejected before execution
    (``exit_code=-1``, empty output, ``reject_reason`` explains why).
    ``allowed=True`` mirrors :class:`ValidationResult`.
    """

    allowed: bool
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    reject_reason: str | None = None


def _read_head(stream: "io.BufferedRandom | None", limit: int) -> tuple[str, str]:
    """Read at most ``limit`` bytes from the head of ``stream``.

    Returns ``(text, note)`` where ``note`` is non-empty when output was
    dropped, so the caller can say so instead of silently truncating.
    """
    if stream is None:
        return "", ""
    try:
        stream.seek(0)
    except (OSError, ValueError):
        return "", ""
    data = stream.read(limit + 1)
    note = ""
    if len(data) > limit:
        data = data[:limit]
        note = f"\n[truncated at {limit} bytes]"
    return data.decode("utf-8", errors="replace"), note


def run_command(command: str, cwd: str, timeout_ms: int) -> ValidationResult:
    """Run a single shell command with timeout; return structured results.

    Never raises: a timeout or nonzero exit is reported in the result.
    Output is truncated to :data:`MAX_OUTPUT_BYTES` to bound memory, and
    decoding errors fall back to replacement characters.

    ``stdout``/``stderr`` are redirected to temporary files rather than
    pipes. ``capture_output=True`` buffers the child's *entire* output in
    memory and only then slices the first 10 MB, so a trusted command that
    prints gigabytes (``yes``, a test suite with a debug build, ``npm install``
    without ``--silent``) could OOM the iterate loop — the truncation after
    the fact protects nothing. The temp files also cap the child's output
    and are read back only up to the limit.
    """
    import os
    import tempfile

    start = time.perf_counter()
    env = dict(os.environ)
    env["PAGER"] = "cat"
    try:
        with tempfile.TemporaryFile() as out_fh, tempfile.TemporaryFile() as err_fh:
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=cwd,
                    timeout=timeout_ms / 1000,
                    stdout=out_fh,
                    stderr=err_fh,
                    stdin=subprocess.DEVNULL,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                duration_ms = round((time.perf_counter() - start) * 1000)
                stdout, out_note = _read_head(out_fh, MAX_OUTPUT_BYTES)
                stderr, err_note = _read_head(err_fh, MAX_OUTPUT_BYTES)
                return ValidationResult(
                    command=command,
                    exit_code=-1,
                    stdout=stdout + out_note,
                    stderr=stderr + err_note,
                    timed_out=True,
                    duration_ms=duration_ms,
                )
            duration_ms = round((time.perf_counter() - start) * 1000)
            stdout, out_note = _read_head(out_fh, MAX_OUTPUT_BYTES)
            stderr, err_note = _read_head(err_fh, MAX_OUTPUT_BYTES)
            return ValidationResult(
                command=command,
                exit_code=completed.returncode,
                stdout=stdout + out_note,
                stderr=stderr + err_note,
                timed_out=False,
                duration_ms=duration_ms,
            )
    except OSError as exc:
        # An unlaunchable shell (missing cwd, fork failure) is a result, not
        # a crash: the loop reports it as a failed validation command.
        duration_ms = round((time.perf_counter() - start) * 1000)
        return ValidationResult(
            command=command,
            exit_code=-1,
            stdout="",
            stderr=f"failed to execute command: {exc}",
            timed_out=False,
            duration_ms=duration_ms,
        )


def run_validation(
    command: str,
    project_root: str | Path | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> ValidationRunResult:
    """Run ``command`` if (and only if) it is preconfigured in the project.

    1. Load the effective config (defaults + project overrides; never None).
    2. Reject when no trusted commands are configured at all.
    3. Reject on any non-exact match against the configured commands.
    4. Otherwise execute with timeout and return the structured result.
    """
    root = str(project_root) if project_root is not None else str(Path.cwd())
    effective = load_effective_config(root)
    config = effective.config

    predefined = flatten_commands(config.validation.commands)
    if not predefined:
        reason = REJECT_NO_CONFIG if effective.source == "defaults" else REJECT_NO_COMMANDS
        return ValidationRunResult(
            allowed=False,
            command=command,
            exit_code=-1,
            stdout="",
            stderr="",
            timed_out=False,
            duration_ms=0,
            reject_reason=reason,
        )
    if not is_command_allowed(command, predefined):
        return ValidationRunResult(
            allowed=False,
            command=command,
            exit_code=-1,
            stdout="",
            stderr="",
            timed_out=False,
            duration_ms=0,
            reject_reason=f"{REJECT_NOT_ALLOWED_PREFIX} Allowed commands: "
            f"{' | '.join(predefined)}",
        )

    result = run_command(command.strip(), root, timeout_ms)
    return ValidationRunResult(
        allowed=True,
        command=result.command,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        timed_out=result.timed_out,
        duration_ms=result.duration_ms,
    )
