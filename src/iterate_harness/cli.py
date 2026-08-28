"""CLI entry point using typer."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Optional
from urllib.parse import urlparse

import typer

from iterate_harness import __version__
from iterate_harness.iterate.batch import DEFAULT_SCHEDULE_TIMEOUT_SECONDS
from iterate_harness.iterate.decision_log import DecisionLogEntry

log = logging.getLogger(__name__)

#: Set to "0" to disable the best-effort "new version available" hint on --version.
_UPDATE_CHECK_ENV_VAR = "ITERATE_HARNESS_UPDATE_CHECK"

_PREVIEW_STOPWORDS = {
    "a",
    "an",
    "and",
    "bug",
    "by",
    "fix",
    "for",
    "get",
    "help",
    "in",
    "of",
    "on",
    "or",
    "please",
    "show",
    "test",
    "that",
    "the",
    "this",
    "to",
    "with",
}


def _safe_short(text: str, *, limit: int = 140) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _schema_argument_preview(tool_schema: dict[str, object]) -> dict[str, object]:
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


def _mcp_transport_preview(config: object) -> dict[str, str]:
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


def _validate_mcp_server(name: str, config: object) -> dict[str, object]:
    preview = _mcp_transport_preview(config)
    issues: list[str] = []
    status = "ok"
    transport = preview["transport"]

    if transport == "stdio":
        command = getattr(config, "command", None) if not isinstance(config, dict) else config.get("command")
        raw_cwd = getattr(config, "cwd", None) if not isinstance(config, dict) else config.get("cwd")
        command_text = str(command or "").strip()
        if not command_text:
            issues.append("missing command")
        elif shutil.which(command_text) is None:
            issues.append(f"command not found in PATH: {command_text}")
        if raw_cwd:
            resolved_cwd = Path(str(raw_cwd)).expanduser()
            if not resolved_cwd.exists():
                issues.append(f"cwd does not exist: {resolved_cwd}")
    elif transport in {"http", "ws"}:
        raw_url = getattr(config, "url", None) if not isinstance(config, dict) else config.get("url")
        parsed = urlparse(str(raw_url or "").strip())
        expected = {"http", "https"} if transport == "http" else {"ws", "wss"}
        if parsed.scheme not in expected or not parsed.netloc:
            issues.append(f"invalid {transport} url: {raw_url}")

    if issues:
        status = "error"
    return {
        "name": name,
        **preview,
        "status": status,
        "issues": issues,
    }


def _dry_run_command_behavior(name: str) -> dict[str, str]:
    read_only = {
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
    mutating = {
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
    if name in read_only:
        return {
            "kind": "read_only",
            "detail": "This slash command mainly inspects current state and should not require a model turn.",
        }
    if name in mutating:
        return {
            "kind": "stateful",
            "detail": "This slash command can mutate local state, queue work, or trigger follow-up execution depending on its arguments.",
        }
    return {
        "kind": "unknown",
        "detail": "This slash command comes from a handler or plugin that dry-run cannot classify precisely.",
    }


def _tokenize_preview_text(text: str) -> list[str]:
    lowered = text.lower()
    ascii_tokens = re.findall(r"[a-z0-9_/-]+", lowered)
    cjk_tokens = [char for char in lowered if "\u4e00" <= char <= "\u9fff"]
    seen: set[str] = set()
    ordered: list[str] = []
    for token in [*ascii_tokens, *cjk_tokens]:
        normalized = token.strip("-_/")
        if len(normalized) < 2 and normalized not in cjk_tokens:
            continue
        if normalized in _PREVIEW_STOPWORDS:
            continue
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _score_candidate_match(prompt: str, *fields: str) -> tuple[int, list[str]]:
    prompt_lower = prompt.lower()
    prompt_tokens = _tokenize_preview_text(prompt)
    haystack = " ".join(field.lower() for field in fields if field).strip()
    if not haystack:
        return 0, []

    score = 0
    reasons: list[str] = []
    for token in prompt_tokens:
        if token in haystack:
            score += max(2, min(len(token), 8))
            if len(reasons) < 3:
                reasons.append(token)
    primary_name = fields[0].lower() if fields and fields[0] else ""
    if primary_name and primary_name in prompt_lower:
        score += 10
        if fields[0] not in reasons:
            reasons.insert(0, fields[0])
    return score, reasons[:3]


def _candidate_entry(name: str, description: str, *, score: int, reasons: list[str]) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "score": score,
        "reasons": reasons,
    }


def _recommend_preview_candidates(
    prompt: str | None,
    *,
    skills: list[object],
    tool_schemas: list[dict[str, object]],
    command_entries: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    if not prompt:
        return {"skills": [], "tools": [], "commands": []}
    stripped = prompt.strip()
    if not stripped or stripped.startswith("/"):
        return {"skills": [], "tools": [], "commands": []}

    skill_matches: list[dict[str, object]] = []
    for skill in skills:
        score, reasons = _score_candidate_match(
            stripped,
            str(getattr(skill, "name", "")),
            str(getattr(skill, "description", "")),
            str(getattr(skill, "content", ""))[:800],
        )
        if score >= 4:
            skill_matches.append(
                _candidate_entry(
                    str(getattr(skill, "name", "")),
                    str(getattr(skill, "description", "")),
                    score=score,
                    reasons=reasons,
                )
            )

    tool_matches: list[dict[str, object]] = []
    for tool in tool_schemas:
        optional = ", ".join(str(item) for item in tool.get("optional_args") or [])
        required = ", ".join(str(item) for item in tool.get("required_args") or [])
        score, reasons = _score_candidate_match(
            stripped,
            str(tool.get("name") or ""),
            str(tool.get("description") or ""),
            required,
            optional,
        )
        if score >= 4:
            tool_matches.append(
                _candidate_entry(
                    str(tool.get("name") or ""),
                    str(tool.get("description") or ""),
                    score=score,
                    reasons=reasons,
                )
            )

    command_matches: list[dict[str, object]] = []
    for command in command_entries:
        score, reasons = _score_candidate_match(
            stripped,
            str(command.get("name") or ""),
            str(command.get("description") or ""),
            str(command.get("behavior", {}).get("detail") or ""),
        )
        if score >= 8:
            command_matches.append(
                _candidate_entry(
                    str(command.get("name") or ""),
                    str(command.get("description") or ""),
                    score=score,
                    reasons=reasons,
                )
            )

    skill_matches.sort(key=lambda entry: (-int(entry["score"]), str(entry["name"])))
    tool_matches.sort(key=lambda entry: (-int(entry["score"]), str(entry["name"])))
    command_matches.sort(key=lambda entry: (-int(entry["score"]), str(entry["name"])))
    return {
        "skills": skill_matches[:5],
        "tools": tool_matches[:8],
        "commands": command_matches[:5],
    }


def _evaluate_dry_run_readiness(
    *,
    prompt: str | None,
    entrypoint: dict[str, object],
    validation: dict[str, object],
) -> dict[str, object]:
    level = "ready"
    reasons: list[str] = []
    next_actions: list[str] = []

    if entrypoint.get("kind") == "unknown_slash_command":
        level = "blocked"
        reasons.append("The prompt starts with '/' but does not match any registered slash command.")
        next_actions.append("Check the command name and run `ih --dry-run -p \"/help\"` to inspect available slash commands.")

    api_client = validation.get("api_client")
    if isinstance(api_client, dict) and api_client.get("status") == "error":
        if entrypoint.get("kind") == "model_prompt":
            level = "blocked"
            detail = str(api_client.get("detail") or "").strip()
            reasons.append(detail or "Runtime client resolution failed for a prompt that would require a model call.")
            next_actions.append("Fix authentication or provider profile configuration before running this prompt.")
        elif level != "blocked":
            level = "warning"
            reasons.append("Runtime client resolution failed. Interactive commands may still work, but model execution would fail.")
            next_actions.append("If you expect a model call later, fix authentication or provider profile configuration first.")

    mcp_errors = int(validation.get("mcp_errors") or 0)
    if mcp_errors > 0 and level != "blocked":
        level = "warning"
        reasons.append(f"{mcp_errors} configured MCP server(s) have obvious configuration errors.")
        next_actions.append("Fix or disable the broken MCP server configuration before relying on MCP-backed tools.")

    auth_status = str(validation.get("auth_status") or "")
    if auth_status.startswith("missing") and entrypoint.get("kind") in {"interactive_session", "model_prompt"} and level != "blocked":
        level = "warning"
        reasons.append("Authentication is missing, so live model execution would not start successfully.")
        next_actions.append("Run `ih auth login` or configure the active profile credentials before executing.")

    if not prompt and level == "ready":
        reasons.append("No prompt provided; dry-run only validated the session setup path.")
        next_actions.append("Provide `-p/--print` for a single prompt preview, or start `ih` normally to enter an interactive session.")
    elif level == "ready":
        reasons.append("Resolved configuration, prompt assembly, and static discovery checks all look usable.")
        if entrypoint.get("kind") == "slash_command":
            next_actions.append(f"You can run `ih -p \"{prompt}\"` directly.")
        elif entrypoint.get("kind") == "model_prompt":
            next_actions.append("You can run this prompt directly with `ih -p '...'` or open the interactive UI with `ih`.")
        else:
            next_actions.append("You can run IterateHarness normally with the current configuration.")

    deduped_actions: list[str] = []
    seen_actions: set[str] = set()
    for action in next_actions:
        normalized = action.strip()
        if not normalized or normalized in seen_actions:
            continue
        seen_actions.add(normalized)
        deduped_actions.append(normalized)

    return {"level": level, "reasons": reasons, "next_actions": deduped_actions}


def _build_dry_run_preview(
    *,
    prompt: str | None,
    cwd: str,
    model: str | None,
    max_turns: int | None,
    base_url: str | None,
    system_prompt: str | None,
    append_system_prompt: str | None,
    api_key: str | None,
    api_format: str | None,
    permission_mode: str | None,
) -> dict[str, object]:
    from iterate_harness.api.provider import auth_status, detect_provider
    from iterate_harness.commands import create_default_command_registry
    from iterate_harness.config import get_config_file_path, load_settings
    from iterate_harness.mcp.config import load_mcp_server_configs
    from iterate_harness.plugins import load_plugins
    from iterate_harness.prompts.context import build_runtime_system_prompt
    from iterate_harness.skills import load_skill_registry
    from iterate_harness.tools import create_default_tool_registry
    from iterate_harness.ui.runtime import _resolve_api_client_from_settings

    resolved_cwd = str(Path(cwd).expanduser().resolve())
    settings = load_settings().merge_cli_overrides(
        model=model,
        max_turns=max_turns,
        base_url=base_url,
        system_prompt=system_prompt,
        api_key=api_key,
        api_format=api_format,
        permission_mode=permission_mode,
    )
    provider = detect_provider(settings)
    auth = auth_status(settings)
    profile_name, profile = settings.resolve_profile()

    plugins = load_plugins(settings, resolved_cwd)
    plugin_commands = [
        command
        for plugin in plugins
        if plugin.enabled
        for command in plugin.commands
    ]
    command_registry = create_default_command_registry(plugin_commands=plugin_commands)
    command_match = command_registry.lookup(prompt) if prompt else None
    skill_registry = load_skill_registry(resolved_cwd, settings=settings)
    skills = skill_registry.list_skills()
    mcp_servers = load_mcp_server_configs(settings, plugins)
    tool_registry = create_default_tool_registry()
    tool_schemas = []
    for tool_schema in tool_registry.to_api_schema():
        args_preview = _schema_argument_preview(tool_schema)
        tool_schemas.append(
            {
                "name": str(tool_schema.get("name") or ""),
                "description": str(tool_schema.get("description") or ""),
                **args_preview,
            }
        )

    client_validation = {"status": "ok", "detail": ""}
    try:
        with redirect_stderr(StringIO()):
            _resolve_api_client_from_settings(settings)
    except SystemExit:
        client_validation = {"status": "error", "detail": "runtime client could not be resolved with current auth/config"}
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        client_validation = {"status": "error", "detail": str(exc)}

    preview_prompt = prompt.strip() if prompt else None
    prompt_seed = preview_prompt
    if append_system_prompt:
        appended = append_system_prompt.strip()
        if appended:
            existing = settings.system_prompt or ""
            settings = settings.model_copy(update={"system_prompt": f"{existing}\n\n{appended}".strip()})
    system_prompt_text = build_runtime_system_prompt(
        settings,
        cwd=resolved_cwd,
        latest_user_prompt=prompt_seed,
    )

    command_entries = []
    for command in command_registry.list_commands():
        behavior = _dry_run_command_behavior(command.name)
        command_entries.append(
            {
                "name": command.name,
                "description": command.description,
                "remote_invocable": command.remote_invocable,
                "remote_admin_opt_in": command.remote_admin_opt_in,
                "behavior": behavior,
            }
        )

    recommendations = _recommend_preview_candidates(
        preview_prompt,
        skills=skills,
        tool_schemas=tool_schemas,
        command_entries=command_entries,
    )

    if preview_prompt:
        if preview_prompt.startswith("/") and command_match is not None:
            matched_command = command_match[0]
            behavior = _dry_run_command_behavior(matched_command.name)
            entrypoint = {
                "kind": "slash_command",
                "command": matched_command.name,
                "args": command_match[1],
                "description": matched_command.description,
                "remote_invocable": matched_command.remote_invocable,
                "remote_admin_opt_in": matched_command.remote_admin_opt_in,
                "behavior": behavior["kind"],
                "detail": (
                    f"Input resolves to /{matched_command.name}. "
                    f"{behavior['detail']} Dry-run does not execute the command handler."
                ),
            }
        elif preview_prompt.startswith("/") and command_match is None:
            entrypoint = {
                "kind": "unknown_slash_command",
                "detail": "Input starts with / but does not match a registered slash command.",
            }
        else:
            entrypoint = {
                "kind": "model_prompt",
                "detail": (
                    "The first live step would be a model request. "
                    "Exact tool calls and parameters are decided by the model at runtime."
                ),
            }
    else:
        entrypoint = {
            "kind": "interactive_session",
            "detail": "IterateHarness would start and wait for user input. No model or tool call happens until you submit one.",
        }

    preview = {
        "mode": "dry-run",
        "cwd": resolved_cwd,
        "config_path": str(get_config_file_path()),
        "prompt": preview_prompt,
        "prompt_preview": _safe_short(preview_prompt or "", limit=220) if preview_prompt else "",
        "settings": {
            "active_profile": profile_name,
            "profile_label": profile.label,
            "provider": provider.name,
            "api_format": settings.api_format,
            "model": settings.model,
            "base_url": settings.base_url or "",
            "permission_mode": settings.permission.mode.value,
            "max_turns": settings.max_turns,
            "effort": settings.effort,
            "passes": settings.passes,
        },
        "validation": {
            "auth_status": auth,
            "api_client": client_validation,
            "system_prompt_chars": len(system_prompt_text),
            "mcp_validation": "skipped in dry-run (configured only; external servers are not started)",
        },
        "entrypoint": entrypoint,
        "commands": command_entries,
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "source": skill.source,
            }
            for skill in skills
        ],
        "tools": tool_schemas,
        "recommendations": recommendations,
        "plugins": [
            {
                "name": plugin.manifest.name,
                "enabled": plugin.enabled,
                "skills": len(plugin.skills),
                "commands": len(plugin.commands),
                "agents": len(plugin.agents),
                "mcp_servers": len(plugin.mcp_servers),
            }
            for plugin in plugins
        ],
        "mcp_servers": [
            _validate_mcp_server(name, config)
            for name, config in sorted(mcp_servers.items())
        ],
        "system_prompt_preview": _safe_short(system_prompt_text, limit=600),
    }
    mcp_errors = sum(1 for entry in preview["mcp_servers"] if entry.get("status") == "error")
    preview["validation"]["mcp_errors"] = mcp_errors
    preview["readiness"] = _evaluate_dry_run_readiness(
        prompt=preview_prompt,
        entrypoint=preview["entrypoint"],
        validation=preview["validation"],
    )
    return preview


def _format_dry_run_preview(preview: dict[str, object]) -> str:
    settings = preview.get("settings") if isinstance(preview.get("settings"), dict) else {}
    validation = preview.get("validation") if isinstance(preview.get("validation"), dict) else {}
    entrypoint = preview.get("entrypoint") if isinstance(preview.get("entrypoint"), dict) else {}
    readiness = preview.get("readiness") if isinstance(preview.get("readiness"), dict) else {}
    recommendations = preview.get("recommendations") if isinstance(preview.get("recommendations"), dict) else {}
    plugins = preview.get("plugins") if isinstance(preview.get("plugins"), list) else []
    skills = preview.get("skills") if isinstance(preview.get("skills"), list) else []
    commands = preview.get("commands") if isinstance(preview.get("commands"), list) else []
    tools = preview.get("tools") if isinstance(preview.get("tools"), list) else []
    mcp_servers = preview.get("mcp_servers") if isinstance(preview.get("mcp_servers"), list) else []

    lines = [
        "IterateHarness Dry Run",
        "",
        "Readiness",
        f"- level: {readiness.get('level', 'unknown')}",
    ]
    readiness_reasons = readiness.get("reasons")
    if isinstance(readiness_reasons, list):
        for reason in readiness_reasons[:4]:
            lines.append(f"- {reason}")
    readiness_actions = readiness.get("next_actions")
    if isinstance(readiness_actions, list) and readiness_actions:
        lines.append("- next actions:")
        for action in readiness_actions[:4]:
            lines.append(f"  - {action}")
    lines.extend(
        [
        "",
        "Execution",
        f"- cwd: {preview.get('cwd')}",
        f"- prompt: {preview.get('prompt_preview') or '(none)'}",
        f"- entrypoint: {entrypoint.get('kind', 'unknown')}",
        f"- detail: {entrypoint.get('detail', '')}",
        "",
        "Resolved Settings",
        f"- profile: {settings.get('active_profile')} ({settings.get('profile_label')})",
        f"- provider: {settings.get('provider')}",
        f"- api_format: {settings.get('api_format')}",
        f"- model: {settings.get('model')}",
        f"- base_url: {settings.get('base_url') or '(default)'}",
        f"- permission_mode: {settings.get('permission_mode')}",
        f"- max_turns: {settings.get('max_turns')}",
        f"- effort: {settings.get('effort')} / passes={settings.get('passes')}",
        "",
        "Validation",
        f"- auth: {validation.get('auth_status')}",
        f"- api client: {validation.get('api_client', {}).get('status', 'unknown')}",
        f"- system prompt chars: {validation.get('system_prompt_chars')}",
        f"- mcp: {validation.get('mcp_validation')}",
        f"- mcp config errors: {validation.get('mcp_errors', 0)}",
        "",
        "Discovery",
        f"- plugins: {len(plugins)}",
        f"- skills: {len(skills)}",
        f"- slash commands: {len(commands)}",
        f"- built-in tools: {len(tools)}",
        f"- configured mcp servers: {len(mcp_servers)}",
        ]
    )

    if mcp_servers:
        lines.extend(["", "Configured MCP"])
        for entry in mcp_servers[:8]:
            status = entry.get("status") or "unknown"
            suffix = ""
            issues = entry.get("issues")
            if isinstance(issues, list) and issues:
                suffix = f" [{'; '.join(str(item) for item in issues)}]"
            lines.append(
                f"- {entry.get('name')}: {entry.get('transport')} -> {entry.get('target')} ({status}){suffix}"
            )
        if len(mcp_servers) > 8:
            lines.append(f"- ... (+{len(mcp_servers) - 8} more)")

    if tools:
        lines.extend(["", "Available Tools"])
        for entry in tools[:12]:
            required = entry.get("required_args") or []
            optional = entry.get("optional_args") or []
            signature_parts: list[str] = []
            if required:
                signature_parts.append("required: " + ", ".join(required))
            if optional:
                signature_parts.append("optional: " + ", ".join(optional[:4]))
            suffix = f" ({'; '.join(signature_parts)})" if signature_parts else ""
            lines.append(f"- {entry.get('name')}{suffix}")
        if len(tools) > 12:
            lines.append(f"- ... (+{len(tools) - 12} more)")

    if skills:
        lines.extend(["", "Available Skills"])
        for entry in skills[:8]:
            lines.append(f"- {entry.get('name')}: {_safe_short(str(entry.get('description') or ''), limit=100)}")
        if len(skills) > 8:
            lines.append(f"- ... (+{len(skills) - 8} more)")

    recommended_skills = recommendations.get("skills") if isinstance(recommendations.get("skills"), list) else []
    recommended_tools = recommendations.get("tools") if isinstance(recommendations.get("tools"), list) else []
    recommended_commands = recommendations.get("commands") if isinstance(recommendations.get("commands"), list) else []
    if recommended_skills or recommended_tools or recommended_commands:
        lines.extend(["", "Likely Matches"])
        if recommended_skills:
            lines.append("- skills:")
            for entry in recommended_skills[:4]:
                reasons = ", ".join(str(item) for item in entry.get("reasons") or [])
                suffix = f" [{reasons}]" if reasons else ""
                lines.append(f"  - {entry.get('name')} (score={entry.get('score')}){suffix}")
        if recommended_tools:
            lines.append("- tools:")
            for entry in recommended_tools[:6]:
                reasons = ", ".join(str(item) for item in entry.get("reasons") or [])
                suffix = f" [{reasons}]" if reasons else ""
                lines.append(f"  - {entry.get('name')} (score={entry.get('score')}){suffix}")
        if recommended_commands:
            lines.append("- slash commands:")
            for entry in recommended_commands[:4]:
                reasons = ", ".join(str(item) for item in entry.get("reasons") or [])
                suffix = f" [{reasons}]" if reasons else ""
                lines.append(f"  - /{entry.get('name')} (score={entry.get('score')}){suffix}")

    if entrypoint.get("kind") == "slash_command":
        lines.extend(
            [
                "",
                "Slash Command Detail",
                f"- command: /{entrypoint.get('command')}",
                f"- description: {entrypoint.get('description')}",
                f"- behavior: {entrypoint.get('behavior')}",
                f"- remote_invocable: {entrypoint.get('remote_invocable')}",
                f"- remote_admin_opt_in: {entrypoint.get('remote_admin_opt_in')}",
            ]
        )
        args = str(entrypoint.get("args") or "").strip()
        if args:
            lines.append(f"- args: {args}")

    preview_text = str(preview.get("system_prompt_preview") or "").strip()
    if preview_text:
        lines.extend(["", "System Prompt Preview", preview_text])

    return "\n".join(lines)


def _version_callback(value: bool) -> None:
    if value:
        print(f"iterate_harness {__version__}")
        if os.environ.get(_UPDATE_CHECK_ENV_VAR, "1") == "1":
            from iterate_harness.update import maybe_print_update_hint

            maybe_print_update_hint(current=__version__)
        raise typer.Exit()


app = typer.Typer(
    name="iterate_harness",
    help=(
        "iterate — the multi-round review & fix harness for AI coding workflows.\n\n"
        "Starts an interactive session by default, use -p/--print for non-interactive output."
    ),
    add_completion=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

auth_app = typer.Typer(name="auth", help="Manage authentication")
provider_app = typer.Typer(name="provider", help="Manage provider profiles")
iterate_app = typer.Typer(name="iterate", help="Iterate review/fix loop (init/review/run/resume/log/report)")
web_app = typer.Typer(name="web", help="WebUI management console (design §17)")

app.add_typer(auth_app)
app.add_typer(provider_app)
app.add_typer(iterate_app)
app.add_typer(web_app)


# ---- iterate subcommands ----

#: Timeout for the ``--branch`` git operations (checkout / worktree add).
_BRANCH_GIT_TIMEOUT_SECONDS = 60


def _current_git_branch(cwd: str | Path) -> str | None:
    """Return the current branch name, or ``None`` outside a repo / detached."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_BRANCH_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    branch = proc.stdout.strip()
    return branch if branch and branch != "HEAD" else None


