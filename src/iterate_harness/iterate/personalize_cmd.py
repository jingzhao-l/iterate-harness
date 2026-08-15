"""``ih iterate personalize`` — the skill-parity 9-category wizard.

Python port of the skill's ``iterate_cli/personalize.py`` so both ecosystems
stay byte-compatible on disk:

- **7 structured categories** are written to ``iterate.config.yaml`` under the
  ``personalization`` key (schema-compatible with the skill's
  ``config.schema.json``): ``protected_paths``, ``risk_areas``,
  ``known_intentional``, ``dimension_focus``, ``fix_priority_order``,
  ``forbidden_fixes``, ``extra_validation_commands``.
- **2 free-text categories** (``iterate_notes``, ``code_conventions``) are
  rendered as Markdown and merged into the USER-OWNED region of ``ITERATE.md``
  via header-identified section replacement — manually written sections are
  never touched.
- ``extra_validation_commands`` are merged into ``validation.commands`` and
  their tool prefixes into ``validation.command_whitelist`` (fail-closed: every
  command re-validated against the strict whitelist before persisting).

Security boundary (v2.0.2 semantics, ported verbatim): validation commands
reject shell-chaining metacharacters and only accept pre-approved tool
prefixes. The prefix allowlist can be extended ONLY by the operator via the
``ITERATE_EXTRA_SAFE_COMMAND_PREFIXES`` environment variable — never by
project config.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import onboarding
from .config_loader import CONFIG_FILENAME
from .types import KnownIntentional

#: Personalization schema version (skill parity).
PERSONALIZATION_VERSION = "1.0"

#: Callable used to read interactive user input (injectable for tests).
InputFunc = Callable[[str], str]

#: Module name pattern for extra_validation_commands keys — only
#: alphanumeric, dash, underscore, dot; prevents shell metacharacter
#: injection via module key.
MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

#: Allowed command prefixes for extra_validation_commands (skill parity —
#: 30+ common test/lint/type-check/build tools). Anything not listed is
#: rejected outright; no user-confirmation bypass exists.
KNOWN_SAFE_COMMAND_PREFIXES: tuple[str, ...] = (
    "pytest", "py.test", "unittest", "tox", "nox",
    "ruff", "flake8", "pylint", "mypy", "bandit",
    "black", "isort", "pyupgrade",
    "coverage", "pytest-cov", "pip-audit",
    "npm", "pnpm", "yarn", "npx",
    "tsc", "eslint", "prettier", "jest", "vitest", "ava", "mocha",
    "swift", "swiftc", "xcodebuild", "swift test",
    "cargo", "rustc",
    "go", "gofmt", "golangci-lint",
    "make", "cmake",
    "gradle", "mvn", "java",
    "dotnet",
    "shellcheck", "shfmt",
    "pre-commit",
)

#: Shell metacharacters that enable command chaining — never allowed in a
#: validation command.
FORBIDDEN_COMMAND_CHARS: tuple[str, ...] = (";", "|", "&", "`", "$", ">", "<", "\n", "\r")

#: Operator-level (NOT project-configurable) extension point for the safe
#: prefix allowlist. Only someone able to set the process environment can
#: widen the executable surface.
EXTRA_SAFE_PREFIXES_ENV = "ITERATE_EXTRA_SAFE_COMMAND_PREFIXES"

#: All selectable dimensions in display order (harness defaults == skill).
ALL_DIMENSIONS: list[str] = [
    "correctness",
    "security",
    "performance",
    "architecture",
    "style-tests",
    "tech-debt",
    "spec-compliance",
    "frontend-backend",
    "ui-ux",
]

#: Dimension display names for the selection menus.
DIMENSION_LABELS: dict[str, str] = {
    "correctness": "正确性 / Correctness (critical)",
    "security": "安全 / Security (critical)",
    "performance": "性能 / Performance (high)",
    "architecture": "架构 / Architecture (high)",
    "style-tests": "风格与测试 / Style & Tests (medium)",
    "tech-debt": "技术债 / Tech Debt (medium)",
    "spec-compliance": "规范一致性 / Spec Compliance (high)",
    "frontend-backend": "前后端一致性 / Frontend-Backend (high)",
    "ui-ux": "UI/UX (medium)",
}

#: Headers that mark personalization-generated sections inside the ITERATE.md
#: user-owned region. Used by :func:`merge_user_sections` to replace old
#: personalization content while preserving manually written sections.
PERSONALIZATION_SECTION_HEADERS: tuple[str, ...] = (
    "## 自定义代码约定 / Custom Code Conventions",
    "## 禁区与风险区 / Restricted & Risk Areas",
    "## Iterate 注意点 / Iterate Notes",
    "## 已知意图 / Known Intentional (抑制误报 / Suppress false positives)",
    "## 禁止的修复方式 / Forbidden Fixes",
)

_HEADER_NOTES = "## Iterate 注意点 / Iterate Notes"
_HEADER_CONVENTIONS = "## 自定义代码约定 / Custom Code Conventions"


def _print(message: str = "") -> None:
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Validation-command security (strict whitelist)
# ---------------------------------------------------------------------------


def _operator_extra_prefixes() -> tuple[str, ...]:
    """Operator-approved extra safe prefixes from the environment.

    Tokens containing shell metacharacters (or not starting alphanumeric)
    are dropped, so the result always remains fail-closed.
    """
    raw = os.environ.get(EXTRA_SAFE_PREFIXES_ENV, "")
    if not raw:
        return ()
    tokens = [tok.strip() for tok in re.split(r"[\s,]+", raw) if tok.strip()]
    safe = [tok for tok in tokens if not _has_forbidden_chars(tok) and tok[0].isalnum()]
    return tuple(dict.fromkeys(safe))


def _is_known_safe_command(cmd: str) -> bool:
    """True when cmd starts with a known-safe tool prefix."""
    stripped = cmd.strip()
    if not stripped:
        return False
    first_token = stripped.split(None, 1)[0]
    operator_prefixes = _operator_extra_prefixes()
    # "python -m pytest" style: the effective tool is after -m.
    if first_token in ("python", "python3", "py") and " -m " in stripped:
        parts = stripped.split(" -m ", 1)
        inner = parts[1].strip().split(None, 1)[0]
        return inner in KNOWN_SAFE_COMMAND_PREFIXES or inner in operator_prefixes
    return first_token in KNOWN_SAFE_COMMAND_PREFIXES or first_token in operator_prefixes


def _has_forbidden_chars(cmd: str) -> bool:
    """True when cmd contains shell-chaining metacharacters."""
    return any(ch in cmd for ch in FORBIDDEN_COMMAND_CHARS)


def validate_extra_command(cmd: str) -> tuple[bool, str]:
    """Validate one extra validation command (strict whitelist, fail-closed).

    Returns ``(is_valid, reason)``. When ``is_valid`` is False the caller
    MUST refuse to persist the command.
    """
    if not cmd or not cmd.strip():
        return False, "empty command"
    if _has_forbidden_chars(cmd):
        return False, (
            "command contains forbidden shell metacharacters "
            f"({FORBIDDEN_COMMAND_CHARS}); command chaining is not allowed "
            "in extra validation commands"
        )
    if _is_known_safe_command(cmd):
        return True, ""
    return False, (
        "command does not start with a known-safe tool prefix "
        f"({sorted(KNOWN_SAFE_COMMAND_PREFIXES)[:6]}...); only pre-approved "
        "test/lint/type-check/build tooling is allowed in extra validation "
        "commands. To add a new tool, extend KNOWN_SAFE_COMMAND_PREFIXES "
        "in iterate/personalize_cmd.py, or set the operator-level "
        f"environment variable {EXTRA_SAFE_PREFIXES_ENV}"
    )


# ---------------------------------------------------------------------------
# Data model (9 categories)
# ---------------------------------------------------------------------------


@dataclass
class RiskArea:
    """A fragile file or directory that needs extra care."""

    path: str
    reason: str


@dataclass
class DimensionFocusOverride:
    """Extra focus text appended to a dimension's reviewer prompt."""

    dimension: str
    focus: str


