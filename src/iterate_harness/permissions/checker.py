"""Permission checking for tool execution."""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from iterate_harness.config.settings import PathRuleConfig, PermissionSettings, Settings
from iterate_harness.permissions.modes import PermissionMode

log = logging.getLogger(__name__)

# Paths that are always denied regardless of permission mode or user config.
# These protect high-value credential and key material from LLM-directed access
# (including via prompt injection).  Patterns use fnmatch syntax and are matched
# against the fully-resolved absolute path produced by the query engine.
SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    # SSH keys and config
    "*/.ssh/*",
    # AWS credentials
    "*/.aws/credentials",
    "*/.aws/config",
    # GCP credentials
    "*/.config/gcloud/*",
    # Azure credentials
    "*/.azure/*",
    # GPG keys
    "*/.gnupg/*",
    # Docker credentials
    "*/.docker/config.json",
    # Kubernetes credentials
    "*/.kube/config",
    # IterateHarness own credential stores
    "*/.iterate-harness/credentials.json",
)


@dataclass(frozen=True)
class PermissionDecision:
    """Result of checking whether a tool invocation may run."""

    allowed: bool
    requires_confirmation: bool = False
    reason: str = ""


@dataclass(frozen=True)
class PathRule:
    """A glob-based path permission rule."""

    pattern: str
    allow: bool  # True = allow, False = deny


class PermissionChecker:
    """Evaluate tool usage against the configured permission mode and rules."""

    def __init__(
        self,
        settings: PermissionSettings,
        *,
        forbidden_content_patterns: tuple[re.Pattern[str], ...] = (),
        risk_area_patterns: tuple[str, ...] = (),
    ) -> None:
        self._settings = settings
        # Iterate hard boundary: mutating write payloads matching any of
        # these regexes are rejected outright (see build_permission_checker).
        self._forbidden_content_patterns = forbidden_content_patterns
        # Iterate hard boundary: any MUTATING tool touching one of these
        # absolute-glob paths requires explicit user confirmation even in
        # full_auto mode (design §11.2.2 "risk_areas 强制审批"). Reads are
        # not gated — only writes that could change risk-area content.
        self._risk_area_patterns = risk_area_patterns
        # Parse path rules from settings
        self._path_rules: list[PathRule] = []
        for rule in getattr(settings, "path_rules", []):
            pattern = getattr(rule, "pattern", None) or (rule.get("pattern") if isinstance(rule, dict) else None)
            allow = getattr(rule, "allow", True) if not isinstance(rule, dict) else rule.get("allow", True)
            if isinstance(pattern, str) and pattern.strip():
                self._path_rules.append(PathRule(pattern=pattern.strip(), allow=allow))
            else:
                log.warning(
                    "Skipping path rule with missing, empty, or non-string 'pattern' field: %r",
                    rule,
                )

    def evaluate(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
        content: str | None = None,
    ) -> PermissionDecision:
        """Return whether the tool may run immediately."""
        # Built-in sensitive path protection — always active, cannot be
        # overridden by user settings or permission mode.  This is a
        # defence-in-depth measure against LLM-directed or prompt-injection
        # driven access to credential files.
        if file_path:
            for candidate_path in _policy_match_paths(file_path):
                for pattern in SENSITIVE_PATH_PATTERNS:
                    if fnmatch.fnmatch(candidate_path, pattern):
                        return PermissionDecision(
                            allowed=False,
                            reason=(
                                f"Access denied: {file_path} is a sensitive credential path "
                                f"(matched built-in pattern '{pattern}')"
                            ),
                        )

        # Iterate forbidden-fix boundary (iterate.forbidden_fix_patterns):
        # a mutating write payload matching any configured regex is rejected
        # outright.  Checked before tool allowlists so it cannot be bypassed.
        if content and self._forbidden_content_patterns:
            for forbidden_pattern in self._forbidden_content_patterns:
                if forbidden_pattern.search(content):
                    return PermissionDecision(
                        allowed=False,
                        reason=(
                            "Content matches forbidden fix pattern "
                            f"'{forbidden_pattern.pattern}' (iterate.forbidden_fix_patterns)"
                        ),
                    )

        # Iterate risk-area boundary (iterate.risk_area_paths + project
        # personalization.risk_areas, design §11.2.2): mutating tools that
        # target a risk-area path require explicit user confirmation even in
        # full_auto mode. Checked before tool allowlists so the gate cannot
        # be bypassed by an allowlisted tool. Reads stay ungated.
        if file_path and self._risk_area_patterns and not is_read_only:
            for candidate_path in _policy_match_paths(file_path):
                for pattern in self._risk_area_patterns:
                    if fnmatch.fnmatch(candidate_path, pattern):
                        return PermissionDecision(
                            allowed=False,
                            requires_confirmation=True,
                            reason=(
                                f"Path {file_path} is in a configured risk area "
                                f"(matched '{pattern}'); explicit approval required"
                            ),
                        )

        # Explicit tool deny list
        if tool_name in self._settings.denied_tools:
            return PermissionDecision(allowed=False, reason=f"{tool_name} is explicitly denied")

        # Explicit tool allow list
        if tool_name in self._settings.allowed_tools:
            return PermissionDecision(allowed=True, reason=f"{tool_name} is explicitly allowed")

        # Check path-level rules (upstream contract: deny rules block BOTH
        # reads and writes — iterate protected_paths piggyback on this,
        # which also shields secrets from model reads, a strict superset of
        # the "block writes, allow reads" design minimum).
        if file_path and self._path_rules:
            for candidate_path in _policy_match_paths(file_path):
                for rule in self._path_rules:
                    if fnmatch.fnmatch(candidate_path, rule.pattern):
                        if not rule.allow:
                            return PermissionDecision(
                                allowed=False,
                                reason=f"Path {file_path} matches deny rule: {rule.pattern}",
                            )

        # Exact-match command allowlist (iterate validation commands etc.):
        # a listed command is trusted as-is; prefixes never match.
        allowed_commands = getattr(self._settings, "allowed_commands", [])
        if command and allowed_commands and command.strip() in allowed_commands:
            return PermissionDecision(
                allowed=True,
                reason="Command is exactly allowlisted",
            )

        # Check command deny patterns (e.g. deny "rm -rf /")
        if command:
            for pattern in getattr(self._settings, "denied_commands", []):
                if isinstance(pattern, str) and fnmatch.fnmatch(command, pattern):
                    return PermissionDecision(
                        allowed=False,
                        reason=f"Command matches deny pattern: {pattern}",
                    )

        # Full auto: allow everything
        if self._settings.mode == PermissionMode.FULL_AUTO:
            return PermissionDecision(allowed=True, reason="Auto mode allows all tools")

        # Read-only tools always allowed
        if is_read_only:
            return PermissionDecision(allowed=True, reason="read-only tools are allowed")

        # Plan mode: block mutating tools
        if self._settings.mode == PermissionMode.PLAN:
            return PermissionDecision(
                allowed=False,
                reason="Plan mode blocks mutating tools until the user exits plan mode",
            )

        # Default mode: require confirmation for mutating tools
        bash_hint = _bash_permission_hint(command)
        reason = (
            "Mutating tools require user confirmation in default mode. "
            "Approve the prompt when asked, or run /permissions full_auto "
            "if you want to allow them for this session."
        )
        if bash_hint:
            reason = f"{reason} {bash_hint}"
        return PermissionDecision(
            allowed=False,
            requires_confirmation=True,
            reason=reason,
        )