def _ensure_review_branch(branch: str) -> str | None:
    """Switch to ``branch`` before a review/run when it differs from the current one.

    Prefers a plain ``git checkout``; when the working tree is dirty (checkout
    fails) it falls back to an isolated ``git worktree add`` and chdirs into
    it. Returns the previous working directory when the cwd changed (caller
    must restore it in ``finally``), otherwise ``None``.
    """
    import os
    import subprocess
    import tempfile

    if not branch:
        return None
    cwd = Path.cwd()
    current = _current_git_branch(cwd)
    if current and current == branch:
        print(f"Already on target branch {branch} — continuing.")
        return None

    checkout = subprocess.run(
        ["git", "checkout", branch],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_BRANCH_GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if checkout.returncode == 0:
        print(f"Switched to branch {branch} (was {current or 'HEAD'}) for this run.")
        return None

    # Plain checkout failed (dirty tree, local changes, ...) — review from an
    # isolated linked worktree instead, then restore the cwd afterwards.
    worktree_path = Path(tempfile.mkdtemp(prefix="iterate-branch-")) / "worktree"
    worktree_add = subprocess.run(
        ["git", "worktree", "add", str(worktree_path), branch],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_BRANCH_GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if worktree_add.returncode != 0:
        print(
            f"Could not switch to branch {branch} (both `git checkout` and "
            f"`git worktree add` failed): {worktree_add.stderr.strip()}",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    os.chdir(worktree_path)
    print(f"Created isolated worktree for branch {branch} at {worktree_path} — reviewing there.")
    return str(cwd)


def _run_headless(kickoff: str, branch: str | None = None) -> None:
    """Run one canonical iterate prompt through the kernel print pipeline."""
    import asyncio

    from iterate_harness.iterate.onboard_cmd import (
        ensure_onboarding_fingerprints,
        warn_if_drifted,
    )
    from iterate_harness.ui.app import run_print_mode

    if branch:
        kickoff = (
            f"{kickoff}\n\nTarget branch: `{branch}` — review the repository "
            "as checked out on this branch."
        )
    ensure_onboarding_fingerprints(Path.cwd())
    warn_if_drifted(Path.cwd())
    asyncio.run(run_print_mode(prompt=kickoff, permission_mode="full_auto"))
    print(
        "\nRun finished. "
        "View the report with `ih iterate report --html --serve` "
        "(or `ih iterate log --tail 40` for the decision log).",
        file=sys.stderr,
    )


def _resolve_changed_files(
    changed: bool, ref: str, clean_ok: bool = False
) -> list[str] | None:
    """Collect the changed-file delta for ``--changed``; exit early when empty.

    ``clean_ok`` (scheduled runs) turns "no changes" into a graceful exit 0
    instead of a failure, keeping cron history free of noise.
    """
    from iterate_harness.iterate import git_scope

    if not changed:
        return None
    try:
        files = git_scope.collect_changed_files(str(Path.cwd()), ref)
    except ValueError as exc:
        print(f"Invalid --ref: {exc}", file=sys.stderr)
        raise typer.Exit(1) from exc
    if not files:
        print(f"No changed files vs {ref} (clean tree / not a git repo).")
        raise typer.Exit(0 if clean_ok else 1)
    print(f"Changed-only quick review: {len(files)} file(s) vs {ref}")
    return files


@iterate_app.command("onboard")
def iterate_onboard(
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept suggestions without prompting"),
    goal: str = typer.Option("", "--goal", help="Review goal (default: detection-based suggestion)"),
    no_ai: bool = typer.Option(
        False,
        "--no-ai",
        help="Skip the model scan; render a detection-only knowledge base (channel=cli)",
    ),
) -> None:
    """Full onboarding: model-driven project scan -> ITERATE.md + config + fingerprints."""
    from iterate_harness.iterate.onboard_cmd import run_onboard

    raise typer.Exit(run_onboard(yes=yes, goal=goal, no_ai=no_ai))


@iterate_app.command("refresh")
def iterate_refresh() -> None:
    """Re-capture manifest fingerprints, report drift, refresh metadata (no model)."""
    from iterate_harness.iterate.onboard_cmd import run_refresh

    raise typer.Exit(run_refresh())


@iterate_app.command("reonboard")
def iterate_reonboard(
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept suggestions without prompting"),
    goal: str = typer.Option("", "--goal", help="Review goal (default: detection-based suggestion)"),
    no_ai: bool = typer.Option(False, "--no-ai", help="Detection-only re-scan (no model call)"),
) -> None:
    """Backup artifacts, re-run the full onboarding, preserve the user region verbatim."""
    from iterate_harness.iterate.onboard_cmd import run_reonboard

    raise typer.Exit(run_reonboard(yes=yes, goal=goal, no_ai=no_ai))


@iterate_app.command("personalize")
def iterate_personalize() -> None:
    """Interactive 9-category personalization wizard (config + ITERATE.md dual write)."""
    from iterate_harness.iterate.personalize_cmd import run_personalize

    raise typer.Exit(run_personalize())


@iterate_app.command("status")
def iterate_status() -> None:
    """Show the effective iterate config, onboarding state, and drift status."""
    from iterate_harness.iterate.config_loader import load_effective_config
    from iterate_harness.iterate.onboard_cmd import render_status_onboarding_lines

    effective = load_effective_config(str(Path.cwd()))
    config = effective.config
    print(f"Config source: {effective.source}")
    print(f"Goal: {config.goal}")
    print(f"Max rounds: {config.max_rounds}")
    print(f"Dimensions: {', '.join(config.dimensions)}")
    commands = config.validation.commands if config.validation else {}
    if commands:
        print("Validation commands:")
        for module, cmds in commands.items():
            print(f"  - {module}: {'; '.join(cmds)}")
    else:
        print("Validation commands: (none configured)")
    for line in render_status_onboarding_lines(Path.cwd()):
        print(line)


@iterate_app.command("doctor")
def iterate_doctor() -> None:
    """Check skill↔harness dimension-system consistency (exit 1 on drift)."""
    from iterate_harness.iterate.dimension_check import (
        render_doctor_report,
        run_dimension_doctor,
    )

    report = run_dimension_doctor(str(Path.cwd()))
    print(render_doctor_report(report))
    if not report.ok:
        raise typer.Exit(1)


@iterate_app.command("init")
def iterate_init(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Accept all suggestions and write without prompting"
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing iterate.config.yaml"
    ),
    goal: str = typer.Option(
        "", "--goal", help="Review goal (default: detection-based suggestion)"
    ),
) -> None:
    """Detection-driven config wizard: probe the project, suggest, write."""
    from iterate_harness.iterate import init_wizard

    cwd = Path.cwd()
    config_path = init_wizard.existing_config_path(cwd)
    if config_path.exists() and not force:
        print(f"{init_wizard.CONFIG_FILENAME} already exists ({config_path}); use --force to overwrite.")
        raise typer.Exit(1)

    profile = init_wizard.detect_project(cwd)
    print(f"Detected stack: {', '.join(profile.languages) if profile.languages else 'unknown'}")
    for line in profile.evidence:
        print(f"  - {line}")
    print(f"Suggested test command: {profile.test_command or '(none — configure manually)'}")

    offered = profile.suggested_dimensions
    print("Suggested dimensions:")
    for index, dim in enumerate(offered, 1):
        print(f"  {index}. {dim}")
    chosen: list[str] | None = offered
    final_goal = goal.strip() or (
        f"Iterative review for this {profile.languages[0] if profile.languages else 'software'} project"
    )
    rounds = 3
    if not yes:
        raw = typer.prompt(
            "Dimensions to keep (comma-separated numbers/names, empty = all)", default=""
        )
        chosen = init_wizard.parse_dimension_selection(raw, offered)
        while chosen is None:
            print("Invalid selection — use numbers or exact dimension names.")
            raw = typer.prompt("Dimensions to keep (empty = all)", default="")
            chosen = init_wizard.parse_dimension_selection(raw, offered)
        if not goal.strip():
            final_goal = typer.prompt("Review goal", default=final_goal)
        rounds = typer.prompt("Max review rounds", default=3, type=int)

    config = init_wizard.build_config_dict(
        goal=final_goal, dimensions=chosen, max_rounds=rounds, test_command=profile.test_command
    )
    print("\n--- iterate.config.yaml (preview) ---")
    print(init_wizard.render_config_text(config), end="")
    print("--- end preview ---\n")

    if yes or typer.confirm(f"Write {config_path}"):
        written = init_wizard.write_config(cwd, config)
        print(f"Wrote {written}")
        print("Next: `ih iterate review --changed` for a quick changed-only review.")
    else:
        print("Aborted — nothing written.")


@iterate_app.command("review")
def iterate_review(
    rounds: int = typer.Option(3, "--rounds", min=1, max=20, help="Review round cap"),
    changed: bool = typer.Option(
        False, "--changed", help="Quick review: only files changed vs --ref (default HEAD)"
    ),
    ref: str = typer.Option("HEAD", "--ref", help="Git ref used as the --changed baseline"),
    clean_ok: bool = typer.Option(
        False, "--clean-ok", help="Exit 0 when there are no changes (scheduled runs)"
    ),
    branch: str = typer.Option("", "--branch", help="Target branch for the review (creates worktree if different from current)"),
) -> None:
    """Dry-run pure review: multi-round convergence, read-only, audited report."""
    from iterate_harness.iterate import prompts as iterate_prompts
    from iterate_harness.iterate.config_loader import load_effective_config

    prev_cwd = _ensure_review_branch(branch) if branch else None
    try:
        effective_goal = load_effective_config(str(Path.cwd())).config.goal
        changed_files = _resolve_changed_files(changed, ref, clean_ok)
        _run_headless(
            iterate_prompts.dry_run_kickoff(effective_goal, rounds, changed_files, cwd=str(Path.cwd())),
            branch=branch or None,
        )
    finally:
        if prev_cwd is not None:
            import os
            os.chdir(prev_cwd)


@iterate_app.command("run")
def iterate_run(
    rounds: int = typer.Option(3, "--rounds", min=1, max=20, help="Loop round cap"),
    changed: bool = typer.Option(
        False, "--changed", help="Quick loop: only files changed vs --ref (default HEAD)"
    ),
    ref: str = typer.Option("HEAD", "--ref", help="Git ref used as the --changed baseline"),
    clean_ok: bool = typer.Option(
        False, "--clean-ok", help="Exit 0 when there are no changes (scheduled runs)"
    ),
    branch: str = typer.Option("", "--branch", help="Target branch for the review (creates worktree if different from current)"),
) -> None:
    """Autonomous loop: review -> fix atomic findings -> validate -> repeat."""
    from iterate_harness.iterate import prompts as iterate_prompts
    from iterate_harness.iterate.config_loader import load_effective_config

    prev_cwd = _ensure_review_branch(branch) if branch else None
    try:
        effective_goal = load_effective_config(str(Path.cwd())).config.goal
        changed_files = _resolve_changed_files(changed, ref, clean_ok)
        _run_headless(
            iterate_prompts.normal_kickoff(effective_goal, rounds, changed_files, cwd=str(Path.cwd())),
            branch=branch or None,
        )
    finally:
        if prev_cwd is not None:
            import os
            os.chdir(prev_cwd)


@iterate_app.command("batch")
def iterate_batch_review(
    repos: list[str] = typer.Argument(..., help="Repository paths (reviewed sequentially)"),
    ref: str = typer.Option("HEAD", "--ref", help="Git ref used as the --changed baseline"),
    rounds: int = typer.Option(3, "--rounds", min=1, max=20, help="Review round cap"),
    full: bool = typer.Option(False, "--full", help="Full-codebase review (skip changed-only)"),
    mode: str = typer.Option("dry-run", "--mode", help="dry-run|normal"),
    as_json: bool = typer.Option(False, "--json", help="Emit the ranking as JSON"),
) -> None:
    """Review multiple repos sequentially and rank them by findings (worst first)."""
    import asyncio

    from iterate_harness.iterate import batch as iterate_batch

    if mode not in ("dry-run", "normal"):
        raise typer.BadParameter("mode must be dry-run|normal")
    records = asyncio.run(
        iterate_batch.run_batch(repos=repos, ref=ref, rounds=rounds, full=full, mode=mode)
    )
    if as_json:
        print(json.dumps(iterate_batch.rank_records(records), ensure_ascii=False, indent=2))
    else:
        print(iterate_batch.render_ranking(records))


@iterate_app.command("resume")
def iterate_resume(
    session_id: str = typer.Option("", "--session", help="Session id (default: most recent)"),
) -> None:
    """Resume a previous session and continue the iterate loop in the REPL."""
    import asyncio

    from iterate_harness.services.session_storage import (
        list_session_snapshots,
        load_session_by_id,
        load_session_snapshot,
    )
    from iterate_harness.ui.app import run_repl

    cwd = str(Path.cwd())
    session_data = None
    if session_id:
        session_data = load_session_by_id(cwd, session_id)
        if session_data is None:
            print(f"Session not found: {session_id}", file=sys.stderr)
            raise typer.Exit(1)
    else:
        session_data = load_session_snapshot(cwd) or (list_session_snapshots(cwd, limit=1) or [None])[0]
        if session_data is None:
            print("No previous session found in this directory.", file=sys.stderr)
            raise typer.Exit(1)
    print(f"Resuming session: {session_data.get('summary', '(untitled)')[:60]}")
    asyncio.run(
        run_repl(
            prompt=None,
            cwd=cwd,
            model=session_data.get("model"),
            restore_messages=session_data.get("messages"),
            restore_tool_metadata=session_data.get("tool_metadata"),
        )
    )


@iterate_app.command("log")
def iterate_log(
    tail: int = typer.Option(20, "--tail", min=1, help="Show last N entries"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
    trend: bool = typer.Option(
        False, "--trend", help="Show the cross-run finding trend summary instead of entries"
    ),
    replay: bool = typer.Option(
        False, "--replay", help="Replay the whole run chronologically (relative timestamps)"
    ),
) -> None:
    """View the append-only iterate decision log."""
    from iterate_harness.iterate import decision_log as iter_log
    from iterate_harness.iterate import replay as replay_mod
    from iterate_harness.iterate import trend_store

    if trend:
        summary = trend_store.summarize(str(Path.cwd()))
        if as_json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(trend_store.render_trend_summary(summary))
        return

    entries = iter_log.read_entries(str(Path.cwd()))
    if replay:
        print(replay_mod.render_replay(entries))
        return
    if as_json:
        print(json.dumps([e.__dict__ for e in entries], ensure_ascii=False, indent=2))
        return
    if not entries:
        print("Decision log is empty (.iterate/decision-log.jsonl).")
        return
    for entry in entries[-tail:]:
        data = json.dumps(entry.data, ensure_ascii=False) if entry.data else ""
        print(f"[{entry.timestamp}] r{entry.round} {entry.type} {data}".rstrip())
    print(f"({len(entries)} entries total)")


@iterate_app.command("report")
def iterate_report(
    github: bool = typer.Option(
        False, "--github", help="Emit GitHub Actions workflow commands (PR annotations)"
    ),
    pr: bool = typer.Option(
        False, "--pr", help="Post/update the report as a PR comment via the gh CLI (degrades gracefully)"
    ),
    html_out: str = typer.Option(
        "",
        "--html",
        help="Write a self-contained single-file HTML report (path or '-' for default .iterate/report.html)",
    ),
    serve: bool = typer.Option(
        False,
        "--serve",
        help="Serve the HTML report + replay page on a local HTTP server (opens the browser)",
    ),
    serve_port: int = typer.Option(
        0, "--serve-port", help="Port for --serve (default: OS-assigned ephemeral port)"
    ),
    persist: bool = typer.Option(
        False,
        "--serve-persist",
        help="Keep the --serve server running until Ctrl+C (default: stop after one request)",
    ),
    fail_on: str = typer.Option(
        "high",
        "--fail-on",
        help="Severity gate for the exit code: none|low|medium|high|critical",
    ),
    lang: str = typer.Option(
        "",
        "--lang",
        help="Report language: en|zh (default: config.language or 'en'; --lang overrides config)",
    ),
    csv_out: str = typer.Option(
        "",
        "--csv",
        help="Write findings as CSV (path or '-' for .iterate/report.csv)",
    ),
) -> None:
    """Render the final iterate report from the decision log (CI mode).

    Exit code is 1 when any finding is at or above --fail-on severity;
    a missing or malformed report degrades to an empty report (exit 0).
    Use --html --serve to view the report + round-replay in a browser.
    """
    # Normalize typer OptionInfo defaults so direct Python calls (tests /
    # embedding) behave like the CLI: OptionInfo is always truthy, which
    # would otherwise make every boolean flag evaluate True.
    serve = _typer_flag(serve)
    persist = _typer_flag(persist)
    serve_port = _typer_int(serve_port)
    lang = _typer_str(lang)
    csv_out = _typer_str(csv_out)

    from iterate_harness.iterate import ci_report, html_report, pr_comment, report_server
    from iterate_harness.iterate import decision_log as iter_log

    # Resolve the effective report language: explicit --lang wins over the
    # project config's `language` field; both fall back to English.
    language = lang
    if language == "":
        try:
            from iterate_harness.iterate.config_loader import load_effective_config

            effective = load_effective_config(str(Path.cwd()))
            language = effective.config.language
        except Exception:
            language = ""
    language = (language or "en").strip().lower()
    if language not in ci_report.SUPPORTED_LANGUAGES:
        allowed = "|".join(ci_report.SUPPORTED_LANGUAGES)
        raise typer.BadParameter(f"must be one of: {allowed} (got '{lang}')")

    threshold = fail_on.strip().lower()
    if threshold not in ci_report.SEVERITY_ORDER:
        allowed = "|".join(ci_report.SEVERITY_ORDER)
        raise typer.BadParameter(f"must be one of: {allowed} (got '{fail_on}')")

    entries = iter_log.read_entries(str(Path.cwd()))
    report_entry = ci_report.latest_report_entry(entries)
    if report_entry is None:
        print(
            "No report entry in the decision log yet "
            "(run `ih iterate review` or `ih iterate run` first).",
            file=sys.stderr,
        )
    summary = ci_report.ReportSummary.from_entry(report_entry)
    gate = ci_report.threshold_gate(report_entry)

    if html_out:
        _write_html_report(html_report, entries, html_out)
        if serve:
            _write_html_replay(html_report, entries)
            report_server.serve_report(
                Path.cwd() / ".iterate",
                port=serve_port,
                oneshot=not persist,
                open_browser=True,
            )

    if github:
        print(ci_report.render_github(summary))
    if pr:
        body = pr_comment.render_markdown(summary, gate)
        result = pr_comment.post_pr_comment(body, str(Path.cwd()))
        print(f"PR comment: {result}", file=sys.stderr)
    if not github and not pr:
        print(ci_report.render_text(summary, gate, language=language))
    if csv_out:
        csv_path = csv_out if csv_out != "-" else str(Path.cwd() / ".iterate" / "report.csv")
        result = ci_report.render_csv(summary, csv_path)
        print(f"CSV report: {result}", file=sys.stderr)
    exit_code = max(
        ci_report.severity_gate(summary, threshold),
        ci_report.threshold_exit_code(gate),
    )
    raise typer.Exit(exit_code)


# ---- unattended automation subcommands (schedule / hook / cron) ----

@iterate_app.command("schedule")
def iterate_schedule(
    action: str = typer.Argument(
        ..., help="Action: add|remove|status (add upserts the quick-review cron job)"
    ),
    cron: str = typer.Option(
        "", "--cron", help="5-field cron expression, e.g. '0 9 * * 1-5' (required for add)"
    ),
    ref: str = typer.Option("HEAD", "--ref", help="Git ref used as the --changed baseline"),
    rounds: int = typer.Option(
        3, "--rounds", min=1, max=20, help="Quick-review round cap for scheduled runs"
    ),
    mode: str = typer.Option(
        "dry-run", "--mode", help="Dry-run|normal (normal applies AI fixes automatically)"
    ),
    timeout: int = typer.Option(
        DEFAULT_SCHEDULE_TIMEOUT_SECONDS,
        "--timeout",
        min=60,
        help="Max seconds a scheduled run may take before it is killed",
    ),
    timezone: str = typer.Option(
        None,
        "--timezone",
        help="IANA timezone for evaluating the cron expression (default: UTC)",
    ),
) -> None:
    """Manage the unattended scheduled quick-review cron job. [schedule]"""
    from iterate_harness.iterate import batch as iterate_batch_mod

    action = action.strip().lower()
    if action not in ("add", "remove", "status"):
        raise typer.BadParameter("action must be add|remove|status")

    if action == "add":
        if not cron.strip():
            raise typer.BadParameter("--cron is required for 'schedule add'")
        try:
            job = iterate_batch_mod.install_schedule(
                cwd=str(Path.cwd()),
                schedule=cron,
                ref=ref,
                rounds=rounds,
                mode=mode,
                timeout=timeout,
                timezone=timezone or None,
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        print(
            f"Scheduled job {job['name']}: {job['schedule']} -> {job['command']}"
            f" (cwd={job['cwd']}, timeout={job['timeout']}s)"
        )
    elif action == "remove":
        removed = iterate_batch_mod.remove_schedule()
        print("Scheduled quick-review job removed." if removed else "No scheduled quick-review job found.")
    else:  # status
        status = iterate_batch_mod.schedule_status()
        if status is None or status.get("job") is None:
            print("No scheduled quick-review job. Use `ih iterate schedule add --cron '...'`.")
        else:
            job = status["job"]
            last = status.get("lastRun")
            print(f"Job:        {job['name']}")
            print(f"Schedule:   {job['schedule']}" + (f" ({job['timezone']})" if job.get("timezone") else ""))
            print(f"Command:    {job['command']}")
            print(f"CWD:        {job['cwd']}")
            print(f"Timeout:    {job['timeout']}s")
            if last:
                print(
                    f"Last run:   {last.get('finished_at', last.get('started_at', '?'))} "
                    f"(status={last.get('status', '?')})"
                )
            else:
                print("Last run:   never")


@iterate_app.command("hook")
def iterate_hook(
    action: str = typer.Argument(
        ..., help="Action: install|uninstall|status (git pre-commit auto-review hook)"
    ),
    fail_on: str = typer.Option(
        "low", "--fail-on", help="Block commit at severity: none|low|medium|high|critical"
    ),
) -> None:
    """Manage the git pre-commit hook that gate-keeps commits on findings. [hook]"""
    from iterate_harness.iterate import git_hook

    action = action.strip().lower()
    if action not in ("install", "uninstall", "status"):
        raise typer.BadParameter("action must be install|uninstall|status")

    try:
        if action == "install":
            target = git_hook.install_hook(Path.cwd(), fail_on=fail_on)
            print(f"Pre-commit hook installed: {target}")
        elif action == "uninstall":
            removed = git_hook.uninstall_hook(Path.cwd())
            print("Pre-commit hook removed." if removed else "No managed hook to remove.")
        else:  # status
            status = git_hook.hook_status(Path.cwd())
            if status.get("error"):
                print(f"Hook: not installed ({status['error']})")
            elif status.get("installed"):
                print(f"Hook: installed at {status['path']} (managed by iterate)")
                print(f"Skip: {status.get('skippable', '')}")
            else:
                print(f"Hook: not installed (path={status.get('path')})")
    except git_hook.HookError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)


@iterate_app.command("cron")
def iterate_cron(
    action: str = typer.Argument(
        ..., help="Action: start|stop|status|history (background cron scheduler daemon)"
    ),
    limit: int = typer.Option(
        20, "--limit", min=1, max=200, help="Number of history entries to show (history action)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Manage the background cron scheduler daemon that executes scheduled jobs. [cron]"""
    from iterate_harness.services import cron_scheduler

    action = action.strip().lower()
    if action not in ("start", "stop", "status", "history"):
        raise typer.BadParameter("action must be start|stop|status|history")

    if action == "start":
        try:
            pid = cron_scheduler.start_daemon()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise typer.Exit(1) from exc
        print(f"Scheduler started (pid={pid}).")
    elif action == "stop":
        stopped = cron_scheduler.stop_scheduler()
        print("Scheduler stopped." if stopped else "Scheduler was not running.")
    elif action == "status":
        status = cron_scheduler.scheduler_status()
        if as_json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print(f"Running:       {status['running']}" + (f" (pid={status['pid']})" if status["pid"] else ""))
            print(f"Enabled jobs:  {status['enabled_jobs']}/{status['total_jobs']}")
            print(f"Log file:      {status['log_file']}")
            print(f"History file:  {status['history_file']}")
    else:  # history
        entries = cron_scheduler.load_history(limit=limit)
        if as_json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
        elif not entries:
            print("No cron job executions recorded yet.")
        else:
            for entry in entries:
                started = entry.get("started_at", "?")
                status_value = entry.get("status", "?")
                name = entry.get("name", entry.get("jobName", "?"))
                print(f"{started}  {status_value:<10} {name}")


def _typer_flag(value: object) -> bool:
    """Normalize a typer OptionInfo default to a plain bool.

    ``typer.Option(False)`` passes an ``OptionInfo`` object rather than
    ``False`` when the function is called directly (not through the CLI
    parser).  This helper strips the wrapper so that direct calls behave
    identically to CLI invocation.
    """
    # OptionInfo is always truthy, so check for the type explicitly.
    from typer.models import OptionInfo

    if isinstance(value, OptionInfo):
        return bool(value.default)
    return bool(value)


def _typer_int(value: object) -> int:
    """Normalize a typer OptionInfo default to a plain int."""
    from typer.models import OptionInfo

    if isinstance(value, OptionInfo):
        v = value.default
        return int(v) if v is not None else 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _typer_str(value: object) -> str:
    """Normalize a typer OptionInfo default to a plain str."""
    from typer.models import OptionInfo

    if isinstance(value, OptionInfo):
        v = value.default
        return str(v) if v is not None else ""
    if isinstance(value, str):
        return value
    return str(value) if value is not None else ""


def _write_html_report(html_report: ModuleType, entries: list[DecisionLogEntry], html_out: str) -> None:
    """Render and write the single-file HTML report for this project."""
    page = html_report.build_html_report(entries)
    if page is None:
        print("No report entry to render as HTML.", file=sys.stderr)
        return
    target = Path(html_out) if html_out != "-" else Path.cwd() / ".iterate" / "report.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    print(f"HTML report written: {target}")


def _write_html_replay(html_report: ModuleType, entries: list[DecisionLogEntry]) -> None:
    """Render and write the interactive round-replay page (--serve companion)."""
    page = html_report.build_replay_page(entries)
    if page is None:
        print("No decision-log entries to replay.", file=sys.stderr)
        return
    target = Path.cwd() / ".iterate" / "replay.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    print(f"Replay page written: {target}")


# ---- web subcommands ----

@web_app.command("serve")
def web_serve(
    project_root: str = typer.Option(
        "",
        "--project",
        help="Iterate project root (default: current working directory)",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind host (default: loopback only — do not expose publicly)",
    ),
    port: int = typer.Option(
        0,
        "--port",
        help="Port (default: OS-assigned ephemeral port)",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Do not open the browser automatically",
    ),
) -> None:
    """Start the WebUI management console (FastAPI + React frontend).

    Serves the dashboard, runs timeline, checkpoints, budget, config, and
    reports on a local loopback address and opens the browser. The backend
    binds to 127.0.0.1 by default and never exposes itself externally.
    """
    from iterate_harness.web.api import serve as web_serve_backend

    root = project_root or str(Path.cwd())
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        print(f"Project root not found: {root_path}", file=sys.stderr)
        raise typer.Exit(code=1)

    web_serve_backend(
        project_root=root_path,
        host=host,
        port=port,
        open_browser=not _typer_flag(no_browser),
    )


# ---- auth subcommands ----

# Mapping from provider name to human-readable label for interactive prompts.
# Aligned with the canonical provider names in api/registry.py — the iterate
# review/fix loop only needs direct API-key providers (no subscription/OAuth flows).
_PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic (Claude API)",
    "openai": "OpenAI / compatible",
    "deepseek": "DeepSeek",
    "dashscope": "Alibaba DashScope (Qwen)",
    "moonshot": "Moonshot (Kimi)",
    "gemini": "Google Gemini",
    "minimax": "MiniMax",
    "zhipu": "Zhipu AI (GLM)",
    "siliconflow": "SiliconFlow",
    "nvidia": "NVIDIA NIM",
    "orcarouter": "OrcaRouter",
    "ollama": "Ollama (local)",
}

_AUTH_SOURCE_LABELS: dict[str, str] = {
    "anthropic_api_key": "Anthropic API key",
    "openai_api_key": "OpenAI API key",
    "deepseek_api_key": "DeepSeek API key",
    "dashscope_api_key": "DashScope API key",
    "moonshot_api_key": "Moonshot API key",
    "gemini_api_key": "Gemini API key",
    "minimax_api_key": "MiniMax API key",
    "zhipu_api_key": "Zhipu AI API key",
    "siliconflow_api_key": "SiliconFlow API key",
    "nvidia_api_key": "NVIDIA API key",
    "orcarouter_api_key": "OrcaRouter API key",
    "local": "Local endpoint (no API key)",
}


def _can_use_questionary() -> bool:
    """Return True when a real interactive terminal is available."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if sys.stdin is not sys.__stdin__ or sys.stdout is not sys.__stdout__:
        return False
    try:
        import questionary  # noqa: F401
    except ImportError:
        return False
    return True


def _select_with_questionary(
    title: str,
    options: list[tuple[str, str]],
    *,
    default_value: str | None = None,
) -> str:
    import questionary

    choices = [
        questionary.Choice(
            title=label,
            value=value,
            checked=(value == default_value),
        )
        for value, label in options
    ]
    result = questionary.select(title, choices=choices, default=default_value).ask()
    if result is None:
        raise typer.Abort()
    return str(result)


def _text_prompt(message: str, *, default: str = "") -> str:
    """Prompt for text input, preferring questionary in a real TTY."""
    if _can_use_questionary():
        import questionary

        result = questionary.text(message, default=default).ask()
        if result is None:
            raise typer.Abort()
        return str(result)
    return typer.prompt(message, default=default)


def _secret_prompt(message: str) -> str:
    """Prompt for secret text, preferring questionary in a real TTY."""
    if _can_use_questionary():
        import questionary

        result = questionary.password(message).ask()
        if result is None:
            raise typer.Abort()
        return str(result)
    return typer.prompt(message, hide_input=True)


def _confirm_prompt(message: str, *, default: bool = False) -> bool:
    """Prompt for a yes/no confirmation, preferring questionary in a real TTY."""
    if _can_use_questionary():
        import questionary

        result = questionary.confirm(message, default=default).ask()
        if result is None:
            raise typer.Abort()
        return bool(result)
    return bool(typer.confirm(message, default=default))


def _select_from_menu(
    title: str,
    options: list[tuple[str, str]],
    *,
    default_value: str | None = None,
) -> str:
    """Render a simple numbered picker and return the selected value."""
    if _can_use_questionary():
        return _select_with_questionary(title, options, default_value=default_value)
    print(title, flush=True)
    default_index = 1
    for index, (value, label) in enumerate(options, 1):
        marker = " (default)" if value == default_value else ""
        if value == default_value:
            default_index = index
        print(f"  {index}. {label}{marker}", flush=True)
    raw = typer.prompt("Choose", default=str(default_index))
    try:
        selected = options[int(raw) - 1]
    except (ValueError, IndexError):
        raise typer.BadParameter(f"Invalid selection: {raw}") from None
    return selected[0]


def _prompt_model_for_profile(profile) -> str:
    from iterate_harness.config.settings import (
        CLAUDE_MODEL_ALIAS_OPTIONS,
        display_model_setting,
        is_claude_family_provider,
    )

    current = display_model_setting(profile)
    if profile.allowed_models:
        if len(profile.allowed_models) == 1:
            return profile.allowed_models[0]
        options = [(value, value) for value in profile.allowed_models]
        return _select_from_menu("Choose a model setting:", options, default_value=current if current in profile.allowed_models else profile.allowed_models[0])
    if is_claude_family_provider(profile.provider):
        options = [(value, f"{label} - {description}") for value, label, description in CLAUDE_MODEL_ALIAS_OPTIONS]
        options.append(("__custom__", "Custom model ID"))
        selection = _select_from_menu(
            "Choose a model setting:",
            options,
            default_value=current if any(value == current for value, _, _ in CLAUDE_MODEL_ALIAS_OPTIONS) else "__custom__",
        )
        if selection != "__custom__":
            return selection
    return _text_prompt("Model", default=current).strip() or current


def _format_profile_choice_label(info: dict[str, object]) -> str:
    """Render a user-facing workflow label without leaking internal provider ids."""
    label = str(info["label"])
    state = "" if bool(info["configured"]) else f" ({info['auth_state']})"
    return f"{label}{state}"


def _styled_missing_suffix(info: dict[str, object]) -> tuple[str, str] | None:
    """Return a soft red missing-auth suffix for questionary titles."""
    if bool(info["configured"]):
        return None
    return (f" ({info['auth_state']})", "fg:#d3869b")


def _select_setup_workflow(
    statuses: dict[str, dict[str, object]],
    *,
    default_value: str | None = None,
) -> str:
    """Render the top-level `ih setup` workflow picker with richer hints."""
    hints = {
        "claude-api": ("Claude / Kimi / GLM", "fg:#7aa2f7"),
        "openai": ("OpenAI (official)", "fg:#9ece6a"),
        "openai-compatible": ("OpenAI / compatible", "fg:#9ece6a"),
        "deepseek": ("DeepSeek", "fg:#4fd6be"),
        "zhipu": ("Zhipu GLM", "fg:#7aa2f7"),
        "siliconflow": ("SiliconFlow", "fg:#9ece6a"),
        "qwen": ("Qwen / DashScope", "fg:#bb9af7"),
        "ollama": ("Ollama / local", "fg:#4fd6be"),
    }

    if _can_use_questionary():
        import questionary

        choices = []
        for name, info in statuses.items():
            label = str(info["label"])
            hint = hints.get(name)
            missing = _styled_missing_suffix(info)
            if hint is None:
                if missing is None:
                    title = label
                else:
                    suffix, suffix_style = missing
                    title = [("", label), (suffix_style, suffix)]
            else:
                hint_text, hint_style = hint
                if missing is None:
                    title = [
                        ("", f"{label}  "),
                        (hint_style, hint_text),
                    ]
                else:
                    suffix, suffix_style = missing
                    title = [
                        ("", f"{label}  "),
                        (hint_style, hint_text),
                        ("", "  "),
                        (suffix_style, suffix.strip()),
                    ]
            choices.append(questionary.Choice(title=title, value=name, checked=(name == default_value)))

        result = questionary.select("Choose a provider workflow:", choices=choices, default=default_value).ask()
        if result is None:
            raise typer.Abort()
        return str(result)

    options: list[tuple[str, str]] = []
    for name, info in statuses.items():
        label = _format_profile_choice_label(info)
        hint = hints.get(name)
        if hint is not None:
            label = f"{label} ({hint[0]})"
        options.append((name, label))
    return _select_from_menu("Choose a provider workflow:", options, default_value=default_value)


def _default_credential_slot_for_profile(name: str, auth_source: str) -> str | None:
    from iterate_harness.config.settings import (
        auth_source_uses_api_key,
        builtin_provider_profile_names,
    )

    if name in builtin_provider_profile_names():
        return None
    if not auth_source_uses_api_key(auth_source):
        return None
    return name


def _prompt_api_key_for_profile(label: str) -> str:
    key = _secret_prompt(f"Enter API key for {label}").strip()
    if not key:
        raise typer.BadParameter("API key cannot be empty.")
    return key


def _configure_custom_profile_via_setup(manager) -> str:
    from iterate_harness.config.settings import ProviderProfile, default_auth_source_for_provider

    family = _select_from_menu(
        "Choose a compatible API family:",
        [
            ("anthropic", "Anthropic-compatible"),
            ("openai", "OpenAI-compatible"),
        ],
        default_value="anthropic",
    )
    default_name = f"custom-{family}"
    name = _text_prompt("Profile name", default=default_name).strip()
    if not name:
        raise typer.BadParameter("Profile name cannot be empty.")
    label = _text_prompt("Display label", default=name).strip() or name
    base_url = _text_prompt("Base URL", default="").strip()
    if not base_url:
        raise typer.BadParameter("Base URL cannot be empty.")

    auth_source = default_auth_source_for_provider(family, family)
    model = _text_prompt("Default model", default="").strip()
    if not model:
        raise typer.BadParameter("Default model cannot be empty.")

    profile = ProviderProfile(
        label=label,
        provider=family,
        api_format=family,
        auth_source=auth_source,
        default_model=model,
        last_model=model,
        base_url=base_url,
        credential_slot=_default_credential_slot_for_profile(name, auth_source),
        allowed_models=[model],
    )
    manager.upsert_profile(name, profile)
    manager.store_profile_credential(name, "api_key", _prompt_api_key_for_profile(label))
    return name


def _ensure_preset_profile(
    manager,
    *,
    name: str,
    label: str,
    provider: str,
    api_format: str,
    auth_source: str,
    base_url: str | None,
    model: str,
    lock_model: bool,
) -> str:
    from iterate_harness.config.settings import ProviderProfile

    existing = manager.list_profiles().get(name)
    profile = ProviderProfile(
        label=label,
        provider=provider,
        api_format=api_format,
        auth_source=auth_source,
        default_model=model,
        last_model=model,
        base_url=base_url,
        credential_slot=_default_credential_slot_for_profile(name, auth_source),
        allowed_models=[model] if lock_model else (existing.allowed_models if existing else []),
    )
    manager.upsert_profile(name, profile)
    return name


def _specialize_setup_target(manager, target: str) -> str:
    """Expand a top-level family choice into a concrete workflow profile."""
    from iterate_harness.config.settings import default_auth_source_for_provider

    if target == "claude-api":
        choice = _select_from_menu(
            "Choose an Anthropic-compatible provider:",
            [
                ("claude-api", "Claude official"),
                ("kimi-anthropic", "Moonshot Kimi"),
                ("glm-anthropic", "Zhipu GLM"),
                ("minimax-anthropic", "MiniMax"),
            ],
            default_value="claude-api",
        )
        if choice == "claude-api":
            return choice
        defaults = {
            "kimi-anthropic": ("Kimi (Anthropic-compatible)", "https://api.moonshot.cn/anthropic", "kimi-k2.5"),
            "glm-anthropic": ("GLM (Anthropic-compatible)", "", "glm-4.5"),
            "minimax-anthropic": ("MiniMax (Anthropic-compatible)", "", "MiniMax-M2.7"),
        }
        label, suggested_base_url, suggested_model = defaults[choice]
        base_url = _text_prompt("Base URL", default=suggested_base_url).strip()
        if not base_url:
            raise typer.BadParameter("Base URL cannot be empty.")
        model = _text_prompt("Model", default=suggested_model).strip()
        if not model:
            raise typer.BadParameter("Model cannot be empty.")
        return _ensure_preset_profile(
            manager,
            name=choice,
            label=label,
            provider="anthropic",
            api_format="anthropic",
            auth_source=default_auth_source_for_provider("anthropic", "anthropic"),
            base_url=base_url,
            model=model,
            lock_model=True,
        )

    if target == "openai-compatible":
        choice = _select_from_menu(
            "Choose an OpenAI-compatible provider:",
            [
                ("openai-compatible", "OpenAI official"),
                ("openrouter", "OpenRouter"),
                ("orcarouter", "OrcaRouter"),
            ],
            default_value="openai-compatible",
        )
        if choice == "openai-compatible":
            return choice
        presets = {
            "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", ""),
            "orcarouter": ("OrcaRouter", "https://api.orcarouter.ai/v1", "orcarouter/auto"),
        }
        label, suggested_base_url, suggested_model = presets[choice]
        base_url = _text_prompt("Base URL", default=suggested_base_url).strip()
        if not base_url:
            raise typer.BadParameter("Base URL cannot be empty.")
        model = _text_prompt("Default model", default=suggested_model).strip()
        if not model:
            raise typer.BadParameter("Default model cannot be empty.")
        return _ensure_preset_profile(
            manager,
            name=choice,
            label=label,
            provider="openai",
            api_format="openai",
            auth_source=default_auth_source_for_provider("openai", "openai"),
            base_url=base_url,
            model=model,
            lock_model=False,
        )

    return target


def _ensure_profile_auth(manager, profile_name: str) -> None:
    from iterate_harness.auth.flows import ApiKeyFlow
    from iterate_harness.config.settings import auth_source_provider_name, auth_source_uses_api_key

    profile = manager.list_profiles()[profile_name]
    if profile.auth_source == "local":
        print(f"{profile.label} uses a local endpoint; no API key required.", flush=True)
        return
    if not auth_source_uses_api_key(profile.auth_source):
        _login_provider(auth_source_provider_name(profile.auth_source))
        return

    flow = ApiKeyFlow(
        provider=profile.provider,
        prompt_text=f"Enter API key for {profile.label}",
        signup_url=profile.signup_url,
    )
    try:
        key = flow.run()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    manager.store_profile_credential(profile_name, "api_key", key)
    print(f"{profile.label} API key saved.", flush=True)


def _maybe_update_profile_auth(manager, profile_name: str) -> bool:
    """Ask whether to replace an already configured profile API key."""
    from iterate_harness.config.settings import auth_source_uses_api_key

    profile = manager.list_profiles()[profile_name]
    if not auth_source_uses_api_key(profile.auth_source):
        return False
    if not _confirm_prompt(f"Update API key for {profile.label}?", default=False):
        return False
    _ensure_profile_auth(manager, profile_name)
    return True


def _login_provider(provider: str) -> None:
    """Authenticate the given provider with its API key."""
    from iterate_harness.auth.flows import ApiKeyFlow
    from iterate_harness.auth.manager import AuthManager
    from iterate_harness.auth.storage import store_credential

    manager = AuthManager()

    if provider in ("local", "ollama"):
        # Local endpoints (Ollama) need no API key — activate the profile and
        # point the user at `ih setup` instead of leaving a dead-end.
        if "ollama" in manager.list_profiles():
            manager.use_profile("ollama")
            print(
                f"{provider} runs on a local endpoint (no API key required). "
                "Activated profile 'ollama'. Run `ih setup ollama` to pick a model.",
                flush=True,
            )
        else:
            print(
                f"{provider} runs on a local endpoint (no API key required). "
                "Run `ih setup` to configure a local profile.",
                flush=True,
            )
        return

    if provider not in _PROVIDER_LABELS:
        print(f"Unknown provider: {provider!r}. Known: {', '.join(_PROVIDER_LABELS)}", file=sys.stderr)
        raise typer.Exit(1)

    label = _PROVIDER_LABELS[provider]
    signup_url = None
    if provider == "orcarouter":
        from iterate_harness.config.settings import ORCAROUTER_SIGNUP_URL

        signup_url = ORCAROUTER_SIGNUP_URL
    flow = ApiKeyFlow(provider=provider, prompt_text=f"Enter your {label} API key", signup_url=signup_url)
    try:
        key = flow.run()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    store_credential(provider, "api_key", key)
    stored = False
    try:
        manager.store_credential(provider, "api_key", key)
        stored = True
    except Exception as exc:
        log.error("AuthManager store failed for %s: %s", provider, exc)
    if not stored:
        print(f"Failed to save {label} API key.", file=sys.stderr)
        raise typer.Exit(1)
    print(f"{label} API key saved.", flush=True)


@app.command("setup")
def setup_cmd(
    profile: str | None = typer.Argument(None, help="Provider profile name to configure"),
) -> None:
    """Unified setup flow: choose workflow, authenticate if needed, then set the model."""
    from iterate_harness.auth.manager import AuthManager
    from iterate_harness.config.settings import display_model_setting

    manager = AuthManager()
    statuses = manager.get_profile_statuses()
    if not statuses:
        print("No provider profiles available.", file=sys.stderr)
        raise typer.Exit(1)

    target = profile
    if target is None:
        target = _select_setup_workflow(
            statuses,
            default_value=manager.get_active_profile(),
        )

    target = _specialize_setup_target(manager, target)
    manager = AuthManager()
    statuses = manager.get_profile_statuses()

    if target not in statuses:
        print(f"Unknown provider profile: {target!r}", file=sys.stderr)
        raise typer.Exit(1)

    info = statuses[target]
    if not info["configured"]:
        source_label = _AUTH_SOURCE_LABELS.get(info["auth_source"], info["auth_source"])
        print(f"{info['label']} requires {source_label}.", flush=True)
        _ensure_profile_auth(manager, target)
        manager = AuthManager()
    else:
        if _maybe_update_profile_auth(manager, target):
            manager = AuthManager()

    profile_obj = manager.list_profiles()[target]
    model_setting = _prompt_model_for_profile(profile_obj)
    if model_setting.lower() == "default":
        manager.update_profile(target, last_model="")
    else:
        manager.update_profile(target, last_model=model_setting)
    manager.use_profile(target)

    updated = manager.list_profiles()[target]
    print(
        "Setup complete:\n"
        f"- profile: {target}\n"
        f"- provider: {updated.provider}\n"
        f"- auth_source: {updated.auth_source}\n"
        f"- model: {display_model_setting(updated)}",
        flush=True,
    )


@app.command("update")
def update_cmd(
    check: bool = typer.Option(
        False,
        "--check",
        help="Check for a newer release without applying it",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Apply the update without asking for confirmation",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Reinstall even when the installed version is already the latest",
    ),
) -> None:
    """Check for a newer iterate-harness release and apply it."""
    from iterate_harness import update as update_module

    home = Path.home()
    method = update_module.current_install_method()
    method_label = update_module.INSTALL_METHOD_LABELS.get(method, method)

    print(f"iterate-harness {__version__} (install: {method_label})", flush=True)

    latest = update_module.fetch_latest_version()
    if latest is None:
        print(
            "Could not reach the release feed. Check your network connection and retry.",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    if update_module.compare_versions(__version__, latest) <= 0 and not force:
        print(f"Already up to date (latest release: {latest}).", flush=True)
        return

    print(f"New version available: {__version__} -> {latest}", flush=True)
    if check:
        return

    if not yes:
        proceed = _confirm_prompt(f"Apply the update to iterate-harness {latest}?", default=False)
        if not proceed:
            print("Update cancelled.", flush=True)
            raise typer.Exit(1)

    result = update_module.perform_update(
        current=__version__,
        home=home,
        method=method,
        latest=latest,
    )
    if result.success:
        print(f"Updated: {result.message}", flush=True)
    else:
        print(f"Update failed: {result.message}", file=sys.stderr)
        raise typer.Exit(1)


@auth_app.command("login")
def auth_login(
    provider: Optional[str] = typer.Argument(None, help="Provider name (anthropic, openai, deepseek, …)"),
) -> None:
    """Interactively authenticate with a provider.

    Run without arguments to choose a provider from a menu.
    Supported providers: anthropic, openai, deepseek, dashscope, moonshot, gemini, minimax, zhipu, siliconflow, nvidia.
    """
    if provider is None:
        print("Select a provider to authenticate:", flush=True)
        labels = list(_PROVIDER_LABELS.items())
        for i, (name, label) in enumerate(labels, 1):
            print(f"  {i}. {label} [{name}]", flush=True)
        raw = typer.prompt("Enter number or provider name", default="1")
        try:
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(labels):
                provider = labels[idx][0]
            else:
                print("Invalid selection.", file=sys.stderr)
                raise typer.Exit(1)
        except ValueError:
            provider = raw.strip()

    provider = provider.lower()
    _login_provider(provider)


@auth_app.command("status")
def auth_status_cmd() -> None:
    """Show authentication source and provider profile status."""
    from iterate_harness.auth.manager import AuthManager

    manager = AuthManager()
    auth_sources = manager.get_auth_source_statuses()
    profiles = manager.get_profile_statuses()

    print("Auth sources:")
    print(f"{'Source':<24} {'State':<14} {'Origin':<10} Active")
    print("-" * 60)
    for name, info in auth_sources.items():
        label = _AUTH_SOURCE_LABELS.get(name, name)
        active_str = "<-- active" if info["active"] else ""
        print(f"{label:<24} {info['state']:<14} {info['source']:<10} {active_str}")
        if info.get("detail"):
            print(f"  detail: {info['detail']}")

    print()
    print("Provider profiles:")
    print(f"{'Profile':<20} {'Provider':<18} {'Auth source':<22} {'State':<12} Active")
    print("-" * 92)
    for name, info in profiles.items():
        status_str = "ready" if info["configured"] else info.get("auth_state", "missing auth")
        active_str = "<-- active" if info["active"] else ""
        print(f"{name:<20} {info['provider']:<18} {info['auth_source']:<22} {status_str:<12} {active_str}")


@auth_app.command("logout")
def auth_logout(
    provider: Optional[str] = typer.Argument(None, help="Provider to log out (default: active provider)"),
) -> None:
    """Clear stored authentication for a provider."""
    from iterate_harness.auth.manager import AuthManager

    manager = AuthManager()
    if provider is None:
        target = manager.get_active_profile()
        manager.clear_profile_credential(target)
        print(f"Authentication cleared for profile: {target}", flush=True)
        return
    manager.clear_credential(provider)
    print(f"Authentication cleared for provider: {provider}", flush=True)


@auth_app.command("switch")
def auth_switch(
    provider: str = typer.Argument(..., help="Auth source or profile to activate"),
) -> None:
    """Switch the auth source for the active profile, or use a profile by name."""
    from iterate_harness.auth.manager import AuthManager

    manager = AuthManager()
    try:
        manager.switch_provider(provider)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    print(f"Switched auth/profile to: {provider}", flush=True)


# ---- provider subcommands ----


@provider_app.command("list")
def provider_list() -> None:
    """List configured provider profiles."""
    from iterate_harness.auth.manager import AuthManager

    statuses = AuthManager().get_profile_statuses()
    for name, info in statuses.items():
        marker = "*" if info["active"] else " "
        configured = "ready" if info["configured"] else "missing auth"
        base = info["base_url"] or "(default)"
        print(f"{marker} {name}: {info['label']} [{configured}]")
        print(f"    auth={info['auth_source']} model={info['model']} base_url={base}")


@provider_app.command("use")
def provider_use(
    name: str = typer.Argument(..., help="Provider profile name"),
) -> None:
    """Activate a provider profile."""
    from iterate_harness.auth.manager import AuthManager

    manager = AuthManager()
    try:
        manager.use_profile(name)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    print(f"Activated provider profile: {name}", flush=True)


@provider_app.command("add")
def provider_add(
    name: str = typer.Argument(..., help="Provider profile name"),
    label: str = typer.Option(..., "--label", help="Display label"),
    provider: str = typer.Option(..., "--provider", help="Runtime provider id"),
    api_format: str = typer.Option(..., "--api-format", help="API format"),
    auth_source: str = typer.Option(..., "--auth-source", help="Auth source name"),
    model: str = typer.Option(..., "--model", help="Default model"),
    base_url: str | None = typer.Option(None, "--base-url", help="Optional base URL"),
    credential_slot: str | None = typer.Option(None, "--credential-slot", help="Optional profile-specific credential slot"),
    api_key: str | None = typer.Option(None, "--api-key", help="Set the profile API key"),
    allowed_models: list[str] | None = typer.Option(None, "--allowed-model", help="Allowed model values for this profile"),
    context_window_tokens: int | None = typer.Option(None, "--context-window-tokens", help="Optional context window override for auto-compact"),
    auto_compact_threshold_tokens: int | None = typer.Option(None, "--auto-compact-threshold-tokens", help="Optional explicit auto-compact threshold override"),
) -> None:
    """Create a provider profile."""
    from iterate_harness.auth.manager import AuthManager
    from iterate_harness.config.settings import ProviderProfile

    manager = AuthManager()
    manager.upsert_profile(
        name,
        ProviderProfile(
            label=label,
            provider=provider,
            api_format=api_format,
            auth_source=auth_source,
            default_model=model,
            last_model=model,
            base_url=base_url,
            credential_slot=credential_slot or _default_credential_slot_for_profile(name, auth_source),
            allowed_models=allowed_models or ([model] if credential_slot or _default_credential_slot_for_profile(name, auth_source) else []),
            context_window_tokens=context_window_tokens,
            auto_compact_threshold_tokens=auto_compact_threshold_tokens,
        ),
    )
    if api_key is not None:
        manager = AuthManager()
        manager.store_profile_credential(name, "api_key", api_key)
        print(f"Saved provider profile: {name} (API key set)", flush=True)
    else:
        print(f"Saved provider profile: {name}", flush=True)


@provider_app.command("edit")
def provider_edit(
    name: str = typer.Argument(..., help="Provider profile name"),
    label: str | None = typer.Option(None, "--label", help="Display label"),
    provider: str | None = typer.Option(None, "--provider", help="Runtime provider id"),
    api_format: str | None = typer.Option(None, "--api-format", help="API format"),
    auth_source: str | None = typer.Option(None, "--auth-source", help="Auth source name"),
    model: str | None = typer.Option(None, "--model", help="Default model"),
    base_url: str | None = typer.Option(None, "--base-url", help="Optional base URL"),
    credential_slot: str | None = typer.Option(None, "--credential-slot", help="Optional profile-specific credential slot"),
    api_key: str | None = typer.Option(None, "--api-key", help="Replace the profile API key"),
    allowed_models: list[str] | None = typer.Option(None, "--allowed-model", help="Allowed model values for this profile"),
    context_window_tokens: int | None = typer.Option(None, "--context-window-tokens", help="Optional context window override for auto-compact"),
    auto_compact_threshold_tokens: int | None = typer.Option(None, "--auto-compact-threshold-tokens", help="Optional explicit auto-compact threshold override"),
) -> None:
    """Edit a provider profile."""
    from iterate_harness.auth.manager import AuthManager

    manager = AuthManager()
    try:
        manager.update_profile(
            name,
            label=label,
            provider=provider,
            api_format=api_format,
            auth_source=auth_source,
            default_model=model,
            last_model=model,
            base_url=base_url,
            credential_slot=credential_slot,
            allowed_models=allowed_models,
            context_window_tokens=context_window_tokens,
            auto_compact_threshold_tokens=auto_compact_threshold_tokens,
        )
        if api_key is not None:
            manager = AuthManager()
            manager.store_profile_credential(name, "api_key", api_key)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    if api_key is not None:
        print(f"Updated provider profile: {name} (API key replaced)", flush=True)
    else:
        print(f"Updated provider profile: {name}", flush=True)


@provider_app.command("remove")
def provider_remove(
    name: str = typer.Argument(..., help="Provider profile name"),
) -> None:
    """Remove a provider profile."""
    from iterate_harness.auth.manager import AuthManager

    manager = AuthManager()
    try:
        manager.remove_profile(name)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    print(f"Removed provider profile: {name}", flush=True)

# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit",
        callback=_version_callback,
        is_eager=True,
    ),
    # --- Session ---
    continue_session: bool = typer.Option(
        False,
        "--continue",
        "-c",
        help="Continue the most recent conversation in the current directory",
        rich_help_panel="Session",
    ),
    resume: str | None = typer.Option(
        None,
        "--resume",
        "-r",
        help="Resume a conversation by session ID, or open picker",
        rich_help_panel="Session",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Set a display name for this session",
        rich_help_panel="Session",
    ),
    # --- Model & Effort ---
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Model alias (e.g. 'sonnet', 'opus') or full model ID",
        rich_help_panel="Model & Effort",
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help="Effort level for the session (low, medium, high, max)",
        rich_help_panel="Model & Effort",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Override verbose mode setting from config",
        rich_help_panel="Model & Effort",
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help="Maximum number of agentic turns (enforced by default in --print; optional cap for interactive mode)",
        rich_help_panel="Model & Effort",
    ),
    # --- Output ---
    print_mode: str | None = typer.Option(
        None,
        "--print",
        "-p",
        help="Print response and exit. Pass your prompt as the value: -p 'your prompt'",
        rich_help_panel="Output",
    ),
    output_format: str | None = typer.Option(
        None,
        "--output-format",
        help="Output format with --print: text (default), json, or stream-json",
        rich_help_panel="Output",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview resolved runtime config, skills, commands, and tools without executing the model or tools",
        rich_help_panel="Output",
    ),
    # --- Permissions ---
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help="Permission mode: default, plan, or full_auto",
        rich_help_panel="Permissions",
    ),
    dangerously_skip_permissions: bool = typer.Option(
        False,
        "--dangerously-skip-permissions",
        help="Bypass all permission checks (only for sandboxed environments)",
        rich_help_panel="Permissions",
    ),
    allowed_tools: Optional[list[str]] = typer.Option(
        None,
        "--allowed-tools",
        help="Comma or space-separated list of tool names to allow",
        rich_help_panel="Permissions",
    ),
    disallowed_tools: Optional[list[str]] = typer.Option(
        None,
        "--disallowed-tools",
        help="Comma or space-separated list of tool names to deny",
        rich_help_panel="Permissions",
    ),
    # --- System & Context ---
    system_prompt: str | None = typer.Option(
        None,
        "--system-prompt",
        "-s",
        help="Override the default system prompt",
        rich_help_panel="System & Context",
    ),
    append_system_prompt: str | None = typer.Option(
        None,
        "--append-system-prompt",
        help="Append text to the default system prompt",
        rich_help_panel="System & Context",
    ),
    settings_file: str | None = typer.Option(
        None,
        "--settings",
        help="Path to a JSON settings file or inline JSON string",
        rich_help_panel="System & Context",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Anthropic-compatible API base URL",
        rich_help_panel="System & Context",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        "-k",
        help="API key (overrides config and environment)",
        rich_help_panel="System & Context",
    ),
    bare: bool = typer.Option(
        False,
        "--bare",
        help="Minimal mode: skip hooks, plugins, MCP, and auto-discovery",
        rich_help_panel="System & Context",
    ),
    api_format: str | None = typer.Option(
        None,
        "--api-format",
        help="API format: 'anthropic' (default) or 'openai' (OpenAI-compatible: DeepSeek, DashScope, SiliconFlow, etc.)",
        rich_help_panel="System & Context",
    ),
    theme: str | None = typer.Option(
        None,
        "--theme",
        help="TUI theme: default, dark, minimal, cyberpunk, solarized, or custom name",
        rich_help_panel="System & Context",
    ),
    # --- Advanced ---
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Enable debug logging",
        rich_help_panel="Advanced",
    ),
    mcp_config: Optional[list[str]] = typer.Option(
        None,
        "--mcp-config",
        help="Load MCP servers from JSON files or strings",
        rich_help_panel="Advanced",
    ),
    cwd: str = typer.Option(
        str(Path.cwd()),
        "--cwd",
        help="Working directory for the session",
        hidden=True,
    ),
    backend_only: bool = typer.Option(
        False,
        "--backend-only",
        help="Run the structured backend host for the React terminal UI",
        hidden=True,
    ),
    task_worker: bool = typer.Option(
        False,
        "--task-worker",
        help="Run the stdin-driven headless worker loop used for background agent tasks",
        hidden=True,
    ),
) -> None:
    """Start an interactive session or run a single prompt."""
    if ctx.invoked_subcommand is not None:
        return

    import asyncio
    import logging

    if debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
            stream=sys.stderr,
        )
        logging.getLogger("iterate_harness").setLevel(logging.DEBUG)
    elif os.environ.get("ITERATE_LOG_LEVEL"):
        lvl = getattr(logging, os.environ["ITERATE_LOG_LEVEL"].upper(), logging.WARNING)
        logging.basicConfig(level=lvl, format="%(asctime)s [%(name)s] %(levelname)s %(message)s", stream=sys.stderr)

    if dangerously_skip_permissions:
        permission_mode = "full_auto"

    # Apply --theme override to settings
    if theme:
        from iterate_harness.config.settings import load_settings, save_settings

        settings = load_settings()
        settings.theme = theme
        save_settings(settings)

    from iterate_harness.ui.app import run_print_mode, run_repl, run_task_worker

    if dry_run and (continue_session or resume is not None):
        print("Error: --dry-run does not support --continue/--resume yet.", file=sys.stderr)
        raise typer.Exit(1)

    if dry_run:
        prompt = print_mode.strip() if print_mode is not None else None
        if print_mode is not None and not prompt:
            print("Error: -p/--print requires a prompt value, e.g. -p 'your prompt'", file=sys.stderr)
            raise typer.Exit(1)
        preview = _build_dry_run_preview(
            prompt=prompt,
            cwd=cwd,
            model=model,
            max_turns=max_turns,
            base_url=base_url,
            system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
            api_key=api_key,
            api_format=api_format,
            permission_mode=permission_mode,
        )
        effective_output_format = output_format or "text"
        if effective_output_format == "text":
            print(_format_dry_run_preview(preview))
        elif effective_output_format == "json":
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        elif effective_output_format == "stream-json":
            print(json.dumps(preview, ensure_ascii=False))
        else:
            print(
                "Error: --dry-run only supports --output-format text, json, or stream-json",
                file=sys.stderr,
            )
            raise typer.Exit(1)
        return

    # Handle --continue and --resume flags
    if continue_session or resume is not None:
        from iterate_harness.services.session_storage import (
            list_session_snapshots,
            load_session_by_id,
            load_session_snapshot,
        )

        session_data = None
        if continue_session:
            session_data = load_session_snapshot(cwd)
            if session_data is None:
                print("No previous session found in this directory.", file=sys.stderr)
                raise typer.Exit(1)
            print(f"Continuing session: {session_data.get('summary', '(untitled)')[:60]}")
        elif resume == "" or resume is None:
            # --resume with no value: show session picker
            sessions = list_session_snapshots(cwd, limit=10)
            if not sessions:
                print("No saved sessions found.", file=sys.stderr)
                raise typer.Exit(1)
            print("Saved sessions:")
            for i, s in enumerate(sessions, 1):
                print(f"  {i}. [{s['session_id']}] {s.get('summary', '?')[:50]} ({s['message_count']} msgs)")
            choice = typer.prompt("Enter session number or ID")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(sessions):
                    session_data = load_session_by_id(cwd, sessions[idx]["session_id"])
                else:
                    print("Invalid selection.", file=sys.stderr)
                    raise typer.Exit(1)
            except ValueError:
                session_data = load_session_by_id(cwd, choice)
            if session_data is None:
                print(f"Session not found: {choice}", file=sys.stderr)
                raise typer.Exit(1)
        else:
            session_data = load_session_by_id(cwd, resume)
            if session_data is None:
                print(f"Session not found: {resume}", file=sys.stderr)
                raise typer.Exit(1)

        # Pass restored session to the REPL
        asyncio.run(
            run_repl(
                prompt=None,
                cwd=cwd,
                model=session_data.get("model") or model,
                backend_only=backend_only,
                base_url=base_url,
                system_prompt=system_prompt,
                api_key=api_key,
                restore_messages=session_data.get("messages"),
                restore_tool_metadata=session_data.get("tool_metadata"),
                permission_mode=permission_mode,
                api_format=api_format,
            )
        )
        return

    if print_mode is not None:
        prompt = print_mode.strip()
        if not prompt:
            print("Error: -p/--print requires a prompt value, e.g. -p 'your prompt'", file=sys.stderr)
            raise typer.Exit(1)
        asyncio.run(
            run_print_mode(
                prompt=prompt,
                output_format=output_format or "text",
                cwd=cwd,
                model=model,
                base_url=base_url,
                system_prompt=system_prompt,
                append_system_prompt=append_system_prompt,
                api_key=api_key,
                api_format=api_format,
                permission_mode=permission_mode,
                max_turns=max_turns,
            )
        )
        return

    if task_worker:
        asyncio.run(
            run_task_worker(
                cwd=cwd,
                model=model,
                max_turns=max_turns,
                base_url=base_url,
                system_prompt=system_prompt,
                api_key=api_key,
                api_format=api_format,
                permission_mode=permission_mode,
            )
        )
        return

    asyncio.run(
        run_repl(
            prompt=None,
            cwd=cwd,
            model=model,
            max_turns=max_turns,
            backend_only=backend_only,
            base_url=base_url,
            system_prompt=system_prompt,
            api_key=api_key,
            api_format=api_format,
            permission_mode=permission_mode,
        )
    )