@dataclass
class PersonalizationData:
    """All 9 wizard categories for one project.

    ``iterate_notes`` and ``code_conventions`` are free-form bullets destined
    for the ITERATE.md user-owned region; every other field is a structured
    rule stored under ``personalization`` in iterate.config.yaml.
    """

    protected_paths: list[str] = field(default_factory=list)
    risk_areas: list[RiskArea] = field(default_factory=list)
    known_intentional: list[KnownIntentional] = field(default_factory=list)
    dimension_focus: list[DimensionFocusOverride] = field(default_factory=list)
    fix_priority_order: list[str] = field(default_factory=list)
    forbidden_fixes: list[str] = field(default_factory=list)
    iterate_notes: list[str] = field(default_factory=list)
    code_conventions: list[str] = field(default_factory=list)
    extra_validation_commands: dict[str, list[str]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """True when every category is empty."""
        return not any(
            (
                self.protected_paths,
                self.risk_areas,
                self.known_intentional,
                self.dimension_focus,
                self.fix_priority_order,
                self.forbidden_fixes,
                self.iterate_notes,
                self.code_conventions,
                self.extra_validation_commands,
            )
        )

    def to_config_dict(self) -> dict[str, object]:
        """Serialise the structured fields for iterate.config.yaml."""
        return {
            "version": PERSONALIZATION_VERSION,
            "protected_paths": list(self.protected_paths),
            "risk_areas": [{"path": r.path, "reason": r.reason} for r in self.risk_areas],
            "known_intentional": [
                {"file": k.file, "line": k.line or 0, "dimension": k.dimension, "reason": k.reason}
                for k in self.known_intentional
            ],
            "dimension_focus": [
                {"dimension": d.dimension, "focus": d.focus} for d in self.dimension_focus
            ],
            "fix_priority_order": list(self.fix_priority_order),
            "forbidden_fixes": list(self.forbidden_fixes),
            "extra_validation_commands": {
                module: list(cmds) for module, cmds in self.extra_validation_commands.items()
            },
        }

    def to_user_md_sections(self) -> str:
        """Render every category as Markdown for the ITERATE.md user region."""
        sections: list[str] = []
        if self.code_conventions:
            sections.append(_HEADER_CONVENTIONS + "\n")
            sections.extend(f"- {conv}" for conv in self.code_conventions)
            sections.append("")
        if self.protected_paths or self.risk_areas:
            sections.append("## 禁区与风险区 / Restricted & Risk Areas\n")
            if self.protected_paths:
                sections.append("### 禁区 / Protected (不可修改 / Do not modify)\n")
                sections.extend(f"- `{p}`" for p in self.protected_paths)
                sections.append("")
            if self.risk_areas:
                sections.append("### 风险区 / Risk Areas (改动需审批 / Changes need approval)\n")
                sections.extend(f"- `{r.path}` — {r.reason}" for r in self.risk_areas)
                sections.append("")
        if self.iterate_notes:
            sections.append(_HEADER_NOTES + "\n")
            sections.extend(f"- {note}" for note in self.iterate_notes)
            sections.append("")
        if self.known_intentional:
            sections.append(
                "## 已知意图 / Known Intentional (抑制误报 / Suppress false positives)\n"
            )
            for k in self.known_intentional:
                loc = f"{k.file}:{k.line}" if k.line else k.file
                sections.append(f"- `{loc}` [{k.dimension}] — {k.reason}")
            sections.append("")
        if self.forbidden_fixes:
            sections.append("## 禁止的修复方式 / Forbidden Fixes\n")
            sections.extend(f"- {f}" for f in self.forbidden_fixes)
            sections.append("")
        return "\n".join(sections)


# ---------------------------------------------------------------------------
# ITERATE.md user-region merge
# ---------------------------------------------------------------------------


def _is_personalization_header(line: str) -> bool:
    """Exact-match check so user titles with extra suffixes are preserved."""
    return line.strip() in PERSONALIZATION_SECTION_HEADERS


def merge_user_sections(existing_content: str, new_personalization_md: str) -> str:
    """Replace old personalization sections, preserve manual sections, append new.

    ``existing_content`` is the inner text of the user-owned region (region
    markers excluded). Sections whose header exactly matches a
    personalization-generated header are removed; every other line survives
    verbatim. The freshly rendered personalization Markdown is appended.
    """
    result_lines: list[str] = []
    skipping = False
    for line in existing_content.split("\n"):
        if _is_personalization_header(line):
            skipping = True
            continue
        if skipping and line.strip().startswith("## "):
            skipping = False
            if _is_personalization_header(line):
                skipping = True
                continue
        if skipping:
            continue
        result_lines.append(line)

    cleaned = "\n".join(result_lines).rstrip()
    new_sections = new_personalization_md.strip()
    if not new_sections:
        return cleaned
    if cleaned:
        return f"{cleaned}\n\n{new_sections}\n"
    return f"{new_sections}\n"


def load_personalization_from_iterate_md(project_root: Path) -> tuple[list[str], list[str]]:
    """Read back (iterate_notes, code_conventions) bullets from ITERATE.md.

    Both categories live ONLY in the user-owned region, so they must be
    parsed back from there when the wizard is re-run — otherwise
    ``merge_user_sections`` would silently wipe previously entered content.
    """
    iterate_md = project_root / onboarding.ITERATE_MD_FILENAME
    if not iterate_md.is_file():
        return [], []
    try:
        content = iterate_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    user_content = _inner_user_region(content)
    notes: list[str] = []
    conventions: list[str] = []
    current_section: str | None = None
    for line in user_content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped
            continue
        if not stripped.startswith("- ") or len(stripped) <= 2:
            continue
        item = stripped[2:].strip()
        if current_section == _HEADER_NOTES:
            notes.append(item)
        elif current_section == _HEADER_CONVENTIONS:
            conventions.append(item)
    return notes, conventions


def _inner_user_region(content: str) -> str:
    """Inner text between the user-owned markers (markers excluded)."""
    start = content.find(onboarding.USER_START_MARKER)
    end = content.find(onboarding.USER_END_MARKER)
    if start < 0 or end <= start:
        return ""
    inner_start = start + len(onboarding.USER_START_MARKER)
    return content[inner_start:end]


def update_iterate_md_user_section(project_root: Path, data: PersonalizationData) -> bool:
    """Merge freshly rendered personalization sections into ITERATE.md.

    Splices by the byte-exact region markers — the AI-maintained region and
    any manually written user sections are preserved. Returns True when the
    file was rewritten; False when ITERATE.md is absent/unreadable/malformed
    (the caller keeps the config-only write in that case).
    """
    iterate_md = project_root / onboarding.ITERATE_MD_FILENAME
    if not iterate_md.is_file():
        return False
    try:
        content = iterate_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    start = content.find(onboarding.USER_START_MARKER)
    end = content.find(onboarding.USER_END_MARKER)
    if start < 0 or end <= start:
        return False

    merged = merge_user_sections(_inner_user_region(content), data.to_user_md_sections())
    updated = (
        content[: start + len(onboarding.USER_START_MARKER)]
        + "\n"
        + merged.strip()
        + "\n"
        + content[end:]
    )
    try:
        iterate_md.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Config load / merge / save
# ---------------------------------------------------------------------------


def _parse_known_intentional(raw: object) -> list[KnownIntentional]:
    known: list[KnownIntentional] = []
    if not isinstance(raw, list):
        return known
    for item in raw:
        if not isinstance(item, dict) or "file" not in item:
            continue
        try:
            line = int(item.get("line", 0))
        except (TypeError, ValueError):
            line = 0
        known.append(
            KnownIntentional(
                file=str(item["file"]),
                line=line,
                dimension=str(item.get("dimension", "")),
                reason=str(item.get("reason", "")),
            )
        )
    return known


def _parse_extra_commands(raw: object) -> dict[str, list[str]]:
    extra: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return extra
    for module, cmds in raw.items():
        module_str = str(module)
        if not MODULE_NAME_PATTERN.match(module_str) or not isinstance(cmds, list):
            continue
        valid = _filter_valid_commands(cmds, module_str)
        if valid:
            extra[module_str] = valid
    return extra


def _filter_valid_commands(cmds: list[object], module_str: str) -> list[str]:
    """Fail-closed revalidation of config-sourced commands (skill parity)."""
    valid: list[str] = []
    for cmd in cmds:
        if not isinstance(cmd, str) or not cmd.strip():
            continue
        ok, reason = validate_extra_command(cmd)
        if ok:
            valid.append(cmd)
        else:
            _print(f"  ! 跳过非法验证命令 / skipped invalid command [{module_str}] {cmd!r}: {reason}")
    return valid


def _clean_string_list(raw: object) -> list[str]:
    """Keep only non-empty string entries (defensive against hostile YAML)."""
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item.strip()]