def build_permission_checker(settings: Settings) -> PermissionChecker:
    """Build a :class:`PermissionChecker` with iterate security boundaries wired in.

    Auto-assembles the iterate harness settings into the permission layer so
    every construction path (REPL runtime, ``/permissions``, ``/plan``)
    enforces them without manual duplication in settings files:

    - ``settings.iterate.protected_paths`` are appended to the permission
      path rules as deny rules (deduped against patterns the user already
      configured).  Iterate patterns are relative-style globs (``.env``,
      ``secrets/*``); they are normalized to absolute-path globs
      (``*/.env``, ``*/secrets/*``) because the path-rule layer matches
      fully-resolved absolute paths.  Under the upstream path-rule contract
      a deny rule blocks both reads and writes, which is a strict superset
      of the iterate "block writes" design minimum and also shields secrets
      from model reads.
    - ``settings.iterate.forbidden_fix_patterns`` are compiled into regexes
      and enforced against mutating write payloads (``content`` /
      ``new_string`` / ``diff`` / ``patch`` tool inputs).  Invalid regexes are
      skipped with a warning instead of crashing startup.
    - ``settings.iterate.risk_area_paths`` (plus the project's
      ``personalization.risk_areas``) are normalized into absolute-glob
      patterns that gate MUTATING tool calls behind an explicit user
      confirmation — even in full_auto mode (design §11.2.2 hard boundary).

    When ``settings.iterate.enabled`` is ``False`` the permission settings
    pass through untouched.

    Project-config parity (skill semantics): the ``personalization.protected_paths``
    list of the current project's ``iterate.config.yaml`` is ALSO merged into
    the deny rules, so wizard-configured protected paths are enforced as a
    hard boundary — not just a prompt-side instruction.
    """
    permission = settings.permission
    iterate = settings.iterate
    if not iterate.enabled:
        return PermissionChecker(permission)

    merged = permission.model_copy(deep=True)
    existing_patterns = {rule.pattern for rule in merged.path_rules}
    for pattern in (*iterate.protected_paths, *_project_protected_paths()):
        normalized = _to_deny_path_glob(pattern)
        if normalized and normalized not in existing_patterns:
            merged.path_rules.append(PathRuleConfig(pattern=normalized, allow=False))
            existing_patterns.add(normalized)

    compiled_patterns: list[re.Pattern[str]] = []
    for raw_pattern in iterate.forbidden_fix_patterns:
        try:
            compiled_patterns.append(re.compile(raw_pattern))
        except re.error:
            log.warning("Skipping invalid iterate forbidden_fix_pattern: %r", raw_pattern)

    risk_area_patterns: list[str] = []
    for raw in (*iterate.risk_area_paths, *_project_risk_area_paths()):
        normalized = _to_deny_path_glob(raw)
        if normalized and normalized not in risk_area_patterns:
            risk_area_patterns.append(normalized)

    return PermissionChecker(
        merged,
        forbidden_content_patterns=tuple(compiled_patterns),
        risk_area_patterns=tuple(risk_area_patterns),
    )


