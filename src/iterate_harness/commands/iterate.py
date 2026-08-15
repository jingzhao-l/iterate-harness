"""``/iterate`` slash command handler (registered in commands/registry.py).

Subcommands:
- ``/iterate`` or ``/iterate status`` — effective config + decision-log summary
- ``/iterate onboard [goal]`` — model-driven project scan that writes ITERATE.md
  (fingerprints auto-recorded on the next review/run, or via ``ih iterate refresh``)
- ``/iterate personalize`` — show personalization state; run ``ih iterate
  personalize`` in a terminal for the interactive 9-category wizard
- ``/iterate review [--changed] [--ref <ref>]`` — kick off the canonical dry-run loop
- ``/iterate run [--changed] [--ref <ref>]`` — kick off the canonical normal-mode autonomous loop
- ``/iterate resume`` — continue the last finished run from its decision log
- ``/iterate log [n]`` — tail the decision log (default 20 entries)
- ``/iterate report`` — render the final report entry from the decision log
- ``/iterate config`` — show the effective config
- ``/iterate doctor`` — skill↔harness dimension-system consistency check
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

from iterate_harness.iterate import (
    ci_report,
    config_loader,
    decision_log,
    git_scope,
    prompts,
    trend_store,
)
from iterate_harness.iterate import validate as validate_mod
from iterate_harness.iterate.settings import (
    IterateSettings,
    effective_review_rounds,
    project_config,
)

_LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Imported lazily to avoid a registry ⇄ iterate-handler import cycle.
    from iterate_harness.commands.registry import CommandContext, CommandResult

DEFAULT_LOG_TAIL = 20


def _result(**kwargs: object):
    """Build a CommandResult with a lazy import (registry loads us first)."""
    from iterate_harness.commands.registry import CommandResult

    return CommandResult(**kwargs)  # type: ignore[arg-type]


def _resolve_rounds(cwd: str) -> int:
    """Round cap = min(kernel cap, project max_rounds)."""
    kernel = IterateSettings()
    try:
        from iterate_harness.config.settings import load_settings

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


def _drift_note(cwd: str) -> str:
    """Non-blocking onboarding drift notice appended to loop start messages."""
    from iterate_harness.iterate.onboarding import check_onboarding_drift

    drift = check_onboarding_drift(cwd)
    if drift is None or not drift.has_drift:
        return ""
    return f"\n⚠ Onboarding drift: {drift.summary()} — run `ih iterate refresh` / `reonboard`."


def _auto_fingerprint_note(cwd: str) -> str:
    """Complete post-onboarding bookkeeping (fingerprints) when pending."""
    from iterate_harness.iterate.onboard_cmd import ensure_onboarding_fingerprints

    if ensure_onboarding_fingerprints(cwd, quiet=True):
        return "\n✓ Recorded onboarding fingerprints into iterate.config.yaml."
    return ""


async def iterate_command_handler(args: str, context: CommandContext) -> CommandResult:
    """Handle ``/iterate [subcommand]``."""
    tokens = args.split()
    sub = tokens[0] if tokens else "status"
    rest = tokens[1:]
    cwd = context.cwd or str(Path.cwd())

    if sub in ("status", "config"):
        log_count = len(decision_log.read_entries(cwd))
        from iterate_harness.iterate.onboard_cmd import render_status_onboarding_lines

        onboarding_lines = "\n".join(render_status_onboarding_lines(cwd))
        return _result(
            message=f"{_format_config(cwd)}\ndecision log: {log_count} entries\n{onboarding_lines}"
        )

    if sub == "onboard":
        from iterate_harness.iterate import init_wizard
        from iterate_harness.iterate import onboarding as onboarding_mod

        if (Path(cwd) / onboarding_mod.ITERATE_MD_FILENAME).exists():
            return _result(
                message=(
                    "ITERATE.md already exists — run `ih iterate reonboard` in a terminal "
                    "to re-scan while preserving your user-owned region."
                )
            )
        profile = init_wizard.detect_project(cwd)
        goal = " ".join(rest).strip() or (
            f"Iterative review for this {profile.languages[0] if profile.languages else 'software'} project"
        )
        kickoff = prompts.onboarding_kickoff(
            project_root=cwd,
            goal=goal,
            dimensions=profile.suggested_dimensions,
            evidence_lines=profile.evidence,
            channel="ai",
            completed_at=onboarding_mod.utc_now_iso(),
        )
        return _result(
            message=(
                "Starting model-driven onboarding scan — the model will explore the "
                "project and write ITERATE.md. Manifest fingerprints are recorded "
                "automatically into iterate.config.yaml on your next review/run "
                "(or immediately via `ih iterate refresh` in a terminal). "
                "Add project-specific constraints afterwards with `ih iterate personalize`."
            ),
            submit_prompt=kickoff,
        )

    if sub == "personalize":
        from iterate_harness.iterate import onboarding as onboarding_mod
        from iterate_harness.iterate import personalize_cmd

        if not onboarding_mod.is_onboarded(cwd):
            return _result(
                message="Not onboarded yet — run `/iterate onboard` (or `ih iterate onboard`) first."
            )
        existing = personalize_cmd.load_existing_personalization(Path(cwd))
        counts = (
            f"protected paths: {len(existing.protected_paths)}",
            f"risk areas: {len(existing.risk_areas)}",
            f"known intentional: {len(existing.known_intentional)}",
            f"dimension focus: {len(existing.dimension_focus)}",
            f"fix priority order: {len(existing.fix_priority_order)}",
            f"forbidden fixes: {len(existing.forbidden_fixes)}",
            f"iterate notes: {len(existing.iterate_notes)}",
            f"code conventions: {len(existing.code_conventions)}",
            f"extra validation commands: {sum(len(c) for c in existing.extra_validation_commands.values())}",
        )
        return _result(
            message=(
                "Current personalization:\n- "
                + "\n- ".join(counts)
                + "\n\nThe interactive 9-category wizard runs in a terminal:\n"
                "  ih iterate personalize\n"
                "Structured rules are written to iterate.config.yaml, free-text notes "
                "to the ITERATE.md user-owned region."
            )
        )

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
        kickoff = prompts.dry_run_kickoff(effective.config.goal, rounds, changed_files, cwd=cwd)
        scope_note = (
            f"changed-only, {len(changed_files)} file(s)" if changed_files else "full codebase"
        )
        return _result(
            message=f"Starting dry-run review ({scope_note}, {rounds} round cap)…"
            + _auto_fingerprint_note(cwd)
            + _drift_note(cwd),
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
        kickoff = prompts.normal_kickoff(effective.config.goal, rounds, changed_files, cwd=cwd)
        scope_note = (
            f"changed-only, {len(changed_files)} file(s)" if changed_files else "full codebase"
        )
        return _result(
            message=f"Starting autonomous iterate loop ({scope_note}, {rounds} round cap)…"
            + _auto_fingerprint_note(cwd)
            + _drift_note(cwd),
            submit_prompt=kickoff,
        )

    if sub == "resume":
        from iterate_harness.iterate.last_state import summarize_last_run

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
            from iterate_harness.iterate import replay as replay_mod

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
            from iterate_harness.iterate import html_report as html_mod

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

    if sub == "init":
        from iterate_harness.iterate import init_wizard

        config_path = init_wizard.existing_config_path(cwd)
        if config_path.exists() and "--force" not in rest:
            return _result(
                message=(
                    f"{init_wizard.CONFIG_FILENAME} already exists — use `/iterate init --force` "
                    "to overwrite, or edit it directly."
                )
            )
        profile = init_wizard.detect_project(cwd)
        config = init_wizard.build_config_dict(
            goal=f"Iterative review for this {profile.languages[0] if profile.languages else 'software'} project",
            dimensions=profile.suggested_dimensions,
            max_rounds=3,
            test_command=profile.test_command,
        )
        lines = [
            f"Detected stack: {', '.join(profile.languages) if profile.languages else 'unknown'}",
            *profile.evidence,
            f"Suggested test command: {profile.test_command or '(none)'}",
            "",
            "--- suggested iterate.config.yaml ---",
            init_wizard.render_config_text(config).rstrip(),
            "--- end ---",
        ]
        if "--write" in rest or "--force" in rest:
            written = init_wizard.write_config(cwd, config)
            lines.append(f"\nWrote {written}")
        else:
            lines.append(
                "\nPreview only — run `/iterate init --write` to accept, or `ih iterate init` for the interactive wizard."
            )
        return _result(message="\n".join(lines))

    if sub == "doctor":
        from iterate_harness.iterate.dimension_check import (
            render_doctor_report,
            run_dimension_doctor,
        )

        report = run_dimension_doctor(cwd)
        return _result(message=render_doctor_report(report))

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
            "Usage: /iterate [status|config|onboard|personalize|review|run|resume|log|trend|report|init|doctor|validate]\n"
            "- status|config: effective config summary\n"
            "- onboard [goal]: model-driven project scan that writes ITERATE.md\n"
            "- personalize: show personalization state (wizard: `ih iterate personalize`)\n"
            "- review [--changed] [--ref <ref>]: dry-run pure review (read-only, convergence-enforced);\n"
            "  --changed pins the loop to files changed vs <ref> (default HEAD)\n"
            "- run [--changed] [--ref <ref>]: autonomous review-fix-validate loop (same flags)\n"
            "- resume: continue the last finished run from its decision log\n"
            "- log [n]: tail the decision log (or `log trend`)\n"
            "- trend: cross-run finding trend (new/fixed/stubborn)\n"
            "- report: render the final report from the decision log\n"
            "- init [--write]: detect the stack and suggest an iterate.config.yaml\n"
            "- doctor: skill↔harness dimension-system consistency check\n"
            "- validate <command>: run a preconfigured validation command"
        )
    )
