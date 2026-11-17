"""Permission policy inspection + decision simulation.

The harness has always enforced a layered permission policy — mode, explicit
tool allow/deny lists, path rules, command rules, the built-in sensitive-path
boundary, the iterate ``protected_paths`` / ``risk_areas`` boundaries and the
``forbidden_fix_patterns`` regex gate — but that policy was only visible as the
*outcome* of a live tool call. An operator who wanted to know "would
``bash rm -rf build`` be allowed in ``full_auto``?" had to run the agent and
watch a call fail, and a typo'd path rule (empty pattern) was silently skipped
with nothing but a log line.

This module is the read-only, network-free policy projection used by
``ih permissions``, ``GET /api/v1/permissions`` and the console page. It builds
the *same* :class:`PermissionChecker` the engine builds (via
:func:`build_permission_checker`) and exposes:

- the effective policy (mode, tool lists, path rules, command rules, sensitive
  paths, risk areas, forbidden regexes) — merged boundaries included, so the
  page can never claim a rule is absent when it is enforced;
- static configuration problems with copyable remedies (a rule that can never
  win, an invalid regex, iterate boundaries disabled);
- a decision simulator (:func:`simulate_permission_decision`) that runs the
  real checker over a hypothetical tool call and returns the exact verdict the
  engine would reach — including ``requires_confirmation``, which a live run
  only reveals by stopping to ask.

Nothing here writes settings, runs a tool, or contacts a provider.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

from iterate_harness.config.settings import Settings, load_settings
from iterate_harness.permissions.checker import SENSITIVE_PATH_PATTERNS, build_permission_checker
from iterate_harness.permissions.modes import PermissionMode

UTC = datetime.timezone.utc

#: Human labels for the three permission modes.
MODE_LABELS: dict[str, str] = {
    PermissionMode.DEFAULT.value: "默认",
    PermissionMode.PLAN.value: "计划",
    PermissionMode.FULL_AUTO.value: "全自动",
}

#: One-line explanation of what each mode does to a mutating tool call.
MODE_DETAILS: dict[str, str] = {
    PermissionMode.DEFAULT.value: "Mutating tools require confirmation; read-only tools run freely.",
    PermissionMode.PLAN.value: "Mutating tools are blocked until the session exits plan mode.",
    PermissionMode.FULL_AUTO.value: "Mutating tools run without prompting (hard boundaries still apply).",
}

#: Verdict strings shared by the CLI exit codes, the API and the console page.
VERDICT_ALLOWED = "allowed"
VERDICT_CONFIRM = "confirm"
VERDICT_DENIED = "denied"


def _problem(kind: str, name: str, detail: str, remedy: str) -> dict[str, str]:
    return {"kind": kind, "name": name, "detail": detail, "remedy": remedy}


def _policy_problems(settings: Settings, *, project_root: Path) -> list[dict[str, str]]:
    """Statically validate the permission configuration.

    These are the failures a live run hides: the checker skips an unusable rule
    and carries on, so the only evidence is a ``log.warning`` nobody reads.
    Each problem carries an executable remedy.
    """
    problems: list[dict[str, str]] = []
    permission = settings.permission

    if not settings.iterate.enabled:
        problems.append(
            _problem(
                "iterate",
                "enabled",
                "iterate boundaries are disabled: protected_paths, risk_areas and "
                "forbidden_fix_patterns are NOT enforced.",
                "Set `iterate.enabled: true` in settings.json (or re-run `ih iterate init`).",
            )
        )

    # Path rules: an empty/malformed pattern is skipped by the checker.
    seen_patterns: dict[str, bool] = {}
    for index, rule in enumerate(permission.path_rules):
        pattern = getattr(rule, "pattern", None)
        allow = bool(getattr(rule, "allow", True))
        if not isinstance(pattern, str) or not pattern.strip():
            problems.append(
                _problem(
                    "path_rule",
                    f"path_rules[{index}]",
                    "rule has a missing or empty pattern and is ignored at run time.",
                    f"Give `permission.path_rules[{index}]` a non-empty glob pattern, or remove it.",
                )
            )
            continue
        normalized = pattern.strip()
        if normalized in seen_patterns and seen_patterns[normalized] != allow:
            problems.append(
                _problem(
                    "path_rule",
                    normalized,
                    "the same pattern appears as both an allow and a deny rule; "
                    "the deny rule always wins, so the allow rule is dead configuration.",
                    f"Remove the allow rule for `{normalized}` or change the pattern so they do not collide.",
                )
            )
        seen_patterns.setdefault(normalized, allow)

    # Command rules: deny wins, so an overlapping allow entry is dead config.
    allowed_commands = {command.strip() for command in permission.allowed_commands if command.strip()}
    for command in permission.denied_commands:
        if isinstance(command, str) and command.strip() in allowed_commands:
            problems.append(
                _problem(
                    "command_rule",
                    command,
                    "the command is both allowlisted and deny-listed; the deny rule always wins.",
                    f"Remove `{command}` from one of `permission.allowed_commands` / `permission.denied_commands`.",
                )
            )

    # Tool rules: same precedence, same silent dead entry.
    allowed_tools = {name.strip() for name in permission.allowed_tools if name.strip()}
    for name in permission.denied_tools:
        if isinstance(name, str) and name.strip() in allowed_tools:
            problems.append(
                _problem(
                    "tool_rule",
                    name,
                    "the tool is both allowlisted and deny-listed; the deny rule always wins.",
                    f"Remove `{name}` from one of `permission.allowed_tools` / `permission.denied_tools`.",
                )
            )

    # Forbidden-content regexes: build_permission_checker skips bad ones.
    for raw_pattern in settings.iterate.forbidden_fix_patterns:
        try:
            re.compile(raw_pattern)
        except re.error as exc:
            problems.append(
                _problem(
                    "forbidden_pattern",
                    raw_pattern,
                    f"invalid regular expression ({exc}); the boundary is skipped.",
                    f"Fix or remove `{raw_pattern}` in `iterate.forbidden_fix_patterns`.",
                )
            )

    # Risk areas that normalize to nothing are silently dropped.
    for raw in settings.iterate.risk_area_paths:
        if not isinstance(raw, str) or not raw.strip():
            problems.append(
                _problem(
                    "risk_area",
                    repr(raw),
                    "empty risk-area entry is ignored, so the intended path is NOT gated.",
                    "Give every `iterate.risk_area_paths` entry a non-empty path glob, or remove it.",
                )
            )

    return problems


def _infer_read_only(tool: object) -> bool | None:
    """Best-effort read-only classification for one registered tool.

    The engine classifies a *call* (``tool.is_read_only(parsed_input)``), so a
    schema alone cannot always answer. Defaults are tried first; when the input
    model requires fields we fall back to ``model_construct()`` (no validation,
    missing attributes read as absent). ``None`` means "cannot infer" and the
    simulator treats it as mutating — the fail-closed direction.
    """
    model = getattr(tool, "input_model", None)
    if model is None:
        return None
    arguments: object
    try:
        arguments = model()
    except Exception:  # noqa: BLE001 - required fields / custom validators
        try:
            arguments = model.model_construct()
        except Exception:  # noqa: BLE001 - not a pydantic model
            return None
    try:
        return bool(tool.is_read_only(arguments))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - tool needs real arguments
        return None


def _registered_tools() -> list[dict[str, Any]]:
    """The default tool registry with an inferred read-only flag per tool."""
    try:
        from iterate_harness.tools import create_default_tool_registry

        registry = create_default_tool_registry()
    except Exception:  # noqa: BLE001 - the policy page must survive a loader bug
        return []
    rows: list[dict[str, Any]] = []
    for tool in sorted(registry.list_tools(), key=lambda item: item.name):
        rows.append(
            {
                "name": tool.name,
                "description": tool.description,
                "read_only": _infer_read_only(tool),
            }
        )
    return rows


def _resolve_root(project_root: str | Path | None) -> Path:
    return Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()


def build_permission_policy(
    project_root: str | Path | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Return the effective permission policy for *project_root*.

    The returned dict is JSON-ready and is the single source for the CLI and the
    WebUI page. Boundaries merged by :func:`build_permission_checker` are
    included, so this is what the engine will enforce — not a re-reading of the
    settings file that could drift.
    """
    root = _resolve_root(project_root)
    try:
        resolved_settings = settings or load_settings()
    except Exception as exc:  # noqa: BLE001 - an unreadable settings file is a finding
        resolved_settings = Settings()
        settings_error = f"{type(exc).__name__}: {exc}"
    else:
        settings_error = ""

    checker = build_permission_checker(resolved_settings, cwd=root)
    problems = _policy_problems(resolved_settings, project_root=root)
    if settings_error:
        problems.insert(
            0,
            _problem(
                "settings",
                "load",
                f"settings could not be loaded ({settings_error}); built-in defaults are shown.",
                "Fix the settings file and refresh (`ih doctor` reports the same failure).",
            ),
        )

    mode = resolved_settings.permission.mode.value
    path_rules = [{"pattern": rule.pattern, "allow": rule.allow} for rule in checker.path_rules]
    risk_areas = list(checker.risk_area_patterns)
    forbidden = [pattern.pattern for pattern in checker.forbidden_content_patterns]
    tools = _registered_tools()

    summary = {
        "allowed_tools": len(resolved_settings.permission.allowed_tools),
        "denied_tools": len(resolved_settings.permission.denied_tools),
        "path_rules": len(path_rules),
        "deny_path_rules": sum(1 for rule in path_rules if not rule["allow"]),
        "allow_path_rules": sum(1 for rule in path_rules if rule["allow"]),
        "allowed_commands": len(resolved_settings.permission.allowed_commands),
        "denied_commands": len(resolved_settings.permission.denied_commands),
        "sensitive_paths": len(SENSITIVE_PATH_PATTERNS),
        "risk_areas": len(risk_areas),
        "forbidden_patterns": len(forbidden),
        "tools": len(tools),
        "problems": len(problems),
    }

    return {
        "project_root": str(root),
        "generated_at": datetime.datetime.now(UTC).isoformat(),
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "mode_detail": MODE_DETAILS.get(mode, ""),
        "iterate_enabled": bool(resolved_settings.iterate.enabled),
        "summary": summary,
        "has_problems": bool(problems),
        "problems": problems,
        "allowed_tools": list(resolved_settings.permission.allowed_tools),
        "denied_tools": list(resolved_settings.permission.denied_tools),
        "path_rules": path_rules,
        "allowed_commands": list(resolved_settings.permission.allowed_commands),
        "denied_commands": list(resolved_settings.permission.denied_commands),
        "sensitive_paths": list(SENSITIVE_PATH_PATTERNS),
        "risk_areas": risk_areas,
        "forbidden_patterns": forbidden,
        "tools": tools,
    }


