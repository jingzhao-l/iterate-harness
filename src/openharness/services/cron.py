"""Local cron-style registry helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from openharness.config.paths import get_cron_registry_path
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text


def _cron_lock_path() -> Path:
    path = get_cron_registry_path()
    return path.with_suffix(path.suffix + ".lock")


def load_cron_jobs() -> list[dict[str, Any]]:
    """Load stored cron jobs."""
    path = get_cron_registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def save_cron_jobs(jobs: list[dict[str, Any]]) -> None:
    """Persist cron jobs to disk."""
    atomic_write_text(
        get_cron_registry_path(),
        json.dumps(jobs, indent=2) + "\n",
    )


def validate_cron_expression(expression: str) -> bool:
    """Return True if the expression is a valid cron schedule."""
    return croniter.is_valid(expression)


def validate_timezone(name: str | None) -> bool:
    """Return True when ``name`` is a valid IANA timezone identifier."""
    if not name or not isinstance(name, str):
        return False
    try:
        ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
    return True


def next_run_time(
    expression: str,
    base: datetime | None = None,
    tz_name: str | None = None,
) -> datetime:
    """Return the next run time for a cron expression (UTC-normalized).

    With ``tz_name`` the expression is evaluated in that IANA zone's LOCAL
    time (so ``0 9 * * *`` + ``Asia/Shanghai`` fires at 09:00 Beijing time)
    and the result is converted back to UTC for storage/comparison. Raises
    ``ValueError`` for an unknown zone.
    """
    base = base or datetime.now(UTC)
    if not tz_name:
        return croniter(expression, base).get_next(datetime)
    if not validate_timezone(tz_name):
        raise ValueError(f"unknown timezone: {tz_name!r} (expected an IANA name like Asia/Shanghai)")
    zone = ZoneInfo(tz_name.strip())
    local_next = croniter(expression, base.astimezone(zone)).get_next(datetime)
    return local_next.astimezone(UTC)


def _recompute_next_run(job: dict[str, Any], base: datetime | None = None) -> None:
    """Recompute ``job["next_run"]`` honoring the job's ``timezone``."""
    schedule = job.get("schedule", "")
    tz_name = job.get("timezone")
    if not validate_cron_expression(schedule):
        return
    if tz_name is not None and not validate_timezone(tz_name):
        # Unknown zone stored on an old/broken entry: fall back to UTC.
        tz_name = None
    job["next_run"] = next_run_time(schedule, base=base, tz_name=tz_name).isoformat()


def upsert_cron_job(job: dict[str, Any]) -> None:
    """Insert or replace one cron job.

    Automatically sets ``enabled`` to True and computes ``next_run`` when the
    schedule is a valid cron expression. A ``timezone`` key (IANA name) makes
    the schedule fire in that local zone.
    """
    job.setdefault("enabled", True)
    job.setdefault("created_at", datetime.now(UTC).isoformat())

    _recompute_next_run(job)

    with exclusive_file_lock(_cron_lock_path()):
        jobs = [existing for existing in load_cron_jobs() if existing.get("name") != job.get("name")]
        jobs.append(job)
        jobs.sort(key=lambda item: str(item.get("name", "")))
        save_cron_jobs(jobs)


def delete_cron_job(name: str) -> bool:
    """Delete one cron job by name."""
    with exclusive_file_lock(_cron_lock_path()):
        jobs = load_cron_jobs()
        filtered = [job for job in jobs if job.get("name") != name]
        if len(filtered) == len(jobs):
            return False
        save_cron_jobs(filtered)
    return True


def get_cron_job(name: str) -> dict[str, Any] | None:
    """Return one cron job by name."""
    for job in load_cron_jobs():
        if job.get("name") == name:
            return job
    return None


def set_job_enabled(name: str, enabled: bool) -> bool:
    """Enable or disable a cron job. Returns False if job not found."""
    with exclusive_file_lock(_cron_lock_path()):
        jobs = load_cron_jobs()
        for job in jobs:
            if job.get("name") == name:
                job["enabled"] = enabled
                save_cron_jobs(jobs)
                return True
    return False


def mark_job_run(name: str, *, success: bool) -> None:
    """Update last_run and recompute next_run after a job executes."""
    with exclusive_file_lock(_cron_lock_path()):
        jobs = load_cron_jobs()
        now = datetime.now(UTC)
        for job in jobs:
            if job.get("name") == name:
                job["last_run"] = now.isoformat()
                job["last_status"] = "success" if success else "failed"
                _recompute_next_run(job, base=now)
                save_cron_jobs(jobs)
                return
