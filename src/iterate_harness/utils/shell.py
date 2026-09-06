"""Shared shell and subprocess helpers."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from iterate_harness.config import Settings, load_settings
from iterate_harness.platforms import PlatformName, get_platform
from iterate_harness.sandbox import wrap_command_for_sandbox

#: Environment variable that restores the legacy login-shell behaviour
#: (``bash -lc`` with profile/rc sourcing) for POSIX-backed execution.
#: Agent-executed commands are non-interactive/non-login by design: every
#: subprocess already inherits the parent harness environment (PATH etc. is
#: resolved when ``ih`` starts), so re-sourcing ``~/.bash_profile`` /
#: ``~/.bashrc`` on EVERY spawn only re-pays expensive login hooks (conda init,
#: nvm, pyenv …) that can add seconds per call — and under load, break short
#: timeouts across the task manager, hooks, cron, and the bash tool. Set to
#: ``1`` to opt back into sourcing the user's rc files.
LOAD_RC_ENV_VAR = "ITERATE_HARNESS_SHELL_LOAD_RC"

#: argv prefix for the fast, deterministic POSIX shell. ``--noprofile --norc``
#: turns the harness shell into an agent runtime rather than an interactive
#: login terminal: deterministic, fast, and free of user-hook side effects.
_BASH_NO_RC_ARGS = ("--noprofile", "--norc", "-c")


def _bash_argv(command: str, bash: str, *, load_rc: bool) -> list[str]:
    """Return the bash argv for *command* honoring the rc-loading switch."""
    if load_rc and os.environ.get(LOAD_RC_ENV_VAR) == "1":
        return [bash, "-lc", command]
    return [bash, *_BASH_NO_RC_ARGS, command]


def resolve_shell_command(
    command: str,
    *,
    platform_name: PlatformName | None = None,
    prefer_pty: bool = False,
) -> list[str]:
    """Return argv for the best available shell on the current platform.

    POSIX shells run with ``--noprofile --norc`` (see :data:`LOAD_RC_ENV_VAR`
    for the escape hatch), Windows PowerShell with ``-NoProfile`` — the harness
    executes *agent* commands, not interactive login terminals.
    """
    resolved_platform = platform_name or get_platform()
    load_rc = os.environ.get(LOAD_RC_ENV_VAR) == "1"
    if resolved_platform == "windows":
        bash = shutil.which("bash")
        if bash and _bash_is_usable(bash):
            return _bash_argv(command, bash, load_rc=load_rc)
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            return [powershell, "-NoLogo", "-NoProfile", "-Command", command]
        return [shutil.which("cmd.exe") or "cmd.exe", "/d", "/s", "/c", command]

    bash = shutil.which("bash")
    if bash:
        argv = _bash_argv(command, bash, load_rc=load_rc)
        if prefer_pty:
            wrapped = _wrap_command_with_script(argv, platform_name=resolved_platform)
            if wrapped is not None:
                return wrapped
        return argv
    shell = shutil.which("sh") or os.environ.get("SHELL") or "/bin/sh"
    argv = _bash_argv(command, shell, load_rc=load_rc)
    if prefer_pty:
        wrapped = _wrap_command_with_script(argv, platform_name=resolved_platform)
        if wrapped is not None:
            return wrapped
    return argv


async def create_shell_subprocess(
    command: str,
    *,
    cwd: str | Path,
    settings: Settings | None = None,
    prefer_pty: bool = False,
    stdin: int | None = asyncio.subprocess.DEVNULL,
    stdout: int | None = None,
    stderr: int | None = None,
    env: Mapping[str, str] | None = None,
) -> asyncio.subprocess.Process:
    """Spawn a shell command with platform-aware shell selection and sandboxing."""
    resolved_settings = settings or load_settings()

    # Docker backend: route through docker exec
    if resolved_settings.sandbox.enabled and resolved_settings.sandbox.backend == "docker":
        from iterate_harness.sandbox.session import get_docker_sandbox

        session = get_docker_sandbox()
        if session is not None and session.is_running:
            argv = resolve_shell_command(command)
            return await session.exec_command(
                argv,
                cwd=cwd,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=dict(env) if env is not None else None,
            )
        if resolved_settings.sandbox.fail_if_unavailable:
            from iterate_harness.sandbox import SandboxUnavailableError

            raise SandboxUnavailableError("Docker sandbox session is not running")

    # Non-docker path: run via the local shell.
    argv = resolve_shell_command(command, prefer_pty=prefer_pty)
    argv, cleanup_path = wrap_command_for_sandbox(argv, settings=resolved_settings)

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(Path(cwd).resolve()),
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=dict(env) if env is not None else None,
        )
    except Exception:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)
        raise

    if cleanup_path is not None:
        asyncio.create_task(_cleanup_after_exit(process, cleanup_path))
    return process


def _wrap_command_with_script(
    argv: list[str],
    *,
    platform_name: PlatformName | None = None,
) -> list[str] | None:
    resolved_platform = platform_name or get_platform()
    if resolved_platform == "macos":
        return None
    script = shutil.which("script")
    if script is None:
        return None
    # The command is always the trailing element whether the shell argv is the
    # fast ``bash --noprofile --norc -c <command>`` form or the legacy
    # ``bash -lc <command>`` form.
    if len(argv) >= 3 and argv[-2] == "-c":
        return [script, "-qefc", argv[-1], "/dev/null"]
    return None


def _bash_is_usable(bash_path: str) -> bool:
    """Return True when a discovered bash executable can run commands.

    On Windows, ``shutil.which("bash")`` can find WSL's ``bash.exe`` even when no
    WSL distribution is installed. In that case the executable exists but every
    command fails, so fall back to PowerShell/cmd instead of selecting it.
    The probe mirrors the runtime shell mode (no-rc by default, ``-lc`` when
    :data:`LOAD_RC_ENV_VAR` opts back in).
    """
    probe = [bash_path, "-lc", "exit 0"]
    if os.environ.get(LOAD_RC_ENV_VAR) != "1":
        probe = [bash_path, *_BASH_NO_RC_ARGS, "exit 0"]
    try:
        result = subprocess.run(
            probe,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


async def _cleanup_after_exit(process: asyncio.subprocess.Process, cleanup_path: Path) -> None:
    try:
        await process.wait()
    finally:
        cleanup_path.unlink(missing_ok=True)
