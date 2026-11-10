"""Project capability inventory (``ih capabilities`` + ``GET /api/v1/capabilities``).

The harness has always loaded a large surface at run time — built-in tools,
slash commands, bundled/user/plugin skills, enabled plugins and MCP server
configurations — but until this module the only way to see it was a full
``ih --dry-run`` preview or by reading several unrelated source files. A broken
MCP command in ``settings.json`` was especially invisible: it only surfaced as
a failed connection inside a live run, long after the operator had left the
console.

:func:`build_capability_inventory` is deliberately read-only and
network-free. It loads exactly the same registries the engine uses, validates
the static shape of every MCP server (command exists, URL parses, cwd exists)
and returns one normalized model shared by the CLI and the WebUI page. It never
starts an MCP server and never contacts a provider.

Nothing here is allowed to raise for one bad plugin/skill/server: a loader
failure becomes a ``problem`` entry with an executable remedy, because the
point of the inventory is to explain a broken setup, not to fail because of it.
"""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path
from typing import Any

from .config.settings import Settings, load_settings

UTC = datetime.timezone.utc

#: Slash commands whose only job is to inspect current state.
READ_ONLY_COMMANDS = frozenset(
    {
        "help",
        "version",
        "status",
        "context",
        "cost",
        "usage",
        "stats",
        "onboarding",
        "skills",
        "doctor",
        "diff",
        "branch",
        "privacy-settings",
        "rate-limit-options",
        "release-notes",
        "upgrade",
        "keybindings",
        "files",
    }
)

#: Slash commands that can mutate local state, queue work or trigger a turn.
MUTATING_COMMANDS = frozenset(
    {
        "clear",
        "compact",
        "resume",
        "session",
        "export",
        "share",
        "copy",
        "tag",
        "rewind",
        "init",
        "bridge",
        "login",
        "logout",
        "feedback",
        "config",
        "permissions",
        "plan",
        "fast",
        "effort",
        "passes",
        "turns",
        "continue",
        "provider",
        "model",
        "theme",
        "output-style",
        "vim",
        "voice",
        "commit",
        "issue",
        "pr_comments",
        "agents",
        "subagents",
        "tasks",
        "memory",
    }
)


def command_behavior(name: str) -> dict[str, str]:
    """Classify a slash command for the preview/inventory surfaces."""
    if name in READ_ONLY_COMMANDS:
        return {
            "kind": "read_only",
            "detail": "This slash command mainly inspects current state and should not require a model turn.",
        }
    if name in MUTATING_COMMANDS:
        return {
            "kind": "stateful",
            "detail": "This slash command can mutate local state, queue work, or trigger follow-up execution depending on its arguments.",
        }
    return {
        "kind": "unknown",
        "detail": "This slash command comes from a handler or plugin that dry-run cannot classify precisely.",
    }


def schema_argument_preview(tool_schema: dict[str, Any]) -> dict[str, Any]:
    """Return sorted required/optional argument names for one tool schema."""
    input_schema = tool_schema.get("input_schema")
    if not isinstance(input_schema, dict):
        return {"required_args": [], "optional_args": []}
    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return {"required_args": [], "optional_args": []}
    required_raw = input_schema.get("required")
    required = (
        sorted(str(name) for name in required_raw if isinstance(name, str))
        if isinstance(required_raw, list)
        else []
    )
    optional = sorted(name for name in properties if name not in required)
    return {"required_args": required, "optional_args": optional}


def mcp_transport_preview(config: object) -> dict[str, str]:
    """Return a human-readable transport + target for one MCP config."""
    if hasattr(config, "type"):
        transport = str(getattr(config, "type") or "unknown")
    elif isinstance(config, dict):
        transport = str(config.get("type") or "unknown")
    else:
        transport = "unknown"

    if transport == "stdio":
        command = getattr(config, "command", None) if not isinstance(config, dict) else config.get("command")
        args = getattr(config, "args", None) if not isinstance(config, dict) else config.get("args")
        rendered_args = " ".join(str(item) for item in args) if isinstance(args, list) and args else ""
        target = " ".join(part for part in (str(command or "").strip(), rendered_args.strip()) if part).strip()
        return {"transport": "stdio", "target": target or "configured"}
    if transport in {"http", "ws"}:
        url = getattr(config, "url", None) if not isinstance(config, dict) else config.get("url")
        return {"transport": transport, "target": str(url or "").strip() or "configured"}
    return {"transport": transport, "target": "configured"}