def load_personalization_from_config(config: dict[str, object]) -> PersonalizationData:
    """Parse the ``personalization`` section of a config dict (defensive).

    Accepts either a full config dict (with a ``personalization`` key) or the
    personalization section dict itself (as produced by ``to_config_dict``).
    """
    candidate = config.get("personalization")
    raw = candidate if isinstance(candidate, dict) else config
    if not isinstance(raw, dict):
        return PersonalizationData()

    risk_areas = [
        RiskArea(path=str(item["path"]), reason=str(item.get("reason", "")))
        for item in (raw.get("risk_areas") or [])
        if isinstance(item, dict) and "path" in item
    ]
    dimension_focus = [
        DimensionFocusOverride(
            dimension=str(item["dimension"]), focus=str(item.get("focus", ""))
        )
        for item in (raw.get("dimension_focus") or [])
        if isinstance(item, dict) and "dimension" in item
    ]
    return PersonalizationData(
        protected_paths=_clean_string_list(raw.get("protected_paths")),
        risk_areas=risk_areas,
        known_intentional=_parse_known_intentional(raw.get("known_intentional")),
        dimension_focus=dimension_focus,
        fix_priority_order=_clean_string_list(raw.get("fix_priority_order")),
        forbidden_fixes=_clean_string_list(raw.get("forbidden_fixes")),
        extra_validation_commands=_parse_extra_commands(raw.get("extra_validation_commands")),
    )