def simulate_permission_decision(
    tool_name: str,
    *,
    project_root: str | Path | None = None,
    file_path: str = "",
    command: str = "",
    content: str = "",
    read_only: bool | None = None,
    mode: str = "",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run the real checker over a hypothetical tool call.

    The path/command/content normalization reuses the engine's own helpers
    (``_resolve_permission_file_path`` / ``_extract_command_paths`` /
    ``_extract_permission_content``), so a simulated verdict cannot diverge from
    a live one because of a different interpretation of the same input.

    ``read_only`` of ``None`` infers from the registered tool; an unknown tool
    or an uninferable schema is treated as **mutating** (fail-closed).

    ``mode`` replaces the *configured* mode for this simulation only (``plan`` /
    ``default`` / ``full_auto``). It deliberately does not use the checker's
    session ``mode_override`` channel: that channel refuses ``full_auto`` by
    design (a session key must never escalate), so "what if I switch to
    full_auto?" would silently answer with the old mode.
    """
    if not tool_name.strip():
        raise ValueError("tool_name is required")
    valid_modes = {item.value for item in PermissionMode}
    normalized_mode = mode.strip().lower()
    if normalized_mode and normalized_mode not in valid_modes:
        raise ValueError(
            f"unknown mode {mode!r}; expected one of {', '.join(sorted(valid_modes))}"
        )

    root = _resolve_root(project_root)
    base_settings = settings or load_settings()
    if normalized_mode:
        resolved_settings = base_settings.model_copy(deep=True)
        resolved_settings.permission.mode = PermissionMode(normalized_mode)
    else:
        resolved_settings = base_settings
    checker = build_permission_checker(resolved_settings, cwd=root)

    raw_input: dict[str, object] = {}
    if file_path.strip():
        raw_input["file_path"] = file_path
    if command.strip():
        raw_input["command"] = command
    if content:
        raw_input["content"] = content

    # Lazy import: engine.query pulls in the provider/tool stack, which the
    # policy page must not load just to answer a policy question.
    from iterate_harness.engine.query import (
        _extract_command_paths,
        _extract_permission_command,
        _extract_permission_content,
        _resolve_permission_file_path,
    )

    resolved_path = _resolve_permission_file_path(root, raw_input, None)
    resolved_command = _extract_permission_command(raw_input, None)
    command_paths = (
        _extract_command_paths(resolved_command, root) if resolved_command else None
    )

    if read_only is None:
        inferred: bool | None = None
        for tool in _registered_tools():
            if tool["name"] == tool_name:
                inferred = tool["read_only"]
                break
        if inferred is None:
            resolved_read_only = False
            read_only_source = "assumed"
        else:
            resolved_read_only = inferred
            read_only_source = "inferred"
    else:
        resolved_read_only = read_only
        read_only_source = "explicit"

    resolved_content = None if resolved_read_only else _extract_permission_content(raw_input, None)

    decision = checker.evaluate(
        tool_name,
        is_read_only=resolved_read_only,
        file_path=resolved_path,
        command=resolved_command,
        command_paths=command_paths,
        content=resolved_content,
    )

    if decision.allowed:
        verdict = VERDICT_ALLOWED
    elif decision.requires_confirmation:
        verdict = VERDICT_CONFIRM
    else:
        verdict = VERDICT_DENIED

    effective_mode = resolved_settings.permission.mode.value
    return {
        "tool_name": tool_name,
        "project_root": str(root),
        "generated_at": datetime.datetime.now(UTC).isoformat(),
        "read_only": resolved_read_only,
        "read_only_source": read_only_source,
        "file_path": resolved_path or "",
        "command": resolved_command or "",
        "command_paths": command_paths or [],
        "content_checked": bool(resolved_content),
        "mode": effective_mode,
        "mode_label": MODE_LABELS.get(effective_mode, effective_mode),
        "simulated_mode": normalized_mode,
        "verdict": verdict,
        "allowed": decision.allowed,
        "requires_confirmation": decision.requires_confirmation,
        "reason": decision.reason,
    }


def render_permission_policy(policy: dict[str, Any]) -> str:
    """Render the policy as human-readable terminal text."""
    summary = policy.get("summary") or {}
    lines = [
        f"IterateHarness permission policy — {policy.get('project_root', '')}",
        (
            f"mode: {policy.get('mode')} ({policy.get('mode_label')}) — "
            f"{policy.get('mode_detail')}"
        ),
        (
            f"tools: {summary.get('tools', 0)} · allow: {summary.get('allowed_tools', 0)} · "
            f"deny: {summary.get('denied_tools', 0)} · path rules: {summary.get('path_rules', 0)} "
            f"({summary.get('deny_path_rules', 0)} deny / {summary.get('allow_path_rules', 0)} allow) · "
            f"risk areas: {summary.get('risk_areas', 0)} · forbidden patterns: "
            f"{summary.get('forbidden_patterns', 0)} · sensitive paths: {summary.get('sensitive_paths', 0)}"
        ),
    ]

    problems = policy.get("problems") or []
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

    def section(title: str, values: list[str], empty: str) -> None:
        lines.append("")
        lines.append(f"{title} ({len(values)}):")
        if not values:
            lines.append(f"  {empty}")
            return
        for value in values:
            lines.append(f"  {value}")

    section("Allowed tools", list(policy.get("allowed_tools") or []), "(none — every tool falls through to mode logic)")
    section("Denied tools", list(policy.get("denied_tools") or []), "(none)")
    section(
        "Path rules",
        [
            f"{'allow' if rule.get('allow') else 'deny '}  {rule.get('pattern')}"
            for rule in policy.get("path_rules") or []
        ],
        "(none)",
    )
    section("Denied commands", list(policy.get("denied_commands") or []), "(none)")
    section("Allowed commands", list(policy.get("allowed_commands") or []), "(none)")
    section("Risk areas (mutating calls require confirmation)", list(policy.get("risk_areas") or []), "(none)")
    section("Forbidden content patterns", list(policy.get("forbidden_patterns") or []), "(none)")
    section(
        "Built-in sensitive paths (always denied)",
        list(policy.get("sensitive_paths") or []),
        "(none)",
    )
    return "\n".join(lines)


def render_permission_decision(decision: dict[str, Any]) -> str:
    """Render one simulated decision as human-readable terminal text."""
    verdict = str(decision.get("verdict") or "")
    label = {
        VERDICT_ALLOWED: "ALLOWED",
        VERDICT_CONFIRM: "NEEDS CONFIRMATION",
        VERDICT_DENIED: "DENIED",
    }.get(verdict, verdict.upper())
    lines = [
        f"{label}: {decision.get('tool_name')}",
        f"mode: {decision.get('mode')} ({decision.get('mode_label')})"
        + (f" · simulated: {decision.get('simulated_mode')}" if decision.get("simulated_mode") else ""),
        f"read-only: {decision.get('read_only')} ({decision.get('read_only_source')})",
    ]
    if decision.get("file_path"):
        lines.append(f"path: {decision.get('file_path')}")
    if decision.get("command"):
        lines.append(f"command: {decision.get('command')}")
    if decision.get("command_paths"):
        lines.append(f"paths seen in command: {', '.join(decision.get('command_paths') or [])}")
    if decision.get("content_checked"):
        lines.append("write payload: checked against forbidden patterns")
    lines.append(f"reason: {decision.get('reason')}")
    return "\n".join(lines)


__all__ = [
    "MODE_DETAILS",
    "MODE_LABELS",
    "VERDICT_ALLOWED",
    "VERDICT_CONFIRM",
    "VERDICT_DENIED",
    "build_permission_policy",
    "render_permission_decision",
    "render_permission_policy",
    "simulate_permission_decision",
]
