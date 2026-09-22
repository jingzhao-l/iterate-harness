"""Content search tool with a pure-Python fallback."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, Field

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class GrepToolInput(BaseModel):
    """Arguments for the grep tool."""

    pattern: str = Field(description="Regular expression to search for")
    root: str | None = Field(default=None, description="Search root directory")
    file_glob: str = Field(default="**/*")
    case_sensitive: bool = Field(default=True)
    limit: int = Field(default=200, ge=1, le=2000)
    timeout_seconds: int = Field(default=20, ge=1, le=120)


#: Cap on a single line the Python fallback will load into memory (bytes).
#: Ripgrep skips lines past its stream buffer; the fallback must mirror that
#: instead of buffering a multi-GB minified blob on a search hit.
_MAX_PY_LINE_BYTES = 8 * 1024 * 1024

#: Cap on total bytes read from one file in the Python fallback. Side-steps a
#: pathological file that would otherwise have Python load it fully per search
#: (memory bomb) while still scanning the pragmatic head of any real source.
_MAX_PY_FILE_BYTES = 64 * 1024 * 1024


class GrepTool(BaseTool[GrepToolInput]):
    """Search text files for a regex pattern."""

    name = "grep"
    description = "Search file contents with a regular expression."
    input_model = GrepToolInput

    def is_read_only(self, arguments: GrepToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: GrepToolInput, context: ToolExecutionContext) -> ToolResult:
        root = _resolve_path(context.cwd, arguments.root) if arguments.root else context.cwd
        if root.is_file():
            display_base = _display_base(root, context.cwd)
            matches = await _rg_grep_file(
                path=root,
                pattern=arguments.pattern,
                case_sensitive=arguments.case_sensitive,
                limit=arguments.limit,
                display_base=display_base,
                timeout_seconds=arguments.timeout_seconds,
            )
            if matches is not None:
                return _format_rg_result(matches, arguments.timeout_seconds)

            return ToolResult(
                output=await _python_grep_files(
                    paths=[root],
                    pattern=arguments.pattern,
                    case_sensitive=arguments.case_sensitive,
                    limit=arguments.limit,
                    display_base=display_base,
                    timeout_seconds=arguments.timeout_seconds,
                )
            )

        # Prefer ripgrep for performance; fallback to Python when unavailable.
        matches = await _rg_grep(
            root=root,
            pattern=arguments.pattern,
            file_glob=arguments.file_glob,
            case_sensitive=arguments.case_sensitive,
            limit=arguments.limit,
            timeout_seconds=arguments.timeout_seconds,
        )
        if matches is not None:
            return _format_rg_result(matches, arguments.timeout_seconds)

        # Python fallback (kept for portability).
        return ToolResult(
            output=await _python_grep_files(
                paths=root.glob(arguments.file_glob),
                pattern=arguments.pattern,
                case_sensitive=arguments.case_sensitive,
                limit=arguments.limit,
                display_base=root,
                timeout_seconds=arguments.timeout_seconds,
            )
        )


def _display_base(path: Path, cwd: Path) -> Path:
    try:
        path.relative_to(cwd)
    except ValueError:
        return path.parent
    return cwd


async def _python_grep_files(
    *,
    paths: Iterable[Path],
    pattern: str,
    case_sensitive: bool,
    limit: int,
    display_base: Path,
    timeout_seconds: int,
) -> str:
    """Pure-Python grep fallback, bounded in time, line length, and total
    bytes read per file so a pathological tree cannot wedge the tool."""
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        return f"(invalid regex pattern '{pattern}': {exc})"
    collected: list[str] = []

    def _scan() -> str:
        for path in paths:
            if len(collected) >= limit:
                break
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > _MAX_PY_FILE_BYTES:
                continue
            try:
                with path.open("rb") as handle:
                    head = handle.read(8192)
            except OSError:
                continue
            if b"\x00" in head:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    line_index = 0
                    for line in handle:
                        line_index += 1
                        if len(line) > _MAX_PY_LINE_BYTES:
                            # Oversized line (minified blob): skip rather than
                            # hold multi-GB in memory on a hit.
                            continue
                        if "\x00" in line:
                            # Binary content surfaced mid-stream: treat like
                            # ripgrep and drop the file entirely.
                            break
                        if compiled.search(line):
                            collected.append(
                                f"{_format_path(path, display_base)}:{line_index}:{line.rstrip()}"
                            )
                            if len(collected) >= limit:
                                break
            except OSError:
                continue
        return "\n".join(collected) if collected else "(no matches)"

    # Bound the whole fallback by the caller's timeout: a regex with
    # catastrophic backtracking over a big tree must not hang the tool (the
    # ripgrep path already honours the timeout; the fallback mirrors it).
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_scan),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return f"[grep timed out after {timeout_seconds} seconds]"


def _resolve_path(base: Path, candidate: str | None) -> Path:
    path = Path(candidate or ".").expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _format_rg_result(matches: list[str], timeout_seconds: int) -> ToolResult:
    timed_out = bool(matches and matches[-1] == _timeout_marker(timeout_seconds))
    rendered = matches[:-1] if timed_out else matches
    output = "\n".join(rendered) if rendered else "(no matches)"
    if timed_out:
        output = (
            f"{output}\n\n[grep timed out after {timeout_seconds} seconds]"
            if output != "(no matches)"
            else f"[grep timed out after {timeout_seconds} seconds]"
        )
    return ToolResult(output=output, is_error=timed_out)


async def _rg_grep(
    *,
    root: Path,
    pattern: str,
    file_glob: str,
    case_sensitive: bool,
    limit: int,
    timeout_seconds: int,
) -> list[str] | None:
    """Return matches using ripgrep, or None if ripgrep is unavailable."""
    rg = shutil.which("rg")
    if not rg:
        return None

    include_hidden = (root / ".git").exists() or (root / ".gitignore").exists()
    cmd: list[str] = [
        rg,
        "--no-heading",
        "--line-number",
        "--color",
        "never",
    ]
    if include_hidden:
        cmd.append("--hidden")
    if not case_sensitive:
        cmd.append("-i")
    if file_glob:
        cmd.extend(["--glob", file_glob])
    # `--` ensures patterns like `-foo` aren't parsed as flags.
    cmd.extend(["--", pattern, "."])

    from iterate_harness.sandbox.session import get_docker_sandbox

    session = get_docker_sandbox()
    if session is not None and session.is_running:
        process = await session.exec_command(
            cmd,
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    else:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=8 * 1024 * 1024,  # 8 MB per line — avoids LimitOverrunError on long lines
        )

    matches: list[str] = []
    try:
        await asyncio.wait_for(
            _collect_rg_matches(process, matches, limit=limit),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        matches.append(_timeout_marker(timeout_seconds))
        await _terminate_process(process)
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    finally:
        if len(matches) >= limit and process.returncode is None:
            await _terminate_process(process)
        elif process.returncode is None:
            await process.wait()

    # rg exits 0 when matches are found, 1 when none are found.
    # Any other return code indicates an error; fall back to Python.
    if process.returncode in {0, 1, -15, -9}:
        return matches
    return None


async def _rg_grep_file(
    *,
    path: Path,
    pattern: str,
    case_sensitive: bool,
    limit: int,
    display_base: Path,
    timeout_seconds: int,
) -> list[str] | None:
    rg = shutil.which("rg")
    if not rg:
        return None

    cmd: list[str] = [
        rg,
        "--no-heading",
        "--line-number",
        "--color",
        "never",
    ]
    if not case_sensitive:
        cmd.append("-i")
    cmd.extend(["--", pattern, path.name])

    from iterate_harness.sandbox.session import get_docker_sandbox

    session = get_docker_sandbox()
    if session is not None and session.is_running:
        process = await session.exec_command(
            cmd,
            cwd=path.parent,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    else:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(path.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=8 * 1024 * 1024,  # 8 MB per line — avoids LimitOverrunError on long lines
        )

    matches: list[str] = []
    try:
        await asyncio.wait_for(
            _collect_rg_file_matches(
                process,
                matches,
                limit=limit,
                path=path,
                display_base=display_base,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        matches.append(_timeout_marker(timeout_seconds))
        await _terminate_process(process)
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    finally:
        if len(matches) >= limit and process.returncode is None:
            await _terminate_process(process)
        elif process.returncode is None:
            await process.wait()

    if process.returncode in {0, 1, -15, -9}:
        return matches
    return None


def _timeout_marker(timeout_seconds: int) -> str:
    return f"__ITERATE_GREP_TIMEOUT__:{timeout_seconds}"


async def _collect_rg_matches(
    process: asyncio.subprocess.Process,
    matches: list[str],
    *,
    limit: int,
) -> None:
    assert process.stdout is not None
    while len(matches) < limit:
        try:
            raw = await process.stdout.readline()
        except ValueError:
            # Line exceeded the stream buffer limit; skip it and continue.
            continue
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if line:
            matches.append(line)


async def _collect_rg_file_matches(
    process: asyncio.subprocess.Process,
    matches: list[str],
    *,
    limit: int,
    path: Path,
    display_base: Path,
) -> None:
    assert process.stdout is not None
    while len(matches) < limit:
        try:
            raw = await process.stdout.readline()
        except ValueError:
            # Line exceeded the stream buffer limit; skip it and continue.
            continue
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if not line:
            continue
        matches.append(f"{_format_path(path, display_base)}:{line}")


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        process.kill()
        try:
            await process.wait()
        except ProcessLookupError:
            # The process exited between kill() and wait(); its returncode was
            # already reaped by the event loop — nothing left to wait on.
            return None
    except ProcessLookupError:
        return None
    return None


def _format_path(path: Path, display_base: Path) -> str:
    try:
        return str(path.relative_to(display_base))
    except ValueError:
        return str(path)
