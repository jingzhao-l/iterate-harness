"""Directional-key personalize wizard for the TUI (``/iterate personalize``).

The CLI wizard (``personalize_cmd.run_personalize_wizard``) is
free-text driven; this module re-uses its data model and save chain but
drives every step through the TUI's interactive channels:

- ``ask_select(title, options)`` — the directional-key select modal
  (options are ``{value, label, description?}`` dicts, returns a value).
- ``ask_prompt(question)`` — the free-text question modal (returns str).

Both channels are optional at runtime: when either is missing the slash
command falls back to the summary + CLI pointer. All handlers are
defensive — a cancelled modal (empty/unknown answer) aborts the current
step, never the wizard.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from . import onboarding
from .config_loader import CONFIG_FILENAME
from .personalize_cmd import (
    ALL_DIMENSIONS,
    DimensionFocusOverride,
    PersonalizationData,
    RiskArea,
    validate_extra_command,
)
from .types import KnownIntentional

_LOG = logging.getLogger(__name__)

#: Interactive channel signatures (mirror engine.query AskUserSelect/Prompt).
AskSelect = Callable[[str, list[dict[str, str]]], Awaitable[str]]
AskPrompt = Callable[[str], Awaitable[str]]

# Menu sentinel values (never collide with user text or dimension keys).
VALUE_ADD = "__add__"
VALUE_BACK = "__back__"
VALUE_SAVE = "__save__"
VALUE_CANCEL = "__cancel__"
VALUE_RESET = "__reset__"
VALUE_PREFIX_REMOVE = "__rm__"
VALUE_PREFIX_DIMENSION = "__dim__"

#: Max characters of an entry preview inside a menu label.
LABEL_PREVIEW_LIMIT = 60

#: Category menu definition: (value, label, description).
_CATEGORIES: list[tuple[str, str, str]] = [
    (
        "protected_paths",
        "禁区 / Protected paths",
        "iterate 不得修改的文件或目录 (glob) — files iterate must never modify",
    ),
    (
        "risk_areas",
        "风险区 / Risk areas",
        "改动需审批的文件/目录 — changes need approval",
    ),
    (
        "known_intentional",
        "已知意图 / Known intentional",
        "抑制误报 (file:line + dimension) — suppress false positives",
    ),
    (
        "dimension_focus",
        "维度 focus / Dimension focus",
        "给某维度的 reviewer 追加关注点 — extra focus per dimension",
    ),
    (
        "fix_priority_order",
        "修复优先级 / Fix priority order",
        "决定修复顺序的维度排序 — dimension priority for fixes",
    ),
    (
        "forbidden_fixes",
        "禁止修复方式 / Forbidden fixes",
        "禁止使用的修复手法 — fix strategies that are banned",
    ),
    (
        "iterate_notes",
        "iterate 备注 / Iterate notes",
        "自由文本备注 (写入 ITERATE.md 用户区) — free-form notes",
    ),
    (
        "code_conventions",
        "代码规范 / Code conventions",
        "项目编码规范 (写入 ITERATE.md 用户区) — project conventions",
    ),
    (
        "extra_validation_commands",
        "额外验证命令 / Extra validation commands",
        "按模块追加的白名单验证命令 — whitelisted validation commands",
    ),
]

_CATEGORY_FIELDS = {
    "protected_paths": "protected_paths",
    "risk_areas": "risk_areas",
    "known_intentional": "known_intentional",
    "dimension_focus": "dimension_focus",
    "fix_priority_order": "fix_priority_order",
    "forbidden_fixes": "forbidden_fixes",
    "iterate_notes": "iterate_notes",
    "code_conventions": "code_conventions",
    "extra_validation_commands": "extra_validation_commands",
}


def _preview(text: str) -> str:
    """Shorten an entry for a menu label."""
    flat = " ".join(str(text).split())
    if len(flat) <= LABEL_PREVIEW_LIMIT:
        return flat
    return flat[: LABEL_PREVIEW_LIMIT - 1] + "…"


async def _select(
    ask_select: AskSelect, title: str, options: list[dict[str, str]]
) -> str | None:
    """Await the select modal; None on cancel/exception."""
    try:
        answer = await ask_select(title, options)
    except Exception:  # noqa: BLE001 - a broken modal must never crash the wizard
        _LOG.warning("personalize tui: select channel failed", exc_info=True)
        return None
    return str(answer) if answer is not None else None


async def _prompt(ask_prompt: AskPrompt, question: str) -> str | None:
    """Await the text modal; None on cancel/exception."""
    try:
        answer = await ask_prompt(question)
    except Exception:  # noqa: BLE001
        _LOG.warning("personalize tui: prompt channel failed", exc_info=True)
        return None
    return str(answer).strip() if answer is not None else None


async def _edit_string_list(
    title: str,
    description: str,
    items: list[str],
    ask_select: AskSelect,
    ask_prompt: AskPrompt,
    add_prompt: str,
) -> list[str]:
    """Add/remove flow for a plain string category; returns the new list."""
    result = list(items)
    while True:
        options = [{"value": VALUE_ADD, "label": "+ 添加 / Add…", "description": description}]
        for index, item in enumerate(result):
            options.append(
                {
                    "value": f"{VALUE_PREFIX_REMOVE}{index}",
                    "label": f"- 删除 / Remove: {_preview(item)}",
                }
            )
        options.append({"value": VALUE_BACK, "label": "← 返回 / Back"})
        choice = await _select(ask_select, title, options)
        if choice == VALUE_ADD:
            text = await _prompt(ask_prompt, add_prompt)
            if text:
                result.append(text)
        elif choice is not None and choice.startswith(VALUE_PREFIX_REMOVE):
            index = int(choice[len(VALUE_PREFIX_REMOVE) :])
            if 0 <= index < len(result):
                result.pop(index)
        else:
            return result


async def _select_dimension(ask_select: AskSelect, title: str) -> str | None:
    """Pick one of the canonical dimensions; None on cancel."""
    options = [
        {
            "value": f"{VALUE_PREFIX_DIMENSION}{name}",
            "label": f"{index + 1}. {name}",
        }
        for index, name in enumerate(ALL_DIMENSIONS)
    ]
    choice = await _select(ask_select, title, options)
    if choice is None or not choice.startswith(VALUE_PREFIX_DIMENSION):
        return None
    dimension = choice[len(VALUE_PREFIX_DIMENSION) :]
    return dimension if dimension in ALL_DIMENSIONS else None


async def _edit_risk_areas(
    data: PersonalizationData, ask_select: AskSelect, ask_prompt: AskPrompt
) -> None:
    while True:
        options = [
            {
                "value": VALUE_ADD,
                "label": "+ 添加 / Add…",
                "description": "路径 + 原因 / path + reason",
            }
        ]
        for index, area in enumerate(data.risk_areas):
            options.append(
                {
                    "value": f"{VALUE_PREFIX_REMOVE}{index}",
                    "label": f"- 删除 / Remove: {_preview(f'{area.path} — {area.reason}')}",
                }
            )
        options.append({"value": VALUE_BACK, "label": "← 返回 / Back"})
        choice = await _select(ask_select, "风险区 / Risk areas", options)
        if choice == VALUE_ADD:
            path = await _prompt(ask_prompt, "路径 / Path (e.g. src/auth/):")
            if not path:
                continue
            reason = await _prompt(ask_prompt, "原因 / Reason:") or "(未说明 / unspecified)"
            data.risk_areas.append(RiskArea(path=path, reason=reason))
        elif choice is not None and choice.startswith(VALUE_PREFIX_REMOVE):
            index = int(choice[len(VALUE_PREFIX_REMOVE) :])
            if 0 <= index < len(data.risk_areas):
                data.risk_areas.pop(index)
        else:
            return


async def _edit_known_intentional(
    data: PersonalizationData, ask_select: AskSelect, ask_prompt: AskPrompt
) -> None:
    while True:
        options = [
            {
                "value": VALUE_ADD,
                "label": "+ 添加 / Add…",
                "description": "file:line + dimension + reason",
            }
        ]
        for index, known in enumerate(data.known_intentional):
            location = f"{known.file}:{known.line or 0} [{known.dimension}] — {known.reason}"
            options.append(
                {
                    "value": f"{VALUE_PREFIX_REMOVE}{index}",
                    "label": f"- 删除 / Remove: {_preview(location)}",
                }
            )
        options.append({"value": VALUE_BACK, "label": "← 返回 / Back"})
        choice = await _select(ask_select, "已知意图 / Known intentional", options)
        if choice == VALUE_ADD:
            entry = await _collect_known_intentional(ask_select, ask_prompt)
            if entry is not None:
                data.known_intentional.append(entry)
        elif choice is not None and choice.startswith(VALUE_PREFIX_REMOVE):
            index = int(choice[len(VALUE_PREFIX_REMOVE) :])
            if 0 <= index < len(data.known_intentional):
                data.known_intentional.pop(index)
        else:
            return


async def _collect_known_intentional(
    ask_select: AskSelect, ask_prompt: AskPrompt
) -> KnownIntentional | None:
    file_path = await _prompt(ask_prompt, "文件路径 / File path (e.g. db/queries.py):")
    if not file_path:
        return None
    dimension = await _select_dimension(ask_select, "维度 / Dimension:")
    if dimension is None:
        return None
    line_text = await _prompt(
        ask_prompt, "行号 / Line number (0 或留空 = 整个文件 / empty for whole file):"
    )
    try:
        line = int(line_text) if line_text else 0
    except (TypeError, ValueError):
        line = 0
    reason = await _prompt(ask_prompt, "原因 / Reason:") or "(未说明 / unspecified)"
    return KnownIntentional(file=file_path, line=line, dimension=dimension, reason=reason)


async def _edit_dimension_focus(
    data: PersonalizationData, ask_select: AskSelect, ask_prompt: AskPrompt
) -> None:
    while True:
        options = [
            {
                "value": VALUE_ADD,
                "label": "+ 添加 / Add…",
                "description": "dimension + focus text",
            }
        ]
        for index, focus in enumerate(data.dimension_focus):
            options.append(
                {
                    "value": f"{VALUE_PREFIX_REMOVE}{index}",
                    "label": f"- 删除 / Remove: {_preview(f'{focus.dimension}: {focus.focus}')}",
                }
            )
        options.append({"value": VALUE_BACK, "label": "← 返回 / Back"})
        choice = await _select(ask_select, "维度 focus / Dimension focus", options)
        if choice == VALUE_ADD:
            dimension = await _select_dimension(ask_select, "维度 / Dimension:")
            if dimension is None:
                continue
            focus_text = await _prompt(
                ask_prompt, f"追加 focus 内容 / Focus text for [{dimension}]:"
            )
            if focus_text:
                data.dimension_focus.append(
                    DimensionFocusOverride(dimension=dimension, focus=focus_text)
                )
        elif choice is not None and choice.startswith(VALUE_PREFIX_REMOVE):
            index = int(choice[len(VALUE_PREFIX_REMOVE) :])
            if 0 <= index < len(data.dimension_focus):
                data.dimension_focus.pop(index)
        else:
            return


async def _edit_fix_priority(
    data: PersonalizationData, ask_select: AskSelect, ask_prompt: AskPrompt
) -> None:
    """Reorder via move-to-front; any permutation is reachable."""
    del ask_prompt  # this category needs no free-text input
    while True:
        current = data.fix_priority_order or list(ALL_DIMENSIONS)
        order_preview = " → ".join(current)
        options = [
            {
                "value": f"{VALUE_PREFIX_DIMENSION}{name}",
                "label": f"↑ 置顶 / Move to front: {name}",
            }
            for name in current
        ]
        options.append(
            {
                "value": VALUE_RESET,
                "label": "↺ 重置为默认顺序 / Reset to default order",
                "description": _preview(order_preview),
            }
        )
        options.append({"value": VALUE_BACK, "label": "← 返回 / Back"})
        choice = await _select(
            ask_select, f"修复优先级 / Fix priority — 当前 / current: {order_preview}", options
        )
        if choice is None or choice == VALUE_BACK:
            return
        if choice == VALUE_RESET:
            data.fix_priority_order = list(ALL_DIMENSIONS)
            continue
        if choice.startswith(VALUE_PREFIX_DIMENSION):
            name = choice[len(VALUE_PREFIX_DIMENSION) :]
            if name in current:
                data.fix_priority_order = [name] + [d for d in current if d != name]


async def _edit_extra_commands(
    data: PersonalizationData, ask_select: AskSelect, ask_prompt: AskPrompt
) -> None:
    while True:
        options = [
            {
                "value": VALUE_ADD,
                "label": "+ 添加 / Add…",
                "description": "module + command (whitelist-validated)",
            }
        ]
        for module in sorted(data.extra_validation_commands):
            count = len(data.extra_validation_commands[module])
            options.append(
                {
                    "value": f"{VALUE_PREFIX_REMOVE}{module}",
                    "label": f"- 删除整组 / Remove module [{module}] ({count} cmds)",
                }
            )
        options.append({"value": VALUE_BACK, "label": "← 返回 / Back"})
        choice = await _select(ask_select, "额外验证命令 / Extra validation commands", options)
        if choice == VALUE_ADD:
            await _collect_extra_command(data, ask_prompt)
        elif choice is not None and choice.startswith(VALUE_PREFIX_REMOVE):
            module = choice[len(VALUE_PREFIX_REMOVE) :]
            data.extra_validation_commands.pop(module, None)
        else:
            return


async def _collect_extra_command(data: PersonalizationData, ask_prompt: AskPrompt) -> None:
    """Add one (module, command); invalid commands are re-asked with the reason."""
    module = await _prompt(ask_prompt, "模块名 / Module key (e.g. pytest):")
    if not module:
        return
    while True:
        command = await _prompt(
            ask_prompt, f"[{module}] 命令 / Command (留空结束 / empty to finish):"
        )
        if not command:
            return
        ok, reason = validate_extra_command(command)
        if not ok:
            command = await _prompt(
                ask_prompt,
                f"! 非法命令 / Invalid: {reason}\n重新输入或留空取消 / re-enter or empty to cancel:",
            )
            if not command:
                return
            ok, reason = validate_extra_command(command)
            if not ok:
                continue
        data.extra_validation_commands.setdefault(module, []).append(command)


async def _main_menu(data: PersonalizationData, ask_select: AskSelect) -> str | None:
    options: list[dict[str, str]] = []
    for value, label, description in _CATEGORIES:
        field = _CATEGORY_FIELDS[value]
        entries = getattr(data, field)
        count = sum(len(v) for v in entries.values()) if isinstance(entries, dict) else len(entries)
        options.append(
            {
                "value": value,
                "label": f"{label} ({count})",
                "description": description,
            }
        )
    options.append({"value": VALUE_SAVE, "label": "✓ 保存并完成 / Save & finish"})
    options.append({"value": VALUE_CANCEL, "label": "✗ 取消 / Cancel (discard)"})
    return await _select(
        ask_select, "个性化配置 / Personalize — 选择类别 / pick a category", options
    )


async def _confirm_save(ask_select: AskSelect, data: PersonalizationData) -> str | None:
    """Final gate: save / keep editing / discard."""
    options = [
        {"value": VALUE_SAVE, "label": "✓ 确认保存 / Confirm save"},
        {"value": VALUE_BACK, "label": "← 继续编辑 / Keep editing"},
        {"value": VALUE_CANCEL, "label": "✗ 放弃更改 / Discard"},
    ]
    return await _select(ask_select, "保存个性化配置? / Save personalization?", options)


async def run_tui_personalize(
    existing: PersonalizationData | None,
    *,
    ask_select: AskSelect,
    ask_prompt: AskPrompt,
) -> PersonalizationData | None:
    """Run the directional-key wizard; returns the data to save, or None.

    The caller owns the save chain (config + ITERATE.md user region),
    mirroring ``personalize_cmd.run_personalize``.
    """
    data = copy.deepcopy(existing) if existing is not None else PersonalizationData()
    while True:
        choice = await _main_menu(data, ask_select)
        if choice is None or choice == VALUE_CANCEL:
            return None
        if choice == VALUE_SAVE:
            confirm = await _confirm_save(ask_select, data)
            if confirm == VALUE_SAVE:
                return data
            if confirm == VALUE_CANCEL:
                return None
            continue
        if choice == "protected_paths":
            data.protected_paths = await _edit_string_list(
                "禁区 / Protected paths",
                "iterate 不得修改的文件或目录 (glob) — files iterate must never modify",
                data.protected_paths,
                ask_select,
                ask_prompt,
                "输入 glob 路径 / Enter glob pattern (e.g. legacy/**):",
            )
        elif choice == "risk_areas":
            await _edit_risk_areas(data, ask_select, ask_prompt)
        elif choice == "known_intentional":
            await _edit_known_intentional(data, ask_select, ask_prompt)
        elif choice == "dimension_focus":
            await _edit_dimension_focus(data, ask_select, ask_prompt)
        elif choice == "fix_priority_order":
            await _edit_fix_priority(data, ask_select, ask_prompt)
        elif choice == "forbidden_fixes":
            data.forbidden_fixes = await _edit_string_list(
                "禁止修复方式 / Forbidden fixes",
                "禁止使用的修复手法 — fix strategies that are banned",
                data.forbidden_fixes,
                ask_select,
                ask_prompt,
                "输入禁止的修复方式 / Enter a forbidden fix strategy:",
            )
        elif choice == "iterate_notes":
            data.iterate_notes = await _edit_string_list(
                "iterate 备注 / Iterate notes",
                "自由文本备注 (写入 ITERATE.md 用户区) — free-form notes",
                data.iterate_notes,
                ask_select,
                ask_prompt,
                "输入备注 / Enter a note:",
            )
        elif choice == "code_conventions":
            data.code_conventions = await _edit_string_list(
                "代码规范 / Code conventions",
                "项目编码规范 (写入 ITERATE.md 用户区) — project conventions",
                data.code_conventions,
                ask_select,
                ask_prompt,
                "输入规范条目 / Enter a convention:",
            )
        elif choice == "extra_validation_commands":
            await _edit_extra_commands(data, ask_select, ask_prompt)


def summarize_changes(data: PersonalizationData) -> str:
    """One-line-per-category summary for the save confirmation message."""
    lines = [
        f"protected paths: {len(data.protected_paths)}",
        f"risk areas: {len(data.risk_areas)}",
        f"known intentional: {len(data.known_intentional)}",
        f"dimension focus: {len(data.dimension_focus)}",
        f"fix priority: {' > '.join(data.fix_priority_order) if data.fix_priority_order else 'default'}",
        f"forbidden fixes: {len(data.forbidden_fixes)}",
        f"iterate notes: {len(data.iterate_notes)}",
        f"code conventions: {len(data.code_conventions)}",
        f"extra validation commands: {sum(len(v) for v in data.extra_validation_commands.values())}",
    ]
    return "\n".join(lines)


def project_root_guard(project_root: str | Path) -> tuple[Path, str | None]:
    """Validate the project for the wizard; (root, error_message)."""
    root = Path(project_root)
    if not onboarding.is_onboarded(root):
        return root, "Not onboarded — run `/iterate onboard` first."
    if not (root / CONFIG_FILENAME).is_file():
        return root, f"{CONFIG_FILENAME} not found — run `/iterate onboard` first."
    return root, None