def validate_mcp_server(name: str, config: object) -> dict[str, Any]:
    """Statically validate one MCP server config.

    The checks deliberately mirror what a connection attempt would fail on
    (missing executable, missing cwd, malformed URL) without performing the
    connection. ``remedy`` is populated for errors so the CLI/console can show
    a copyable fix instead of just a red badge.
    """
    preview = mcp_transport_preview(config)
    issues: list[str] = []
    remedies: list[str] = []
    transport = preview["transport"]

    if transport == "stdio":
        command = getattr(config, "command", None) if not isinstance(config, dict) else config.get("command")
        raw_cwd = getattr(config, "cwd", None) if not isinstance(config, dict) else config.get("cwd")
        command_text = str(command or "").strip()
        if not command_text:
            issues.append("missing command")
            remedies.append(f"Set `mcp_servers.{name}.command` to an executable (settings.json or the plugin's mcp.json).")
        elif shutil.which(command_text) is None:
            issues.append(f"command not found in PATH: {command_text}")
            remedies.append(
                f"Install `{command_text}` or change `mcp_servers.{name}.command` to an absolute executable path."
            )
        if raw_cwd:
            resolved_cwd = Path(str(raw_cwd)).expanduser()
            if not resolved_cwd.exists():
                issues.append(f"cwd does not exist: {resolved_cwd}")
                remedies.append(f"Create `{resolved_cwd}` or fix `mcp_servers.{name}.cwd`.")
    elif transport in {"http", "ws"}:
        from urllib.parse import urlparse

        raw_url = getattr(config, "url", None) if not isinstance(config, dict) else config.get("url")
        parsed = urlparse(str(raw_url or "").strip())
        expected = {"http", "https"} if transport == "http" else {"ws", "wss"}
        if parsed.scheme not in expected or not parsed.netloc:
            issues.append(f"invalid {transport} url: {raw_url}")
            schemes = " or ".join(f"`{scheme}://`" for scheme in sorted(expected))
            remedies.append(
                f"Use an absolute URL starting with {schemes} for `mcp_servers.{name}.url`."
            )

    return {
        "name": name,
        **preview,
        "status": "error" if issues else "ok",
        "issues": issues,
        "remedy": " ".join(remedies),
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


def build_capability_inventory(
    project_root: str | Path,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Build the read-only capability inventory for *project_root*.

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
        resolved_settings = Settings()
        problems.append(_loader_problem("settings", exc))

    plugins: list[Any] = []
    try:
        from .plugins import load_plugins

        plugins = load_plugins(resolved_settings, root)
    except Exception as exc:  # noqa: BLE001
        problems.append(_loader_problem("plugins", exc))

    plugin_commands: list[Any] = []
    plugin_rows: list[dict[str, Any]] = []
    for plugin in plugins:
        plugin_commands.extend(plugin.commands)
        manifest = plugin.manifest
        plugin_rows.append(
            {
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "enabled": bool(plugin.enabled),
                "path": str(plugin.path),
                "skills": len(plugin.skills),
                "commands": len(plugin.commands),
                "agents": len(plugin.agents),
                "mcp_servers": len(plugin.mcp_servers),
            }
        )

    command_rows: list[dict[str, Any]] = []
    try:
        from .commands import create_default_command_registry

        registry = create_default_command_registry(plugin_commands=plugin_commands)
    except Exception as exc:  # noqa: BLE001
        registry = None
        problems.append(_loader_problem("commands", exc))
    if registry is not None:
        for command in registry.list_commands():
            behavior = command_behavior(command.name)
            command_rows.append(
                {
                    "name": command.name,
                    "description": command.description,
                    "behavior": behavior["kind"],
                    "behavior_detail": behavior["detail"],
                    "remote_invocable": bool(command.remote_invocable),
                    "remote_admin_opt_in": bool(command.remote_admin_opt_in),
                }
            )

    skill_rows: list[dict[str, Any]] = []
    try:
        from .skills import load_skill_registry

        skill_rows = [
            {
                "name": skill.name,
                "description": skill.description,
                "source": skill.source,
                "path": skill.path or "",
                "user_invocable": bool(skill.user_invocable),
                "disable_model_invocation": bool(skill.disable_model_invocation),
                "model": skill.model or "",
            }
            for skill in load_skill_registry(root, settings=resolved_settings).list_skills()
        ]
    except Exception as exc:  # noqa: BLE001
        problems.append(_loader_problem("skills", exc))

    mcp_rows: list[dict[str, Any]] = []
    try:
        from .mcp.config import load_mcp_server_configs

        mcp_configs = load_mcp_server_configs(resolved_settings, plugins)
    except Exception as exc:  # noqa: BLE001
        mcp_configs = {}
        problems.append(_loader_problem("mcp", exc))
    for name, config in sorted(mcp_configs.items()):
        entry = validate_mcp_server(name, config)
        mcp_rows.append(entry)
        if entry["status"] == "error":
            problems.append(
                _problem(
                    "mcp",
                    name,
                    "; ".join(entry["issues"]),
                    str(entry["remedy"]),
                )
            )

    tool_rows: list[dict[str, Any]] = []
    try:
        from .tools import create_default_tool_registry

        for tool_schema in create_default_tool_registry().to_api_schema():
            tool_rows.append(
                {
                    "name": str(tool_schema.get("name") or ""),
                    "description": str(tool_schema.get("description") or ""),
                    **schema_argument_preview(tool_schema),
                }
            )
    except Exception as exc:  # noqa: BLE001
        problems.append(_loader_problem("tools", exc))

    summary = {
        "tools": len(tool_rows),
        "commands": len(command_rows),
        "skills": len(skill_rows),
        "plugins": len(plugin_rows),
        "enabled_plugins": sum(1 for row in plugin_rows if row["enabled"]),
        "mcp_servers": len(mcp_rows),
        "mcp_errors": sum(1 for row in mcp_rows if row["status"] == "error"),
        "problems": len(problems),
    }

    hook_rows: list[dict[str, Any]] = []
    try:
        from .hooks.inventory import hook_rows_for_capabilities

        hook_rows = hook_rows_for_capabilities(root, settings=resolved_settings)
    except Exception as exc:  # noqa: BLE001
        problems.append(_loader_problem("hooks", exc))
    summary["hooks"] = len(hook_rows)

    return {
        "project_root": str(root),
        "generated_at": now.isoformat(),
        "summary": summary,
        "has_problems": bool(problems),
        "problems": problems,
        "tools": sorted(tool_rows, key=lambda row: str(row["name"])),
        "commands": sorted(command_rows, key=lambda row: str(row["name"])),
        "skills": sorted(skill_rows, key=lambda row: str(row["name"])),
        "plugins": sorted(plugin_rows, key=lambda row: str(row["name"])),
        "mcp_servers": sorted(mcp_rows, key=lambda row: str(row["name"])),
        "hooks": sorted(hook_rows, key=lambda row: (str(row.get("event")), str(row.get("type")))),
    }


def render_capability_inventory(inventory: dict[str, Any]) -> str:
    """Render the inventory as human-readable terminal text."""
    summary = inventory.get("summary") or {}
    lines = [
        f"IterateHarness capability inventory — {inventory.get('project_root', '')}",
        (
            f"tools: {summary.get('tools', 0)} · commands: {summary.get('commands', 0)} · "
            f"skills: {summary.get('skills', 0)} · plugins: {summary.get('plugins', 0)} "
            f"({summary.get('enabled_plugins', 0)} enabled) · MCP servers: {summary.get('mcp_servers', 0)} · "
            f"hooks: {summary.get('hooks', 0)}"
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

    def section(title: str, key: str, formatter: Any) -> None:
        rows = inventory.get(key) or []
        lines.append("")
        lines.append(f"{title} ({len(rows)}):")
        if not rows:
            lines.append("  (none)")
            return
        for row in rows:
            lines.append(f"  {formatter(row)}")

    section(
        "MCP servers",
        "mcp_servers",
        lambda row: (
            f"{row.get('name')} [{row.get('transport')}: {row.get('target')}] "
            f"{row.get('status')}"
            + (f" — {'; '.join(row.get('issues') or [])}" if row.get("issues") else "")
        ),
    )
    section(
        "Skills",
        "skills",
        lambda row: f"{row.get('name')} [{row.get('source')}] {row.get('description')}",
    )
    section(
        "Plugins",
        "plugins",
        lambda row: (
            f"{row.get('name')} {row.get('version')} "
            f"[{'enabled' if row.get('enabled') else 'disabled'}] "
            f"skills={row.get('skills')} commands={row.get('commands')} agents={row.get('agents')}"
        ),
    )
    section(
        "Tools",
        "tools",
        lambda row: (
            f"{row.get('name')} — {row.get('description')}"
            + (
                f" (required: {', '.join(row.get('required_args') or [])})"
                if row.get("required_args")
                else ""
            )
        ),
    )
    section(
        "Slash commands",
        "commands",
        lambda row: f"/{row.get('name')} [{row.get('behavior')}] {row.get('description')}",
    )
    section(
        "Lifecycle hooks",
        "hooks",
        lambda row: (
            f"{row.get('event')} / {row.get('type')} [{row.get('source')}] "
            f"{row.get('detail')}"
            + (f" matcher={row.get('matcher')}" if row.get("matcher") else "")
            + (" [blocks on failure]" if row.get("block_on_failure") else "")
        ),
    )
    return "\n".join(lines)


__all__ = [
    "MUTATING_COMMANDS",
    "READ_ONLY_COMMANDS",
    "build_capability_inventory",
    "command_behavior",
    "mcp_transport_preview",
    "render_capability_inventory",
    "schema_argument_preview",
    "validate_mcp_server",
]
