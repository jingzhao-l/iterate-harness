"""Lifecycle-hook inventory (``ih hooks list`` + ``GET /api/v1/hooks``).

The engine loads lifecycle hooks from ``settings.hooks`` and from every enabled
plugin, then executes them inline in front of tool calls and turn boundaries —
but until now the operator had no surface that answered "what hooks are
registered, for which events, and will they actually fire?". A hook registered
under a misspelled event name (``pre-tool-use`` instead of ``pre_tool_use``) was
silently dropped by the loader with only a log line; a plugin hook that fails to
validate never appeared anywhere an operator could see it.

:func:`build_hook_inventory` returns one normalized, JSON-ready model shared by
the CLI and the console page. It is read-only and network-free: it loads the
exact registry the engine uses (settings + enabled plugins), reports the static
shape of every hook (type, matcher, command/prompt/url, timeout, block-on-
failure) and lists unknown event names as ``problems`` with an executable
remedy. Nothing here is allowed to raise for one bad hook/plugin: a loader
failure becomes a ``problems`` entry, because the point of the inventory is to
explain a broken setup, not to fail because of it.

The same loader also feeds the capability inventory's ``hooks`` section, so the
two surfaces can never disagree about how many hooks are live.
"""

from __future__ import annotations

import datetime
import os
import shlex
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .events import HookEvent
from .journal import distinct_events, distinct_hook_types, journal_summary

UTC = datetime.timezone.utc

#: Recognized hook definition types (for inventory rendering).
_KNOWN_HOOK_TYPES = frozenset({"command", "prompt", "http", "agent"})

#: POSIX shell builtins that a hook command may legitimately start with even
#: though ``shutil.which`` cannot find them (command hooks run through a shell).
_SHELL_BUILTINS = frozenset(
    {
        ":",
        ".",
        "[",
        "alias",
        "break",
        "cd",
        "command",
        "continue",
        "echo",
        "eval",
        "exec",
        "exit",
        "export",
        "false",
        "getopts",
        "hash",
        "kill",
        "let",
        "local",
        "printf",
        "pwd",
        "read",
        "readonly",
        "return",
        "set",
        "shift",
        "source",
        "test",
        "times",
        "trap",
        "true",
        "type",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "wait",
    }
)


def _command_issues(command: str) -> list[str]:
    """Return static problems with a command hook (empty list when plausible).

    The inventory exists to surface a broken setup *before* a run, so a hook
    pointing at a binary that does not exist is a problem here rather than a
    mystery failure on the first matching tool call. The checks are deliberately
    conservative: a command is only flagged when the shell would certainly fail
    to start it (unparseable, empty, missing absolute path, or a first token
    that is neither a shell builtin nor on ``PATH``).
    """
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return [f"command is not parseable: {exc}"]
    if not parts:
        return ["command is empty"]
    program = parts[0]
    if "$" in program or "{" in program:
        # Shell/parameter expansion is resolved at execution time; a static
        # inventory cannot (and must not) guess its value.
        return []
    expanded = Path(program).expanduser()
    if program.startswith("~") or "/" in program or "\\" in program:
        if "~" in program and not expanded.exists():
            return [f"command path does not exist: {expanded}"]
        if expanded.is_absolute():
            if not expanded.exists():
                return [f"command path does not exist: {expanded}"]
            if not os.access(expanded, os.X_OK):
                return [f"command path is not executable: {expanded}"]
        return []
    if program in _SHELL_BUILTINS:
        return []
    if shutil.which(program) is None:
        return [f"command not found on PATH: {program}"]
    return []


