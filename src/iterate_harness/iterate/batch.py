"""Unattended iterate scenarios: scheduled changed-only reviews and
multi-repo batch reviews with a ranking.

Two entry points, both riding on the headless print pipeline:

- **Schedule** — register/remove a cron job that runs a changed-only quick
  review on this repo (only new findings surface via the trend library).
- **Batch** — run one changed-only (or full) quick review per repo,
  sequentially, then rank the repos by severity-weighted findings score.
"""

from __future__ import annotations

import contextlib
import io
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iterate_harness.iterate import ci_report, config_loader, decision_log, git_scope, prompts

#: Canonical cron job name for the scheduled changed-only quick review.
ITERATE_CRON_JOB_NAME = "iterate.review-changed"

#: Long default timeout for scheduled reviews (multi-round agent loops).
DEFAULT_SCHEDULE_TIMEOUT_SECONDS = 3600

#: Severity weights for the batch ranking score (higher = worse).
SEVERITY_WEIGHTS: dict[str, int] = {"critical": 10, "high": 5, "medium": 2, "low": 1}

#: How much captured per-repo output tail to keep for diagnostics.
_OUTPUT_TAIL_CHARS = 500


# ---------------------------------------------------------------------------
# Scheduled changed-only review (cron)
# ---------------------------------------------------------------------------


def build_scheduled_command(*, ref: str, rounds: int, mode: str) -> str:
    """Build the shell command a scheduled quick review runs."""
    git_scope.validate_ref(ref)
    subcommand = "run" if mode == "normal" else "review"
    return (
        f"ih iterate {subcommand} --changed --clean-ok "
        f"--ref {ref} --rounds {rounds}"
    )


def install_schedule(
    *,
    cwd: str,
    schedule: str,
    ref: str = git_scope.DEFAULT_REF,
    rounds: int = 3,
    mode: str = "dry-run",
    timeout: int = DEFAULT_SCHEDULE_TIMEOUT_SECONDS,
    timezone: str | None = None,
) -> dict[str, Any]:
    """Register (or replace) the scheduled quick-review cron job.

    ``timezone`` (IANA name, e.g. ``Asia/Shanghai``) evaluates the cron
    expression in that local zone; the default is UTC. Raises ``ValueError``
    for an invalid cron expression, ref, or timezone.
    """
    from iterate_harness.services.cron import (
        get_cron_job,
        upsert_cron_job,
        validate_cron_expression,
        validate_timezone,
    )

    if not validate_cron_expression(schedule):
        raise ValueError(f"invalid cron expression: {schedule!r} (5-field cron)")
    if mode not in ("dry-run", "normal"):
        raise ValueError(f"mode must be dry-run|normal (got {mode!r})")
    if timezone is not None and not validate_timezone(timezone):
        raise ValueError(
            f"unknown timezone: {timezone!r} (expected an IANA name like Asia/Shanghai)"
        )
    command = build_scheduled_command(ref=ref, rounds=rounds, mode=mode)
    upsert_cron_job(
        {
            "name": ITERATE_CRON_JOB_NAME,
            "schedule": schedule,
            "command": command,
            "cwd": cwd,
            "timeout": max(1, int(timeout)),
            **({"timezone": timezone} if timezone else {}),
        }
    )
    stored = get_cron_job(ITERATE_CRON_JOB_NAME)
    assert stored is not None  # upsert just wrote it
    return stored


def remove_schedule() -> bool:
    """Remove the scheduled quick-review job; returns True when it existed."""
    from iterate_harness.services.cron import delete_cron_job

    return delete_cron_job(ITERATE_CRON_JOB_NAME)


def schedule_status() -> dict[str, Any] | None:
    """Return the job dict plus its last execution entry, or None."""
    from iterate_harness.services.cron import get_cron_job
    from iterate_harness.services.cron_scheduler import load_history

    job = get_cron_job(ITERATE_CRON_JOB_NAME)
    if job is None:
        return None
    history = load_history(limit=1, job_name=ITERATE_CRON_JOB_NAME)
    return {"job": job, "lastRun": history[-1] if history else None}


# ---------------------------------------------------------------------------
# Batch multi-repo review + ranking
# ---------------------------------------------------------------------------


def _repo_label(repo: Path) -> str:
    return repo.resolve().name


def _severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in SEVERITY_WEIGHTS}
    for finding in findings:
        key = str(finding.get("severity") or "").strip().lower()
        if key in counts:
            counts[key] += 1
    return counts


def repo_score(severity: dict[str, int]) -> int:
    """Severity-weighted score used to rank repos (higher = worse)."""
    return sum(SEVERITY_WEIGHTS[key] * severity.get(key, 0) for key in SEVERITY_WEIGHTS)


