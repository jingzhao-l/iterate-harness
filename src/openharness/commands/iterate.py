"""``/iterate`` slash command handler (registered in commands/registry.py).

Subcommands:
- ``/iterate`` or ``/iterate status`` — effective config + decision-log summary
- ``/iterate review [--changed] [--ref <ref>]`` — kick off the canonical dry-run loop
- ``/iterate run [--changed] [--ref <ref>]`` — kick off the canonical normal-mode autonomous loop
- ``/iterate resume`` — continue the last finished run from its decision log
- ``/iterate log [n]`` — tail the decision log (default 20 entries)
- ``/iterate report`` — render the final report entry from the decision log
- ``/iterate config`` — show the effective config
- ``/iterate validate <command>`` — run one preconfigured validation command

``--changed`` switches review/run into a changed-only quick review pinned to
the files that differ from ``--ref`` (default ``HEAD``).

Kickoffs submit a canonical prompt; the engine-level IterateLoopPolicy
(auto-attached by QueryEngine) enforces convergence deterministically.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from openharness.iterate import (
    ci_report,
    config_loader,
    decision_log,
    git_scope,
    prompts,
    trend_store,
)
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
    for name in sorted(cfg.dimension_resources):
        rendered = config_loader.resources_to_dict(cfg.dimension_resources[name])
        if rendered:
            lines.append(f"dimension resources [{name}]: {rendered}")
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


def _parse_changed_flags(rest: list[str]) -> tuple[bool, str]:
    """Parse ``--changed`` / ``--ref <ref>`` tokens from a subcommand tail."""
    changed = "--changed" in rest
    ref = git_scope.DEFAULT_REF
    if "--ref" in rest:
        index = rest.index("--ref")
        if index + 1 < len(rest):
            ref = rest[index + 1]
    return changed, ref


def _collect_changed_or_none(cwd: str, ref: str) -> list[str] | None:
    """Collect the changed-file delta; ``None`` means flag absent or unusable.

    Raises ``ValueError`` (propagated to the caller as a friendly message)
    when the ref token is invalid.
    """
    return git_scope.collect_changed_files(cwd, ref) or None


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
        changed, ref = _parse_changed_flags(rest)
        changed_files: list[str] | None = None
        if changed:
            try:
                changed_files = _collect_changed_or_none(cwd, ref)
            except ValueError as exc:
                return _result(message=f"Rejected: {exc}")
            if changed_files is None:
                return _result(
                    message=(
                        f"No changed files detected vs {ref} "
                        "(clean tree / not a git repo) — nothing to quick-review."
                    )
                )
        kickoff = prompts.dry_run_kickoff(effective.config.goal, rounds, changed_files)
        scope_note = (
            f"changed-only, {len(changed_files)} file(s)" if changed_files else "full codebase"
        )
        return _result(
            message=f"Starting dry-run review ({scope_note}, {rounds} round cap)…",
            submit_prompt=kickoff,
        )

    if sub in ("run", "iterate", "loop"):
        effective = config_loader.load_effective_config(cwd)
        rounds = _resolve_rounds(cwd)
        changed, ref = _parse_changed_flags(rest)
        changed_files = None
        if changed:
            try:
                changed_files = _collect_changed_or_none(cwd, ref)
            except ValueError as exc:
                return _result(message=f"Rejected: {exc}")
            if changed_files is None:
                return _result(
                    message=(
                        f"No changed files detected vs {ref} "
                        "(clean tree / not a git repo) — nothing to quick-review."
                    )
                )
        kickoff = prompts.normal_kickoff(effective.config.goal, rounds, changed_files)
        scope_note = (
            f"changed-only, {len(changed_files)} file(s)" if changed_files else "full codebase"
        )
        return _result(
            message=f"Starting autonomous iterate loop ({scope_note}, {rounds} round cap)…",
            submit_prompt=kickoff,
        )

    if sub == "resume":
        from openharness.iterate.last_state import summarize_last_run

        summary = summarize_last_run(cwd)
        if summary is None:
            return _result(
                message="No finished iterate run to resume (.iterate/decision-log.jsonl has no report entry)."
            )
        effective = config_loader.load_effective_config(cwd)
        rounds = _resolve_rounds(cwd)
        kickoff = prompts.resume_kickoff(effective.config.goal, rounds, summary)
        return _result(
            message=(
                f"Resuming last iterate run ({summary['mode']}, stopped at round "
                f"{summary['rounds']}, {summary['totalFindings']} finding(s))…"
            ),
            submit_prompt=kickoff,
        )

    if sub == "log":
        if rest and rest[0] == "trend":
            return _result(message=trend_store.render_trend_summary(trend_store.summarize(cwd)))
        if "--replay" in rest:
            from openharness.iterate import replay as replay_mod

            return _result(
                message=replay_mod.render_replay(decision_log.read_entries(cwd))
            )
        try:
            limit = int(rest[0]) if rest else DEFAULT_LOG_TAIL
        except ValueError:
            limit = DEFAULT_LOG_TAIL
        return _result(message=_format_log_tail(cwd, max(1, limit)))

    if sub == "trend":
        return _result(message=trend_store.render_trend_summary(trend_store.summarize(cwd)))

    if sub == "report":
        entries = decision_log.read_entries(cwd)
        report_entry = ci_report.latest_report_entry(entries)
        if report_entry is None:
            return _result(
                message="No report entry in the decision log yet (run /iterate review or /iterate run first)."
            )
        if "--html" in rest:
            from openharness.iterate import html_report as html_mod

            page = html_mod.build_html_report(entries)
            if page is None:
                return _result(message="No report entry to render as HTML.")
            target = Path(cwd) / ".iterate" / "report.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(page, encoding="utf-8")
            return _result(
                message=(
                    f"HTML report written: {target}\n"
                    + ci_report.render_text(ci_report.ReportSummary.from_entry(report_entry))
                )
            )
        return _result(
            message=ci_report.render_text(ci_report.ReportSummary.from_entry(report_entry))
        )

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
            "Usage: /iterate [status|config|review|run|resume|log|trend|report|validate]\n"
            "- status|config: effective config summary\n"
            "- review [--changed] [--ref <ref>]: dry-run pure review (read-only, convergence-enforced);\n"
            "  --changed pins the loop to files changed vs <ref> (default HEAD)\n"
            "- run [--changed] [--ref <ref>]: autonomous review-fix-validate loop (same flags)\n"
            "- resume: continue the last finished run from its decision log\n"
            "- log [n]: tail the decision log (or `log trend`)\n"
            "- trend: cross-run finding trend (new/fixed/stubborn)\n"
            "- report: render the final report from the decision log\n"
            "- validate <command>: run a preconfigured validation command"
        )
    )