def _project_protected_paths() -> tuple[str, ...]:
    """``personalization.protected_paths`` from the cwd's iterate.config.yaml.

    Never raises: an unreadable/missing config simply contributes no rules
    (fail-open for availability, the kernel deny list above still applies).
    """
    personalization = _project_personalization()
    raw = personalization.get("protected_paths") if personalization else None
    if not isinstance(raw, list):
        return ()
    return tuple(pattern for pattern in raw if isinstance(pattern, str) and pattern.strip())


def _project_risk_area_paths() -> tuple[str, ...]:
    """``personalization.risk_areas`` from the cwd's iterate.config.yaml.

    Accepts both a list of plain path globs (``["db/*"]``) and a list of
    ``{"path": ..., "reason": ...}`` entries so the wizard's structured shape
    round-trips into the permission gate. Never raises: a missing/unreadable
    config contributes no rules (fail-open).
    """
    personalization = _project_personalization()
    raw = personalization.get("risk_areas") if personalization else None
    if not isinstance(raw, list):
        return ()
    paths: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            paths.append(entry.strip())
        elif isinstance(entry, dict):
            path = entry.get("path")
            if isinstance(path, str) and path.strip():
                paths.append(path.strip())
    return tuple(paths)


def _project_personalization() -> dict[str, object]:
    """The current project's ``personalization`` config block (or ``{}``)."""
    try:
        from iterate_harness.iterate.config_loader import load_effective_config

        personalization = load_effective_config(str(Path.cwd())).config.personalization
    except Exception:  # noqa: BLE001 - permission bootstrap must never crash
        return {}
    return personalization if isinstance(personalization, dict) else {}


def _to_deny_path_glob(pattern: str) -> str | None:
    """Normalize an iterate protected-path pattern to an absolute-path glob.

    The path-rule layer matches fully-resolved absolute paths with fnmatch
    (full-string semantics), while iterate protected paths are relative-style
    globs: ``.env`` means "any .env at any depth", and ``secrets/*`` means
    "anything under a secrets/ directory at any depth".

    - absolute patterns (``/etc/*``) pass through unchanged;
    - patterns containing ``/`` are prefixed with ``*/`` (``secrets/*`` →
      ``*/secrets/*``);
    - bare basename patterns are prefixed with ``*/`` (``.env`` → ``*/.env``,
      which also matches the filesystem root ``/.env`` because fnmatch's
      ``*`` may match an empty string).
    """
    normalized = pattern.strip()
    if not normalized:
        return None
    if normalized.startswith("/"):
        return normalized
    return "*/" + normalized


def _policy_match_paths(file_path: str) -> tuple[str, ...]:
    """Return path forms that should participate in policy matching.

    Directory-scoped tools like ``grep`` and ``glob`` may operate on a root such
    as ``/home/user/.ssh``. Appending a trailing slash lets glob-style deny
    patterns like ``*/.ssh/*`` and ``/etc/*`` match the directory root itself.
    """
    normalized = file_path.rstrip("/")
    if not normalized:
        return (file_path,)
    return (normalized, normalized + "/")


def _bash_permission_hint(command: str | None) -> str:
    if not command:
        return ""
    lowered = command.lower()
    install_markers = (
        "npm install",
        "pnpm install",
        "yarn install",
        "bun install",
        "pip install",
        "uv pip install",
        "poetry install",
        "cargo install",
        "create-next-app",
        "npm create ",
        "pnpm create ",
        "yarn create ",
        "bun create ",
        "npx create-",
        "npm init ",
        "pnpm init ",
        "yarn init ",
    )
    if any(marker in lowered for marker in install_markers):
        return (
            "Package installation and scaffolding commands change the workspace, "
            "so they will not run automatically in default mode."
        )
    return ""