def load_existing_personalization(project_root: Path) -> PersonalizationData:
    """Full existing personalization for editing (config + ITERATE.md)."""
    config_path = project_root / CONFIG_FILENAME
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        config = {}
    data = load_personalization_from_config(config if isinstance(config, dict) else {})
    data.iterate_notes, data.code_conventions = load_personalization_from_iterate_md(project_root)
    return data


def merge_personalization_into_config(
    config: dict[str, object], data: PersonalizationData
) -> dict[str, object]:
    """Write structured fields into a config copy + merge extra commands.

    ``extra_validation_commands`` are (re-validated then) merged into
    ``validation.commands`` and their tool prefixes into
    ``validation.command_whitelist`` so schema validation never fails.
    """
    result = dict(config)
    result["personalization"] = data.to_config_dict()
    if not data.extra_validation_commands:
        return result

    validation = dict(result.get("validation") or {})
    commands = dict(validation.get("commands") or {})
    whitelist = [str(w) for w in (validation.get("command_whitelist") or [])]
    for module, cmds in data.extra_validation_commands.items():
        existing = [str(c) for c in (commands.get(module) or [])]
        for cmd in cmds:
            ok, reason = validate_extra_command(cmd)
            if not ok:
                _print(f"  ! 跳过非法验证命令 / skipped invalid command [{module}] {cmd!r}: {reason}")
                continue
            if cmd not in existing:
                existing.append(cmd)
            prefix = cmd.strip().split(None, 1)[0]
            if prefix and prefix not in whitelist:
                whitelist.append(prefix)
        commands[module] = existing
    validation["commands"] = commands
    validation["command_whitelist"] = whitelist
    result["validation"] = validation
    return result