def _url_issues(url: str) -> list[str]:
    """Return static problems with an HTTP hook URL."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return [f"invalid http(s) url: {url or '(empty)'}"]
    return []


def _row_issues(row: dict[str, Any]) -> list[str]:
    """Static validation for one normalized hook row."""
    hook_type = str(row.get("type") or row.get("raw_type") or "")
    if hook_type == "command":
        return _command_issues(str(row.get("command") or ""))
    if hook_type == "http":
        return _url_issues(str(row.get("url") or ""))
    if hook_type in {"prompt", "agent"}:
        if not str(row.get("prompt") or "").strip():
            return [f"{hook_type} hook has an empty prompt"]
    return []


def _apply_validation(row: dict[str, Any], *, problems: list[dict[str, str]]) -> None:
    """Attach per-row validation and mirror issues into the top-level list."""
    issues = _row_issues(row)
    row["issues"] = issues
    row["status"] = "warn" if issues else "ok"
    if not issues:
        return
    detail = " ".join(issues)
    event = str(row.get("event") or "")
    source = str(row.get("source") or "settings")
    problems.append(
        _problem(
            "hook",
            f"{event}:{row.get('type', 'hook')}",
            f"{source} hook can never run as configured: {detail}",
            (
                f"Fix `settings.hooks.{event}` (or the plugin hook) so the first token "
                f"is an installed executable / absolute path, or register it under a valid event."
            ),
        )
    )


def _hook_detail(hook: object) -> str:
    """Return the primary command/prompt/url text of a hook (bounded)."""
    text = str(
        getattr(hook, "command", None)
        or getattr(hook, "prompt", None)
        or getattr(hook, "url", None)
        or ""
    )
    if len(text) > 200:
        text = text[:197] + "..."
    return text


def _hook_row(hook: object, *, source: str, event: str) -> dict[str, Any]:
    """Project one hook definition to the inventory row shape."""
    hook_type = str(getattr(hook, "type", "unknown"))
    row: dict[str, Any] = {
        "type": hook_type if hook_type in _KNOWN_HOOK_TYPES else "unknown",
        "raw_type": hook_type,
        "matcher": str(getattr(hook, "matcher", None) or ""),
        "detail": _hook_detail(hook),
        "model": str(getattr(hook, "model", None) or ""),
        "timeout_seconds": int(getattr(hook, "timeout_seconds", 30) or 30),
        "block_on_failure": bool(getattr(hook, "block_on_failure", False)),
        "source": source,
        "event": event,
    }
    if hook_type == "command":
        row["command"] = _hook_detail(hook)
    elif hook_type == "http":
        row["url"] = _hook_detail(hook)
        headers = getattr(hook, "headers", None) or {}
        if isinstance(headers, dict) and headers:
            row["headers"] = {str(k): str(v) for k, v in headers.items()}
    else:
        row["prompt"] = _hook_detail(hook)
    return row


def build_hook_inventory(
    project_root: str | Path,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    """Build the read-only hook inventory for *project_root*.

    Returns a plain JSON-ready dict so both the Typer command and the FastAPI
    response model can consume the exact same values. A failing loader becomes
    a ``problems`` entry; it never aborts the rest of the inventory.
    """
    root = Path(project_root).expanduser().resolve()
    problems: list[dict[str, str]] = []
    now = datetime.datetime.now(UTC)

    try:
        resolved_settings = settings or load_settings()
    except Exception as exc:  # noqa: BLE001 - inventory must survive a broken settings file
        from iterate_harness.config.settings import Settings

        resolved_settings = Settings()
        problems.append(_loader_problem("settings", exc))

    plugins: list[Any] = []
    try:
        from iterate_harness.plugins import load_plugins

        plugins = load_plugins(resolved_settings, root)
    except Exception as exc:  # noqa: BLE001
        problems.append(_loader_problem("plugins", exc))

    valid_events = {event.value for event in HookEvent}

    # ---- settings-defined hooks ----
    raw_settings_hooks = dict(getattr(resolved_settings, "hooks", {}) or {})
    for raw_event in raw_settings_hooks:
        if raw_event not in valid_events:
            problems.append(
                _problem(
                    "event",
                    str(raw_event),
                    f"unknown hook event {raw_event!r} (valid: {', '.join(sorted(valid_events))})",
                    f"Fix `settings.hooks.{raw_event}` to one of: {', '.join(sorted(valid_events))}.",
                )
            )

    # ---- plugin-defined hooks ----
    for plugin in plugins:
        if not getattr(plugin, "enabled", True):
            continue
        for raw_event in (getattr(plugin, "hooks", {}) or {}):
            if raw_event not in valid_events:
                problems.append(
                    _problem(
                        "event",
                        str(raw_event),
                        f"plugin {getattr(plugin, 'name', plugin)!r} registers unknown hook event {raw_event!r} "
                        f"(valid: {', '.join(sorted(valid_events))})",
                        f"Fix the plugin's hook event to one of: {', '.join(sorted(valid_events))}.",
                    )
                )

    # ---- event-ordered hook rows (settings first, then enabled plugins) ----
    events: list[dict[str, Any]] = []
    total = 0
    for event in HookEvent:
        event_rows: list[dict[str, Any]] = []
        for raw_hook in raw_settings_hooks.get(event.value, []) or []:
            row = _hook_row(raw_hook, source="settings", event=event.value)
            _apply_validation(row, problems=problems)
            event_rows.append(row)
            total += 1
        for plugin in plugins:
            if not getattr(plugin, "enabled", True):
                continue
            for raw_hook in (getattr(plugin, "hooks", {}) or {}).get(event.value, []) or []:
                source = f"plugin:{getattr(plugin, 'name', 'unknown')}"
                row = _hook_row(raw_hook, source=source, event=event.value)
                _apply_validation(row, problems=problems)
                event_rows.append(row)
                total += 1
        if event_rows:
            events.append(
                {
                    "event": event.value,
                    "count": len(event_rows),
                    "hooks": event_rows,
                }
            )

    by_event: dict[str, int] = {entry["event"]: entry["count"] for entry in events}
    journal = journal_summary(root)

    return {
        "project_root": str(root),
        "generated_at": now.isoformat(),
        "summary": {
            "total": total,
            "events": len(events),
            "by_event": by_event,
            "journal_total": journal["total"],
            "journal_failures": journal["failures"],
            "journal_blocked": journal["blocked"],
        },
        "has_problems": bool(problems),
        "problems": problems,
        "events": events,
        "journal": journal,
        "journal_events": distinct_events(root),
        "journal_types": distinct_hook_types(root),
    }


def _problem(kind: str, name: str, detail: str, remedy: str) -> dict[str, str]:
    return {"kind": kind, "name": name, "detail": detail, "remedy": remedy}


def _loader_problem(kind: str, exc: Exception) -> dict[str, str]:
    return _problem(
        "loader",
        kind,
        f"{type(exc).__name__}: {exc}",
        "Fix the reported file/config and refresh; run `ih doctor` for a broader environment check.",
    )


def load_settings() -> Any:
    """Import-time shim so the module can be imported without the engine."""
    from iterate_harness.config.settings import load_settings as _load

    return _load()


def render_hook_inventory(inventory: dict[str, Any]) -> str:
    """Render the inventory as human-readable terminal text."""
    summary = inventory.get("summary") or {}
    lines = [
        f"IterateHarness lifecycle hooks — {inventory.get('project_root', '')}",
        (
            f"hooks: {summary.get('total', 0)} across {summary.get('events', 0)} event(s) · "
            f"journal: {summary.get('journal_total', 0)} executions "
            f"({summary.get('journal_failures', 0)} failed, {summary.get('journal_blocked', 0)} blocked)"
        ),
    ]

    problems = inventory.get("problems") or []
    if problems:
        lines.append("")
        lines.append(f"Problems ({len(problems)}):")
        for problem in problems:
            lines.append(f"  [{problem.get('kind', '?')}] {problem.get('name', '')}: {problem.get('detail', '')}")
            remedy = str(problem.get("remedy") or "").strip()
            if remedy:
                lines.append(f"    remedy: {remedy}")
    else:
        lines.append("")
        lines.append("Problems: none")

    events = inventory.get("events") or []
    if not events:
        lines.append("")
        lines.append("No lifecycle hooks registered (settings.hooks is empty).")
        return "\n".join(lines)

    for event_group in events:
        event = str(event_group.get("event") or "")
        lines.append("")
        lines.append(f"{event} ({event_group.get('count', 0)}):")
        for row in event_group.get("hooks") or []:
            hook_type = str(row.get("type") or row.get("raw_type") or "")
            matcher = str(row.get("matcher") or "")
            detail = str(row.get("detail") or "")
            source = str(row.get("source") or "settings")
            block = " [blocks on failure]" if row.get("block_on_failure") else ""
            matcher_suffix = f" matcher={matcher}" if matcher else ""
            lines.append(f"  - {hook_type} ({source}){matcher_suffix}: {detail}{block}")
            for issue in row.get("issues") or []:
                lines.append(f"    ! {issue}")
    return "\n".join(lines)


def hook_rows_for_capabilities(
    project_root: str | Path,
    *,
    settings: Any = None,
) -> list[dict[str, Any]]:
    """Return the flat hook rows for the capability inventory's ``hooks`` section."""
    inventory = build_hook_inventory(project_root, settings=settings)
    rows: list[dict[str, Any]] = []
    for event_group in inventory.get("events") or []:
        for row in event_group.get("hooks") or []:
            rows.append(
                {
                    "event": event_group.get("event", ""),
                    "type": row.get("type", ""),
                    "source": row.get("source", ""),
                    "matcher": row.get("matcher", ""),
                    "detail": row.get("detail", ""),
                    "block_on_failure": row.get("block_on_failure", False),
                    "status": row.get("status", "ok"),
                    "issues": row.get("issues", []),
                }
            )
    return rows
