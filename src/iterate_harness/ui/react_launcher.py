"""Launch the default React terminal frontend."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from iterate_harness import __version__


def _resolve_theme() -> str:
    """Read the theme name from settings, defaulting to 'default'."""
    try:
        from iterate_harness.config.settings import load_settings
        return load_settings().theme or "default"
    except Exception:
        return "default"


def _resolve_npm() -> str:
    """Resolve the npm executable (npm.cmd on Windows)."""
    return shutil.which("npm") or "npm"


def _resolve_tsx(frontend_dir: Path) -> tuple[str, ...]:
    """Resolve the tsx command to invoke directly, bypassing ``npm exec``.

    On Windows / WSL the ``npm exec -- tsx`` wrapper chain often spawns
    intermediate ``cmd.exe`` / shell processes that break TTY stdin
    inheritance.  Calling the ``tsx`` binary directly preserves the TTY so
    that Ink's ``useInput`` (which requires raw-mode stdin) keeps working.

    Returns a tuple of command parts, e.g. ``("path/to/tsx",)`` or
    ``("npm", "exec", "--", "tsx")`` as last-resort fallback.
    """
    # 1. Prefer the locally-installed binary
    bin_dir = frontend_dir / "node_modules" / ".bin"
    if sys.platform == "win32":
        for name in ("tsx.cmd", "tsx.ps1", "tsx"):
            candidate = bin_dir / name
            if candidate.exists():
                return (str(candidate),)
    else:
        candidate = bin_dir / "tsx"
        if candidate.exists():
            return (str(candidate),)

    # 2. Fall back to a globally-installed tsx
    global_tsx = shutil.which("tsx")
    if global_tsx:
        return (global_tsx,)

    # 3. Last resort — go through npm exec (may break TTY on Windows/WSL)
    return (_resolve_npm(), "exec", "--", "tsx")


def get_frontend_dir() -> Path:
    """Return the React terminal frontend directory.

    Checks in order:
    1. Bundled inside the installed package (pip install)
    2. Development repo layout (source checkout)
    """
    # 1. Bundled inside package: iterate_harness/_frontend/
    pkg_frontend = Path(__file__).resolve().parent.parent / "_frontend"
    if (pkg_frontend / "package.json").exists():
        return pkg_frontend

    # 2. Development repo: <repo>/frontend/terminal/
    repo_root = Path(__file__).resolve().parents[3]
    dev_frontend = repo_root / "frontend" / "terminal"
    if (dev_frontend / "package.json").exists():
        return dev_frontend

    # Fallback to package path (will error with clear message)
    return pkg_frontend


def build_backend_command(
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_format: str | None = None,
    permission_mode: str | None = None,
    config_path: str | None = None,
    effort: str | None = None,
    verbose: bool | None = None,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
) -> list[str]:
    """Return the command used by the React frontend to spawn the backend host.

    Credentials are deliberately absent: an ``api_key`` must travel via the
    inherited environment (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``), never
    as a ``--api-key <value>`` argv element that leaks into process listings
    and the ``ITERATE_FRONTEND_CONFIG`` JSON blob.
    """
    command = [sys.executable, "-m", "iterate_harness", "--backend-only"]
    if cwd:
        command.extend(["--cwd", cwd])
    if model:
        command.extend(["--model", model])
    if max_turns is not None:
        command.extend(["--max-turns", str(max_turns)])
    if base_url:
        command.extend(["--base-url", base_url])
    if system_prompt:
        command.extend(["--system-prompt", system_prompt])
    if api_format:
        command.extend(["--api-format", api_format])
    if permission_mode:
        command.extend(["--permission-mode", permission_mode])
    if config_path:
        command.extend(["--settings", config_path])
    if effort:
        command.extend(["--effort", effort])
    if verbose:
        command.append("--verbose")
    for tool in allowed_tools or []:
        command.extend(["--allowed-tools", tool])
    for tool in disallowed_tools or []:
        command.extend(["--disallowed-tools", tool])
    return command


async def launch_react_tui(
    *,
    prompt: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    permission_mode: str | None = None,
    config_path: str | None = None,
    effort: str | None = None,
    verbose: bool | None = None,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
) -> int:
    """Launch the React terminal frontend as the default UI."""
    frontend_dir = get_frontend_dir()
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        raise RuntimeError(f"React terminal frontend is missing: {package_json}")

    npm = _resolve_npm()

    if not (frontend_dir / "node_modules").exists():
        install = await asyncio.create_subprocess_exec(
            npm,
            "install",
            "--no-fund",
            "--no-audit",
            cwd=str(frontend_dir),
        )
        if await install.wait() != 0:
            raise RuntimeError("Failed to install React terminal frontend dependencies")

    env = os.environ.copy()
    # Credentials travel via the environment (inherited by the backend host),
    # never through the argv or the JSON config blob.
    if api_key:
        # Pick the environment variable the backend actually reads for the
        # configured wire format: openai-compatible endpoints resolve the key
        # from OPENAI_API_KEY, the anthropic-native path from
        # ANTHROPIC_API_KEY. Planting the key under the wrong name silently
        # leaves the launched session without credentials.
        if (api_format or "").strip().lower() == "openai":
            env["OPENAI_API_KEY"] = api_key
        else:
            env["ANTHROPIC_API_KEY"] = api_key
    env["ITERATE_FRONTEND_CONFIG"] = json.dumps(
        {
            "backend_command": build_backend_command(
                cwd=cwd or str(Path.cwd()),
                model=model,
                max_turns=max_turns,
                base_url=base_url,
                system_prompt=system_prompt,
                api_format=api_format,
                permission_mode=permission_mode,
                config_path=config_path,
                effort=effort,
                verbose=verbose,
                allowed_tools=allowed_tools,
                disallowed_tools=disallowed_tools,
            ),
            "initial_prompt": prompt,
            "theme": _resolve_theme(),
            "version": __version__,
        }
    )
    tsx_cmd = _resolve_tsx(frontend_dir)
    process = await asyncio.create_subprocess_exec(
        *tsx_cmd,
        "src/index.tsx",
        cwd=str(frontend_dir),
        env=env,
        stdin=None,
        stdout=None,
        stderr=None,
    )
    return await process.wait()


__all__ = ["build_backend_command", "get_frontend_dir", "launch_react_tui"]