def save_personalization_to_config(project_root: Path, data: PersonalizationData) -> Path:
    """Update the personalization section of iterate.config.yaml in-place.

    Raises ``FileNotFoundError`` when the config file does not exist.
    """
    config_path = project_root / CONFIG_FILENAME
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    try:
        existing = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read {config_path}: {exc}") from exc
    updated = merge_personalization_into_config(
        existing if isinstance(existing, dict) else {}, data
    )
    config_path.write_text(
        yaml.safe_dump(updated, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return config_path


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------


def run_personalize_wizard(
    existing: PersonalizationData | None = None,
    input_func: InputFunc = input,
) -> PersonalizationData | None:
    """Run the 9-step wizard; returns None when cancelled."""
    data = existing or PersonalizationData()
    _print_welcome()
    if not _ask_yes_no("开始个性化配置? / Start personalization?", input_func):
        return None
    _run_steps_1_4(data, input_func)
    _run_steps_5_9(data, input_func)
    if not _confirm_summary(data, input_func):
        return None
    return data


def _run_steps_1_4(data: PersonalizationData, input_func: InputFunc) -> None:
    data.protected_paths = _string_list_step(
        title="禁区 / Protected Files",
        description=(
            "iterate 不得修改的文件或目录（支持 glob）。\n"
            "Files/dirs iterate must never modify (glob patterns supported)."
        ),
        items=data.protected_paths,
        add_prompt="输入 glob 路径 / Enter glob pattern (e.g. legacy/**, vendor/**)",
        input_func=input_func,
    )
    data.risk_areas = _typed_list_step(
        title="风险区 / Risk Areas",
        description="改动需架构审批的文件/目录。\nFiles/dirs where changes require approval.",
        items=data.risk_areas,
        formatter=lambda r: f"`{r.path}` — {r.reason}",
        add_func=_add_risk_area,
        input_func=input_func,
    )
    data.known_intentional = _typed_list_step(
        title="已知意图 / Known Intentional",
        description="抑制特定 finding（文件:行号 + 维度）。\nSuppress findings (file:line + dimension).",
        items=data.known_intentional,
        formatter=lambda k: f"`{k.file}:{k.line or 0}` [{k.dimension}] — {k.reason}",
        add_func=_add_known_intentional,
        input_func=input_func,
    )
    data.dimension_focus = _typed_list_step(
        title="维度定制 / Dimension Focus Overrides",
        description="为特定维度追加 focus 内容。\nAppend extra focus text to specific dimensions.",
        items=data.dimension_focus,
        formatter=lambda d: f"[{d.dimension}] {d.focus}",
        add_func=_add_dimension_focus,
        input_func=input_func,
    )


def _run_steps_5_9(data: PersonalizationData, input_func: InputFunc) -> None:
    data.fix_priority_order = _fix_priority_step(data.fix_priority_order, input_func)
    data.forbidden_fixes = _string_list_step(
        title="禁止的修复方式 / Forbidden Fixes",
        description="iterate 不得使用的修复手法。\nFix approaches iterate must never use.",
        items=data.forbidden_fixes,
        add_prompt="输入禁止方式 / Enter forbidden fix (e.g. 'try-catch 吞错', '# noqa')",
        input_func=input_func,
    )
    data.iterate_notes = _string_list_step(
        title="Iterate 注意点 / Iterate Notes",
        description="经验教训、已知陷阱、给 iterate 的提示。\nLessons, pitfalls, tips for iterate.",
        items=data.iterate_notes,
        add_prompt="输入注意点 / Enter note",
        input_func=input_func,
    )
    data.code_conventions = _string_list_step(
        title="自定义代码约定 / Custom Code Conventions",
        description="项目特有的代码规范。\nProject-specific code conventions.",
        items=data.code_conventions,
        add_prompt="输入约定 / Enter convention",
        input_func=input_func,
    )
    data.extra_validation_commands = _validation_commands_step(
        data.extra_validation_commands, input_func
    )


def _string_list_step(
    *,
    title: str,
    description: str,
    items: list[str],
    add_prompt: str,
    input_func: InputFunc,
) -> list[str]:
    """Manage a simple string list with add/remove/skip."""
    current = list(items)
    while True:
        _print_step_header(title, description, current)
        choice = input_func("  └ [a]dd / [r]emove / [s]kip: ").strip().lower()
        if choice in ("s", ""):
            return current
        if choice == "a":
            value = input_func(f"  └ {add_prompt}: ").strip()
            if value:
                current.append(value)
                _print("  ✓ 已添加 / Added")
            else:
                _print("  - 空输入，跳过 / Empty, skipped")
        elif choice == "r" and current:
            index = _read_index(current, input_func)
            if index is not None:
                current.pop(index)
                _print("  ✓ 已删除 / Removed")
        else:
            _print_invalid_choice()


def _typed_list_step(
    *,
    title: str,
    description: str,
    items: list[object],
    formatter: Callable[[object], str],
    add_func: Callable[[InputFunc], object],
    input_func: InputFunc,
) -> list[object]:
    """Manage a typed-object list with add/remove/skip."""
    current = list(items)
    while True:
        _print_step_header(title, description, [formatter(item) for item in current])
        choice = input_func("  └ [a]dd / [r]emove / [s]kip: ").strip().lower()
        if choice in ("s", ""):
            return current
        if choice == "a":
            new_item = add_func(input_func)
            if new_item is not None:
                current.append(new_item)
                _print("  ✓ 已添加 / Added")
        elif choice == "r" and current:
            index = _read_index(current, input_func)
            if index is not None:
                current.pop(index)
                _print("  ✓ 已删除 / Removed")
        else:
            _print_invalid_choice()


def _fix_priority_step(current_order: list[str], input_func: InputFunc) -> list[str]:
    """Reorder dimension fix priority (high → low)."""
    _print()
    _print("▶ 优先修复顺序 / Fix Priority Order")
    _print("  指定维度修复优先级（从高到低）。留空跳过保持默认。")
    _print("  Specify dimension fix priority (high to low). Leave empty to skip.")
    if current_order:
        _print("  当前顺序 / Current order:")
        for i, dim in enumerate(current_order, 1):
            _print(f"    {i}. {DIMENSION_LABELS.get(dim, dim)}")
    _print("  可用维度 / Available dimensions:")
    for i, dim in enumerate(ALL_DIMENSIONS, 1):
        _print(f"    {i}. {DIMENSION_LABELS[dim]}")
    raw = input_func("  └ 维度编号（逗号分隔）/ numbers (high to low): ").strip()
    if not raw:
        return list(current_order)
    selected = _parse_dimension_numbers(raw)
    if not selected:
        _print("  ! 无效输入，保持原顺序 / Invalid input, keeping current order.")
        return list(current_order)
    _print("  新顺序 / New order:")
    for i, dim in enumerate(selected, 1):
        _print(f"    {i}. {DIMENSION_LABELS.get(dim, dim)}")
    if _ask_yes_no("确认? / Confirm?", input_func, default=True):
        return selected
    return list(current_order)


def _validation_commands_step(
    current: dict[str, list[str]], input_func: InputFunc
) -> dict[str, list[str]]:
    """Manage extra validation commands (strict whitelist enforced on add)."""
    result = {module: list(cmds) for module, cmds in current.items()}
    while True:
        _print()
        _print("▶ 补充验证命令 / Extra Validation Commands")
        _print("  项目特有的验证命令，会合并到 validation.commands。")
        _print("  Project-specific validation commands, merged into validation.commands.")
        if result:
            for module, cmds in result.items():
                _print(f"    [{module}]")
                for cmd in cmds:
                    _print(f"      - {cmd}")
        else:
            _print("    (空 / empty)")
        choice = input_func("  └ [a]dd / [r]emove / [s]kip: ").strip().lower()
        if choice in ("s", ""):
            return result
        if choice == "a":
            _add_extra_command(result, input_func)
        elif choice == "r" and result:
            modules = list(result.keys())
            _print("  选择模块 / Select module:")
            for i, module in enumerate(modules, 1):
                _print(f"    {i}. {module}")
            index = _read_index(modules, input_func, prompt="模块编号 / Module number")
            if index is not None:
                result.pop(modules[index])
                _print("  ✓ 已删除 / Removed")
        else:
            _print_invalid_choice()


def _add_extra_command(result: dict[str, list[str]], input_func: InputFunc) -> None:
    module = input_func("  └ 模块名 / Module name (e.g. python, swift): ").strip()
    if not module:
        _print("  - 空模块名，跳过 / Empty module, skipped")
        return
    if not MODULE_NAME_PATTERN.match(module):
        _print("  ! 无效模块名（仅允许字母、数字、._-）/ Invalid module name")
        return
    cmd = input_func(f"  └ {module} 命令 / command: ").strip()
    if not cmd:
        _print("  - 空命令，跳过 / Empty command, skipped")
        return
    ok, reason = validate_extra_command(cmd)
    if not ok:
        _print(f"  ✗ 拒绝 / Rejected [{cmd}]: {reason}")
        return
    existing = result.get(module, [])
    if cmd in existing:
        _print("  - 命令已存在 / Command already exists")
        return
    existing.append(cmd)
    result[module] = existing
    _print("  ✓ 已添加 / Added")


def _add_risk_area(input_func: InputFunc) -> RiskArea | None:
    path = input_func("  └ 路径 / Path (e.g. src/auth/): ").strip()
    if not path:
        return None
    reason = input_func("  └ 原因 / Reason: ").strip() or "(未说明 / unspecified)"
    return RiskArea(path=path, reason=reason)


def _add_known_intentional(input_func: InputFunc) -> KnownIntentional | None:
    """Collect one KnownIntentional entry; None on cancel/invalid input."""
    file_path = input_func("  └ 文件路径 / File path (e.g. db/queries.py): ").strip()
    if not file_path:
        return None
    line_str = input_func(
        "  └ 行号 / Line number (0 或留空表示整个文件 / 0 or empty for whole file): "
    ).strip()
    try:
        line = int(line_str) if line_str else 0
    except ValueError:
        line = 0
    _print_dimensions()
    dim_str = input_func("  └ 维度编号 / Dimension number: ").strip()
    try:
        dim_index = int(dim_str) - 1
    except ValueError:
        _print("  ! 无效输入，已取消 / Invalid input, cancelled")
        return None
    if not 0 <= dim_index < len(ALL_DIMENSIONS):
        _print("  ! 无效维度编号，已取消 / Invalid dimension number, cancelled")
        return None
    reason = input_func("  └ 原因 / Reason: ").strip() or "(未说明 / unspecified)"
    return KnownIntentional(
        file=file_path, line=line, dimension=ALL_DIMENSIONS[dim_index], reason=reason
    )


def _add_dimension_focus(input_func: InputFunc) -> DimensionFocusOverride | None:
    _print_dimensions()
    dim_str = input_func("  └ 维度编号 / Dimension number: ").strip()
    try:
        dim_index = int(dim_str) - 1
    except ValueError:
        return None
    if not 0 <= dim_index < len(ALL_DIMENSIONS):
        return None
    dimension = ALL_DIMENSIONS[dim_index]
    focus = input_func(f"  └ 追加 focus 内容 / Extra focus text for [{dimension}]: ").strip()
    if not focus:
        return None
    return DimensionFocusOverride(dimension=dimension, focus=focus)


def _existing_entry_count(data: PersonalizationData) -> int:
    structured = (
        len(data.protected_paths)
        + len(data.risk_areas)
        + len(data.known_intentional)
        + len(data.dimension_focus)
        + len(data.fix_priority_order)
        + len(data.forbidden_fixes)
    )
    return structured + len(data.iterate_notes) + len(data.code_conventions)


def run_personalize(
    project_root: str | Path | None = None, *, input_func: InputFunc = input
) -> int:
    """``ih iterate personalize`` orchestration; returns a process exit code."""
    root = Path(project_root) if project_root else Path.cwd()
    if not onboarding.is_onboarded(root):
        _print("Not onboarded — run `ih iterate onboard` first.")
        return 1
    if not (root / CONFIG_FILENAME).is_file():
        _print(f"{CONFIG_FILENAME} not found — run `ih iterate onboard` first.")
        return 1

    existing = load_existing_personalization(root)
    _print(f"Loaded existing personalization: {_existing_entry_count(existing)} entries")
    data = run_personalize_wizard(existing=existing, input_func=input_func)
    if data is None:
        _print("Cancelled — nothing written.")
        return 1

    try:
        config_path = save_personalization_to_config(root, data)
    except (FileNotFoundError, ValueError, OSError) as exc:
        _print(f"Save failed: {exc}")
        return 1

    md_updated = update_iterate_md_user_section(root, data)
    _print("\nPersonalization saved!")
    _print(f"  - updated: {config_path}")
    if md_updated:
        _print(f"  - updated: {root / onboarding.ITERATE_MD_FILENAME} (user-owned region)")
    return 0


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _print_welcome() -> None:
    _print()
    _print("═" * 62)
    _print("Iterate Harness — 个性化配置 / Personalization")
    _print("═" * 62)
    _print("捕获 AI 扫描不到的项目专属约束 / Capture project-specific constraints")
    _print("本向导收集 9 类个性化配置 / This wizard collects 9 categories:")
    categories = (
        "禁区/保护文件 — iterate 不得修改",
        "风险区 — 改动需架构审批",
        "已知意图 — 抑制误报",
        "维度定制 — 追加 focus",
        "优先修复顺序 — 修复优先级",
        "禁止的修复方式 — 不可使用的修复手法",
        "Iterate 注意点 — 经验教训",
        "自定义代码约定 — 项目特有规范",
        "补充验证命令 — 合并到 validation.commands",
    )
    for i, item in enumerate(categories, 1):
        _print(f"  {i}. {item}")
    _print("每步可跳过。结构化规则写入 iterate.config.yaml，自由文本写入 ITERATE.md 用户区。")


def _print_step_header(title: str, description: str, items: list[object]) -> None:
    _print()
    _print(f"▶ {title}")
    _print(f"  {description.replace(chr(10), chr(10) + '  ')}")
    if items:
        for i, item in enumerate(items, 1):
            _print(f"    {i}. {item}")
    else:
        _print("    (空 / empty)")


def _print_dimensions() -> None:
    _print("  选择维度 / Select dimension:")
    for i, dim in enumerate(ALL_DIMENSIONS, 1):
        _print(f"    {i}. {DIMENSION_LABELS[dim]}")


def _read_index(
    items: list[object], input_func: InputFunc, prompt: str = "删除编号 / Remove number"
) -> int | None:
    """Read a 1-based index; None when invalid or out of range."""
    raw = input_func(f"  └ {prompt} (1-{len(items)}): ").strip()
    try:
        index = int(raw) - 1
    except ValueError:
        _print("  ✗ 无效输入 / Invalid input")
        return None
    if 0 <= index < len(items):
        return index
    _print(f"  ✗ 超出范围 / Out of range (1-{len(items)})")
    return None


def _parse_dimension_numbers(raw: str) -> list[str]:
    """Parse comma-separated dimension numbers (1-based) into dimension keys."""
    result: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            num = int(part)
        except ValueError:
            return []
        if not 1 <= num <= len(ALL_DIMENSIONS):
            return []
        dim = ALL_DIMENSIONS[num - 1]
        if dim not in result:
            result.append(dim)
    return result


def _confirm_summary(data: PersonalizationData, input_func: InputFunc) -> bool:
    _print()
    _print("▶ 确认个性化配置 / Confirm Personalization")
    counts = (
        ("禁区 / Protected", len(data.protected_paths)),
        ("风险区 / Risk areas", len(data.risk_areas)),
        ("已知意图 / Intentional", len(data.known_intentional)),
        ("维度定制 / Dim focus", len(data.dimension_focus)),
        ("优先顺序 / Fix priority", len(data.fix_priority_order)),
        ("禁止方式 / Forbidden", len(data.forbidden_fixes)),
        ("注意点 / Notes", len(data.iterate_notes)),
        ("代码约定 / Conventions", len(data.code_conventions)),
        (
            "验证命令 / Extra cmds",
            sum(len(cmds) for cmds in data.extra_validation_commands.values()),
        ),
    )
    for label, count in counts:
        _print(f"  {label}: {count}")
    if data.is_empty():
        _print("  ! 所有类别为空 / All categories empty")
    return _ask_yes_no("确认保存? / Confirm and save?", input_func)


def _ask_yes_no(question: str, input_func: InputFunc, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input_func(f"{question} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _print_invalid_choice() -> None:
    _print("  ! 请输入 a / r / s / Please enter a / r / s")


__all__ = [
    "ALL_DIMENSIONS",
    "DIMENSION_LABELS",
    "EXTRA_SAFE_PREFIXES_ENV",
    "FORBIDDEN_COMMAND_CHARS",
    "KNOWN_SAFE_COMMAND_PREFIXES",
    "MODULE_NAME_PATTERN",
    "PERSONALIZATION_SECTION_HEADERS",
    "PERSONALIZATION_VERSION",
    "DimensionFocusOverride",
    "PersonalizationData",
    "RiskArea",
    "load_existing_personalization",
    "load_personalization_from_config",
    "load_personalization_from_iterate_md",
    "merge_personalization_into_config",
    "merge_user_sections",
    "run_personalize",
    "run_personalize_wizard",
    "save_personalization_to_config",
    "update_iterate_md_user_section",
    "validate_extra_command",
]
