"""WebUI route tests for GET /api/v1/schedules (design §17.3 P10).

The page exists because the console could not answer "what is scheduled, and
did last night's run pass?" — `ih iterate schedule` / `ih cron` were the only
readers. These tests pin the read model: a fresh next-run even when the daemon
is stopped, bounded output tails, and a corrupt registry that degrades to an
empty list instead of a 500.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.web.api import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the machine-level cron registry/history to a temp dir."""
    directory = tmp_path / "data"
    directory.mkdir()
    monkeypatch.setenv("ITERATE_DATA_DIR", str(directory))
    monkeypatch.setenv("ITERATE_LOGS_DIR", str(tmp_path / "logs"))
    return directory


def _write_jobs(data_dir: Path, jobs: list[dict[str, object]]) -> None:
    (data_dir / "cron_jobs.json").write_text(json.dumps(jobs), encoding="utf-8")


def _write_history(data_dir: Path, entries: list[dict[str, object]]) -> None:
    body = "\n".join(json.dumps(entry) for entry in entries)
    (data_dir / "cron_history.jsonl").write_text(body + "\n", encoding="utf-8")


def test_empty_machine_reports_no_jobs_and_no_history(
    client: TestClient, data_dir: Path
) -> None:
    payload = client.get("/api/v1/schedules").json()
    assert payload["scheduler"]["running"] is False
    assert payload["scheduler"]["total_jobs"] == 0
    assert payload["scheduler"]["enabled_jobs"] == 0
    assert payload["jobs"] == []
    assert payload["history"] == []


def test_registered_job_gets_a_fresh_next_run_and_its_last_result(
    client: TestClient, data_dir: Path
) -> None:
    _write_jobs(
        data_dir,
        [
            {
                "name": "iterate-quick-review",
                "schedule": "0 9 * * *",
                "timezone": "Asia/Shanghai",
                "command": "ih iterate run --changed --clean-ok",
                "cwd": "/tmp/project",
                "enabled": True,
                "last_run": "2026-10-28T01:00:00+00:00",
                "last_status": "success",
            }
        ],
    )

    payload = client.get("/api/v1/schedules").json()
    assert payload["scheduler"]["total_jobs"] == 1
    assert payload["scheduler"]["enabled_jobs"] == 1
    job = payload["jobs"][0]
    assert job["name"] == "iterate-quick-review"
    assert job["schedule"] == "0 9 * * *"
    assert job["timezone"] == "Asia/Shanghai"
    assert job["cwd"] == "/tmp/project"
    assert job["enabled"] is True
    assert job["schedule_valid"] is True
    assert job["last_status"] == "success"
    assert job["last_run"].startswith("2026-10-28T01:00:00")
    next_run = datetime.datetime.fromisoformat(job["next_run"])
    assert next_run > datetime.datetime.now(datetime.timezone.utc)
    # 09:00 Asia/Shanghai is 01:00 UTC, whatever the machine's own zone is.
    assert next_run.astimezone(datetime.timezone.utc).hour == 1
    assert next_run.astimezone(datetime.timezone.utc).minute == 0


def test_invalid_schedule_is_reported_rather_than_crashing(
    client: TestClient, data_dir: Path
) -> None:
    _write_jobs(
        data_dir,
        [{"name": "broken", "schedule": "not a cron", "command": "ih iterate run"}],
    )
    job = client.get("/api/v1/schedules").json()["jobs"][0]
    assert job["schedule_valid"] is False
    assert job["next_run"] is None
    assert job["enabled"] is True


def test_history_is_normalised_with_duration_and_bounded_tails(
    client: TestClient, data_dir: Path
) -> None:
    long_stdout = "x" * 5000
    _write_history(
        data_dir,
        [
            {
                "name": "iterate-quick-review",
                "command": "ih iterate run",
                "started_at": "2026-10-28T01:00:00+00:00",
                "ended_at": "2026-10-28T01:00:12.500000+00:00",
                "status": "success",
                "returncode": 0,
                "stdout": long_stdout,
                "stderr": "warning: something",
            },
            {
                # Legacy writers used ``jobName``; the read model normalises it.
                "jobName": "legacy-job",
                "started_at": "2026-10-28T02:00:00+00:00",
                "ended_at": "2026-10-28T02:00:03+00:00",
                "status": "failed",
                "returncode": 2,
                "stdout": "",
                "stderr": "boom",
            },
        ],
    )

    history = client.get("/api/v1/schedules").json()["history"]
    assert [entry["name"] for entry in history] == ["iterate-quick-review", "legacy-job"]
    first, second = history
    assert first["duration_s"] == 12.5
    assert first["status"] == "success"
    assert first["returncode"] == 0
    assert len(first["stdout_tail"]) == 2000
    assert first["stdout_tail"] == long_stdout[-2000:]
    assert first["stderr_tail"] == "warning: something"
    assert second["duration_s"] == 3.0
    assert second["returncode"] == 2
    assert second["command"] == ""


def test_a_corrupt_registry_degrades_to_an_empty_list(
    client: TestClient, data_dir: Path
) -> None:
    (data_dir / "cron_jobs.json").write_text("{not json", encoding="utf-8")
    (data_dir / "cron_history.jsonl").write_text("not json either\n", encoding="utf-8")
    payload = client.get("/api/v1/schedules").json()
    assert payload["jobs"] == []
    assert payload["history"] == []


def test_history_limit_is_bounded_and_keeps_the_newest(
    client: TestClient, data_dir: Path
) -> None:
    _write_history(
        data_dir,
        [
            {
                "name": f"job-{index}",
                "started_at": f"2026-10-2{index}T00:00:00+00:00",
                "ended_at": f"2026-10-2{index}T00:00:01+00:00",
                "status": "success",
                "returncode": 0,
            }
            for index in range(1, 5)
        ],
    )
    history = client.get("/api/v1/schedules", params={"limit": 2}).json()["history"]
    assert [entry["name"] for entry in history] == ["job-3", "job-4"]
    assert client.get("/api/v1/schedules", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/schedules", params={"limit": 201}).status_code == 422
