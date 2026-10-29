"""Scheduled-review route (design §17.3 P10).

``ih iterate schedule`` and ``ih cron`` manage unattended reviews from the
terminal, but the console had no way to answer the two questions an operator
actually asks — "what is scheduled, and did last night's run pass?". This
route exposes the machine-level cron registry, the scheduler's liveness, and
the execution history with bounded output tails. Read-only: it never starts,
stops, adds or removes a job.

The registry and the daemon are machine-global (``~/.iterate-harness/data``),
not per project, so this route deliberately takes no ``project_root``.
"""

from __future__ import annotations

import datetime
from typing import Any, Mapping

from fastapi import APIRouter, Query

from ...services import cron_scheduler
from ...services.cron import load_cron_jobs, next_run_time, validate_cron_expression
from ..schemas import SchedulesView

router = APIRouter(tags=["schedules"])

#: How much of a run's stdout/stderr the page carries (characters, tail end).
TAIL_CHARS = 2000


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


@router.get("/schedules", response_model=SchedulesView)
def get_schedules(limit: int = Query(50, ge=1, le=200)) -> SchedulesView:
    """Scheduler liveness, registered jobs and recent executions (oldest first)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    status = cron_scheduler.scheduler_status()
    jobs = load_cron_jobs()
    history = cron_scheduler.load_history(limit=limit)
    return SchedulesView(
        generated_at=now.isoformat(),
        scheduler=status,
        jobs=[_job_view(job, now) for job in jobs],
        history=[_history_view(entry) for entry in history],
    )


__all__ = ["router"]
