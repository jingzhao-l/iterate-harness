"""``/iterate`` slash command handler (registered in commands/registry.py).

Subcommands:
- ``/iterate`` or ``/iterate status`` — effective config + decision-log summary
- ``/iterate review [--dry-run]`` — kick off the canonical dry-run loop
- ``/iterate run`` — kick off the canonical normal-mode autonomous loop
- ``/iterate log [n]`` — tail the decision log (default 20 entries)
- ``/iterate config`` — show the effective config
- ``/iterate validate <command>`` — run one preconfigured validation command

Kickoffs submit a canonical prompt; the engine-level IterateLoopPolicy
(auto-attached by QueryEngine) enforces convergence deterministically.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from openharness.iterate import config_loader, decision_log, prompts
from openharness.iterate import validate as validate_mod
from openharness.iterate.settings import IterateSettings, effective_review_rounds, project_config

_LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Imported lazily to avoid a registry ⇄ iterate-handler import cycle.
    from openharness.commands.registry import CommandContext, CommandResult

DEFAULT_LOG_TAIL = 20


def _result(**kwargs: object):
    """Build a CommandResult with a lazy import (registry loads us first)."""
    from openharness.commands.registry import CommandResult

    return CommandResult(**kwargs)  # type: ignore[arg-type]


def _resolve_rounds(cwd: str) -> int:
    """Round cap = min(kernel cap, project max_rounds)."""
    kernel = IterateSettings()
    try:
        from openharness.config.settings import load_settings

        kernel = load_settings().iterate
    except Exception:  # noqa: BLE001 - slash command must never crash on settings
        _LOG.debug("iterate: falling back to default kernel iterate settings")
    return effective_review_rounds(kernel, project_config(cwd))


def _format_config(cwd: str) -> str:
    effective = config_loader.load_effective_config(cwd)
    cfg = effective.config
    lines = [
        f"config source: {effective.source}",
        f"goal: {cfg.goal}",
        f"dimensions: {', '.join(cfg.dimensions)}",
        f"max rounds: {_resolve_rounds(cwd)} (project max_rounds={cfg.max_rounds})",
        f"validation commands: {len(config_loader.flatten_commands(cfg.validation.commands))} configured",
    ]
    return "\n".join(lines)


def _format_log_tail(cwd: str, limit: int) -> str:
    entries = decision_log.read_entries(cwd)
    if not entries:
        return "Decision log is empty (.iterate/decision-log.jsonl)."
    shown = entries[-limit:]
    lines = [f"decision log: {len(entries)} entries (showing last {len(shown)})"]
    for entry in shown:
        data = json.dumps(entry.data, ensure_ascii=False) if entry.data else ""
        lines.append(f"[{entry.timestamp}] r{entry.round} {entry.type} {data}".rstrip())
    return "\n".join(lines)


async def iterate_command_handler(args: str, context: CommandContext) -> CommandResult:
    """Handle ``/iterate [subcommand]``."""
    tokens = args.split()
    sub = tokens[0] if tokens else "status"
    rest = tokens[1:]
    cwd = context.cwd or str(Path.cwd())

    if sub in ("status", "config"):
        log_count = len(decision_log.read_entries(cwd))
        return _result(message=f"{_format_config(cwd)}\ndecision log: {log_count} entries")

    if sub == "review":
        effective = config_loader.load_effective_config(cwd)
        rounds = _resolve_rounds(cwd)
        kickoff = prompts.dry_run_kickoff(effective.config.goal, rounds)
        return _result(
            message=f"Starting dry-run review ({rounds} round cap)…",
            submit_prompt=kickoff,
        )

    if sub in ("run", "iterate", "loop"):
        effective = config_loader.load_effective_config(cwd)
        rounds = _resolve_rounds(cwd)
        kickoff = prompts.normal_kickoff(effective.config.goal, rounds)
        return _result(
            message=f"Starting autonomous iterate loop ({rounds} round cap)…",
            submit_prompt=kickoff,
        )

    if sub == "log":
        try:
            limit = int(rest[0]) if rest else DEFAULT_LOG_TAIL
        except ValueError:
            limit = DEFAULT_LOG_TAIL
        return _result(message=_format_log_tail(cwd, max(1, limit)))

    if sub == "validate":
        if not rest:
            allowed = config_loader.flatten_commands(
                config_loader.load_effective_config(cwd).config.validation.commands
            )
            listing = "\n".join(f"- {cmd}" for cmd in allowed) or "(none configured)"
            return _result(message=f"Usage: /iterate validate <command>\nConfigured commands:\n{listing}")
        command = " ".join(rest)
        result = await asyncio.to_thread(validate_mod.run_validation, command, cwd)
        if not result.allowed:
            return _result(message=f"Rejected: {result.reject_reason}")
        return _result(
            message=(
                f"exit={result.exit_code} duration={result.duration_ms}ms "
                f"timedOut={result.timed_out}\n[stdout]\n{result.stdout}\n[stderr]\n{result.stderr}"
            )
        )

    return _result(
        message=(
            "Usage: /iterate [status|config|review|run|log|validate]\n"
            "- status|config: effective config summary\n"
            "- review: dry-run pure review (read-only, convergence-enforced)\n"
            "- run: autonomous review-fix-validate loop\n"
            "- log [n]: tail the decision log\n"
            "- validate <command>: run a preconfigured validation command"
        )
    )