def _latest_summary(repo_root: Path) -> ci_report.ReportSummary:
    entries = decision_log.read_entries(str(repo_root))
    return ci_report.ReportSummary.from_entry(ci_report.latest_report_entry(entries))


def _reviewed_record(repo: Path, duration_s: float) -> dict[str, Any]:
    summary = _latest_summary(repo)
    severity = _severity_counts(summary.findings)
    return {
        "repo": _repo_label(repo),
        "path": str(repo),
        "status": "reviewed",
        "verdict": summary.verdict,
        "totalFindings": summary.total_findings,
        "severity": severity,
        "score": repo_score(severity),
        "durationSeconds": round(duration_s, 1),
    }


def _base_record(repo: Path, status: str, note: str) -> dict[str, Any]:
    return {
        "repo": _repo_label(repo),
        "path": str(repo),
        "status": status,
        "verdict": "-",
        "totalFindings": 0,
        "severity": {key: 0 for key in SEVERITY_WEIGHTS},
        "score": 0,
        "durationSeconds": 0.0,
        "note": note,
    }


async def _review_one_repo(
    repo: Path, *, ref: str, rounds: int, full: bool, mode: str
) -> dict[str, Any]:
    """Run one headless quick review and return its ranking record."""
    if not repo.is_dir():
        return _base_record(repo, "error", "not a directory")
    changed_files: list[str] | None = None
    if not full:
        try:
            changed_files = git_scope.collect_changed_files(repo, ref) or None
        except ValueError as exc:
            return _base_record(repo, "error", f"invalid ref: {exc}")
        if changed_files is None:
            return _base_record(repo, "clean", f"no changes vs {ref}")
    goal = config_loader.load_effective_config(str(repo)).config.goal
    kickoff = (
        prompts.normal_kickoff(goal, rounds, changed_files, cwd=str(repo))
        if mode == "normal"
        else prompts.dry_run_kickoff(goal, rounds, changed_files, cwd=str(repo))
    )
    from iterate_harness.ui.app import run_print_mode

    started = time.monotonic()
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            await run_print_mode(
                prompt=kickoff, cwd=str(repo), permission_mode="full_auto"
            )
    except Exception as exc:  # noqa: BLE001 - one repo must not kill the batch
        tail = buffer.getvalue()[-_OUTPUT_TAIL_CHARS:]
        note = f"{type(exc).__name__}: {exc}" + (f" | tail: {tail}" if tail else "")
        return _base_record(repo, "error", note)
    return _reviewed_record(repo, time.monotonic() - started)


async def run_batch(
    *,
    repos: list[str],
    ref: str = git_scope.DEFAULT_REF,
    rounds: int = 3,
    full: bool = False,
    mode: str = "dry-run",
) -> list[dict[str, Any]]:
    """Run the quick review sequentially across ``repos`` (unchanged order)."""
    records: list[dict[str, Any]] = []
    for raw in repos:
        repo = Path(raw).expanduser()
        records.append(
            await _review_one_repo(repo, ref=ref, rounds=rounds, full=full, mode=mode)
        )
    return records


def rank_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort records worst-first: reviewed by score, then clean/error last."""
    status_rank = {"reviewed": 0, "clean": 1, "error": 2}
    return sorted(
        records,
        key=lambda r: (
            status_rank.get(str(r.get("status")), 3),
            -int(r.get("score") or 0),
            -int(r.get("totalFindings") or 0),
            str(r.get("repo")),
        ),
    )


def render_ranking(records: list[dict[str, Any]], *, ran_at: datetime | None = None) -> str:
    """Render the batch ranking table (repos sorted worst-first)."""
    moment = ran_at or datetime.now(UTC)
    header = (
        f"iterate batch ranking — {len(records)} repo(s), generated {moment.isoformat()}"
    )
    columns = ["repo", "status", "findings", "c/h/m/l", "score", "verdict", "note"]
    rows = []
    for record in rank_records(records):
        severity = record.get("severity") or {}
        severity_text = "/".join(
            str(severity.get(key, 0)) for key in ("critical", "high", "medium", "low")
        )
        rows.append(
            [
                str(record.get("repo")),
                str(record.get("status")),
                str(record.get("totalFindings", 0)),
                severity_text,
                str(record.get("score", 0)),
                str(record.get("verdict", "-")),
                str(record.get("note", ""))[:_OUTPUT_TAIL_CHARS],
            ]
        )
    widths = [
        max(len(columns[i]), *(len(row[i]) for row in rows)) if rows else len(columns[i])
        for i in range(len(columns))
    ]

    def _fmt(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    lines = [header, ""]
    lines.append(_fmt(columns))
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(_fmt(row) for row in rows)
    return "\n".join(lines)
