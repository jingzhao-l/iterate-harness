"""Scheduled-review routes (design §17.3 P10).

``ih iterate schedule`` and ``ih cron`` manage unattended reviews from the
terminal, but the console could only *read* the registry: the page answered
"what is scheduled, and did last night's run pass?" while adding, pausing or
removing a plan still required a shell. This module closes that loop.

The registry and daemon are machine-global (``~/.iterate-harness/data``), not
per project, so the views take no ``project_root``. Writing is deliberately
narrower than the CLI:

- the console may only create the canonical ``iterate.review-changed`` job,
  and only for a project root the console is already allowed to serve;
- retargeting that job to a different directory requires ``replace=true``
  (409 otherwise), so a stray click cannot silently move someone's schedule;
- enable/disable and delete work on an existing job by name, which is an
  operator-level action over the same registry the CLI writes;
- every mutation requires ``confirm=true`` and is audited.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Query, Request

from ...iterate import batch as iterate_batch
from ...services import cron_scheduler
from ...services.cron import (
    delete_cron_job,
    get_cron_job,
    load_cron_jobs,
    next_run_time,
    set_job_enabled,
    validate_cron_expression,
)
from ..roots import resolve_project_root
from ..schemas import OperationResult, ScheduleUpsertRequest, SchedulesView
from ..security import AuditLog

router = APIRouter(tags=["schedules"])

#: How much of a run's stdout/stderr the page carries (characters, tail end).
TAIL_CHARS = 2000

#: Longest job name accepted by the mutating routes. The CLI uses
#: ``iterate.review-changed``; the bound keeps a hostile caller from writing
#: an unbounded registry key.
MAX_JOB_NAME = 128


def _parse_iso(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _tail(value: object) -> str:
    text = value if isinstance(value, str) else ""
    return text[-TAIL_CHARS:] if len(text) > TAIL_CHARS else text


def _job_view(job: Mapping[str, Any], now: datetime.datetime) -> dict[str, object]:
    schedule = str(job.get("schedule") or "")
    raw_timezone = job.get("timezone")
    timezone_name = raw_timezone if isinstance(raw_timezone, str) and raw_timezone else None
    valid = validate_cron_expression(schedule)
    next_run: str | None = None
    if valid:
        try:
            # Computed fresh rather than read from ``job["next_run"]``: that
            # stored value is only refreshed when the daemon runs, so a stopped
            # scheduler would otherwise show a "next run" that is already past.
            next_run = next_run_time(schedule, base=now, tz_name=timezone_name).isoformat()
        except ValueError:
            valid = False
    last_run = _parse_iso(job.get("last_run"))
    last_status = job.get("last_status")
    return {
        "name": str(job.get("name") or ""),
        "schedule": schedule,
        "timezone": timezone_name,
        "command": str(job.get("command") or ""),
        "cwd": str(job.get("cwd") or ""),
        "enabled": bool(job.get("enabled", True)),
        "schedule_valid": valid,
        "next_run": next_run,
        "last_run": last_run.isoformat() if last_run is not None else None,
        "last_status": str(last_status) if last_status else None,
    }


def _history_view(entry: Mapping[str, Any]) -> dict[str, object]:
    started = _parse_iso(entry.get("started_at"))
    ended = _parse_iso(entry.get("ended_at"))
    duration: float | None = None
    if started is not None and ended is not None:
        duration = round((ended - started).total_seconds(), 1)
    returncode = entry.get("returncode")
    return {
        "name": str(entry.get("name") or entry.get("jobName") or ""),
        "command": str(entry.get("command") or ""),
        "started_at": started.isoformat() if started is not None else "",
        "ended_at": ended.isoformat() if ended is not None else "",
        "status": str(entry.get("status") or ""),
        "returncode": (
            returncode if isinstance(returncode, int) and not isinstance(returncode, bool) else None
        ),
        "duration_s": duration,
        "stdout_tail": _tail(entry.get("stdout")),
        "stderr_tail": _tail(entry.get("stderr")),
    }


def _require_confirm(confirm: bool, action: str) -> None:
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail=f"{action} requires confirm=true (secondary confirmation)",
        )


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


@router.get("/schedules", response_model=SchedulesView)
def get_schedules(limit: int = Query(50, ge=1, le=200)) -> SchedulesView:
    """Scheduler liveness, registered jobs and recent executions (oldest first)."""
    now = _now()
    return SchedulesView(
        generated_at=now.isoformat(),
        scheduler=cron_scheduler.scheduler_status(),
        jobs=[_job_view(job, now) for job in load_cron_jobs()],
        history=[_history_view(entry) for entry in cron_scheduler.load_history(limit=limit)],
    )


@router.post("/schedules", response_model=OperationResult)
def upsert_schedule(
    body: ScheduleUpsertRequest,
    request: Request,
    confirm: bool = False,
) -> OperationResult:
    """Register or update the canonical scheduled quick review, audited.

    The job is bound to ``body.project_root`` (default: this console's
    project), which must pass the same root allow-list as every other write.
    Moving an existing job to another directory needs ``replace=true``; the
    route answers 409 with the current directory in the message otherwise.
    """
    _require_confirm(confirm, "schedule upsert")
    root = resolve_project_root(request, body.project_root)

    existing = get_cron_job(iterate_batch.ITERATE_CRON_JOB_NAME)
    if existing is not None:
        existing_cwd = str(existing.get("cwd") or "")
        try:
            same_root = bool(existing_cwd) and Path(existing_cwd).resolve() == root
        except OSError:
            same_root = False
        if not same_root and not body.replace:
            raise HTTPException(
                status_code=409,
                detail=(
                    "a scheduled quick review already points at "
                    f"{existing_cwd or '(unknown directory)'}; pass replace=true "
                    f"to move it to {root}"
                ),
            )

    try:
        job = iterate_batch.install_schedule(
            cwd=str(root),
            schedule=body.schedule.strip(),
            ref=body.ref.strip(),
            rounds=body.rounds,
            mode=body.mode,
            timeout=body.timeout,
            timezone=body.timezone.strip() or None,
        )
    except ValueError as exc:
        # Invalid cron / timezone / ref / mode: a caller error, not a 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    AuditLog(root).record(
        "schedule.upsert",
        job["name"],
        summary={
            "schedule": job["schedule"],
            "timezone": job.get("timezone"),
            "cwd": job["cwd"],
            "mode": body.mode,
            "rounds": body.rounds,
            "timeout": job.get("timeout"),
        },
    )

    running = cron_scheduler.is_scheduler_running()
    message = (
        f"已登记计划 {job['name']}（{job['schedule']}）"
        + ("" if running else "；调度器当前未运行，请执行 `ih iterate cron start`")
    )
    return OperationResult(
        status="ok",
        message=message,
        target=str(job["name"]),
        detail={"job": _job_view(job, _now()), "scheduler_running": running},
    )


@router.post("/schedules/enabled", response_model=OperationResult)
def toggle_schedule(
    request: Request,
    name: str = Query(..., min_length=1, max_length=MAX_JOB_NAME),
    enabled: bool = Query(...),
    project_root: str = "",
    confirm: bool = False,
) -> OperationResult:
    """Enable or disable one registered job, audited.

    This is the console's pause switch for unattended reviews: the daemon
    keeps running and every other job keeps firing.
    """
    _require_confirm(confirm, "schedule toggle")
    root = resolve_project_root(request, project_root)
    job = get_cron_job(name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no scheduled job named {name!r}")
    if not set_job_enabled(name, enabled):
        # The registry changed between the read and the write (the daemon
        # pruned/marked it): report the race instead of a phantom success.
        raise HTTPException(status_code=409, detail=f"scheduled job {name!r} disappeared")

    AuditLog(root).record(
        "schedule.toggle",
        name,
        summary={"enabled": enabled, "cwd": str(job.get("cwd") or "")},
    )
    updated = get_cron_job(name) or job
    return OperationResult(
        status="ok",
        message=f"计划 {name} 已{'启用' if enabled else '停用'}",
        target=name,
        detail={"job": _job_view(updated, _now())},
    )


@router.delete("/schedules", response_model=OperationResult)
def remove_schedule(
    request: Request,
    name: str = Query(..., min_length=1, max_length=MAX_JOB_NAME),
    project_root: str = "",
    confirm: bool = False,
) -> OperationResult:
    """Remove one registered job (the daemon will stop firing it), audited."""
    _require_confirm(confirm, "schedule removal")
    root = resolve_project_root(request, project_root)
    job = get_cron_job(name)
    if job is None or not delete_cron_job(name):
        raise HTTPException(status_code=404, detail=f"no scheduled job named {name!r}")

    AuditLog(root).record(
        "schedule.remove",
        name,
        summary={"schedule": job.get("schedule"), "cwd": str(job.get("cwd") or "")},
    )
    return OperationResult(
        status="ok",
        message=f"已删除计划 {name}",
        target=name,
        detail={"job": _job_view(job, _now())},
    )


__all__ = ["router"]
