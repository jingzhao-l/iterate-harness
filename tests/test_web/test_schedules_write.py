"""WebUI write routes for scheduled reviews (design §17.3 P10).

The console could already *read* the cron registry; these tests pin the half
that closes the loop — register/update the canonical quick review, pause or
resume a job, delete one — including the guard rails: secondary confirmation,
root allow-list, invalid cron/timezone/limits, and the 409 that prevents a
stray save from silently moving an existing plan to another directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.iterate import batch as iterate_batch
from iterate_harness.services.cron import load_cron_jobs
from iterate_harness.web.api import create_app


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the machine-level cron registry to a temp dir."""
    directory = tmp_path / "data"
    directory.mkdir()
    monkeypatch.setenv("ITERATE_DATA_DIR", str(directory))
    monkeypatch.setenv("ITERATE_LOGS_DIR", str(tmp_path / "logs"))
    return directory


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


@pytest.fixture()
def other_project(tmp_path: Path) -> Path:
    directory = tmp_path / "other"
    directory.mkdir()
    return directory


@pytest.fixture()
def client(project: Path) -> TestClient:
    return TestClient(create_app(project_root=project))


def _body(project: Path, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "project_root": str(project),
        "schedule": "0 9 * * 1-5",
        "ref": "HEAD",
        "rounds": 3,
        "mode": "dry-run",
        "timezone": "Asia/Shanghai",
        "timeout": 1800,
    }
    body.update(overrides)
    return body


def test_create_registers_the_canonical_job(
    client: TestClient, data_dir: Path, project: Path
) -> None:
    response = client.post("/api/v1/schedules", params={"confirm": "true"}, json=_body(project))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["target"] == iterate_batch.ITERATE_CRON_JOB_NAME
    job = payload["detail"]["job"]
    assert job["cwd"] == str(project.resolve())
    assert job["schedule"] == "0 9 * * 1-5"
    assert job["timezone"] == "Asia/Shanghai"
    assert job["schedule_valid"] is True
    assert "--changed" in job["command"] and "--rounds 3" in job["command"]
    assert "cron start" in payload["message"]  # daemon is not running in tests

    stored = load_cron_jobs()
    assert len(stored) == 1
    assert stored[0]["timeout"] == 1800
    # The write is audited on the project it targets.
    audit = project / ".iterate" / "web-audit.jsonl"
    assert audit.is_file()
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["action"] == "schedule.upsert"


def test_save_requires_secondary_confirmation(client: TestClient, data_dir: Path, project: Path) -> None:
    response = client.post("/api/v1/schedules", json=_body(project))
    assert response.status_code == 422
    assert "confirm=true" in response.json()["detail"]
    assert load_cron_jobs() == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schedule", "not a cron"),
        ("timezone", "Not/AZone"),
        ("ref", "--upload-pack=evil"),
    ],
)
def test_invalid_values_are_caller_errors(
    client: TestClient, data_dir: Path, project: Path, field: str, value: str
) -> None:
    response = client.post(
        "/api/v1/schedules",
        params={"confirm": "true"},
        json=_body(project, **{field: value}),
    )
    assert response.status_code == 422, response.text
    assert load_cron_jobs() == []


@pytest.mark.parametrize(("field", "value"), [("rounds", 0), ("rounds", 21), ("timeout", 59), ("timeout", 7201)])
def test_out_of_range_limits_are_rejected_by_the_request_model(
    client: TestClient, data_dir: Path, project: Path, field: str, value: int
) -> None:
    response = client.post(
        "/api/v1/schedules",
        params={"confirm": "true"},
        json=_body(project, **{field: value}),
    )
    assert response.status_code == 422
    assert load_cron_jobs() == []


def test_moving_an_existing_plan_requires_replace(
    tmp_path: Path, data_dir: Path, project: Path, other_project: Path
) -> None:
    # An unpinned app may address both roots (the sidebar's multi-project mode).
    client = TestClient(create_app())
    first = client.post("/api/v1/schedules", params={"confirm": "true"}, json=_body(project))
    assert first.status_code == 200, first.text

    conflict = client.post(
        "/api/v1/schedules", params={"confirm": "true"}, json=_body(other_project)
    )
    assert conflict.status_code == 409
    assert str(project.resolve()) in conflict.json()["detail"]
    assert "replace=true" in conflict.json()["detail"]
    assert load_cron_jobs()[0]["cwd"] == str(project.resolve())

    replaced = client.post(
        "/api/v1/schedules",
        params={"confirm": "true"},
        json=_body(other_project, replace=True),
    )
    assert replaced.status_code == 200, replaced.text
    jobs = load_cron_jobs()
    assert len(jobs) == 1
    assert jobs[0]["cwd"] == str(other_project.resolve())


def test_registering_again_for_the_same_root_is_an_update(
    client: TestClient, data_dir: Path, project: Path
) -> None:
    assert client.post("/api/v1/schedules", params={"confirm": "true"}, json=_body(project)).status_code == 200
    updated = client.post(
        "/api/v1/schedules",
        params={"confirm": "true"},
        json=_body(project, schedule="30 7 * * *", rounds=5, mode="normal"),
    )
    assert updated.status_code == 200, updated.text
    jobs = load_cron_jobs()
    assert len(jobs) == 1
    assert jobs[0]["schedule"] == "30 7 * * *"
    assert "ih iterate run --changed" in jobs[0]["command"]
    assert "--rounds 5" in jobs[0]["command"]


def test_roots_outside_the_pinned_set_are_refused(
    tmp_path: Path, data_dir: Path, project: Path, other_project: Path
) -> None:
    client = TestClient(create_app(project_root=project))
    response = client.post(
        "/api/v1/schedules", params={"confirm": "true"}, json=_body(other_project)
    )
    assert response.status_code == 403
    assert load_cron_jobs() == []


def test_toggle_pauses_and_resumes_a_job(
    client: TestClient, data_dir: Path, project: Path
) -> None:
    assert client.post("/api/v1/schedules", params={"confirm": "true"}, json=_body(project)).status_code == 200
    name = iterate_batch.ITERATE_CRON_JOB_NAME

    paused = client.post(
        "/api/v1/schedules/enabled",
        params={"name": name, "enabled": "false", "confirm": "true", "project_root": str(project)},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["detail"]["job"]["enabled"] is False
    assert load_cron_jobs()[0]["enabled"] is False

    resumed = client.post(
        "/api/v1/schedules/enabled",
        params={"name": name, "enabled": "true", "confirm": "true", "project_root": str(project)},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["detail"]["job"]["enabled"] is True
    assert load_cron_jobs()[0]["enabled"] is True


def test_toggle_needs_confirm_and_a_real_job(
    client: TestClient, data_dir: Path, project: Path
) -> None:
    no_confirm = client.post(
        "/api/v1/schedules/enabled", params={"name": "nope", "enabled": "false"}
    )
    assert no_confirm.status_code == 422
    missing = client.post(
        "/api/v1/schedules/enabled",
        params={"name": "nope", "enabled": "false", "confirm": "true"},
    )
    assert missing.status_code == 404


def test_delete_removes_only_the_named_job(
    client: TestClient, data_dir: Path, project: Path
) -> None:
    assert client.post("/api/v1/schedules", params={"confirm": "true"}, json=_body(project)).status_code == 200
    name = iterate_batch.ITERATE_CRON_JOB_NAME

    removed = client.delete(
        "/api/v1/schedules",
        params={"name": name, "confirm": "true", "project_root": str(project)},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["target"] == name
    assert load_cron_jobs() == []

    again = client.delete("/api/v1/schedules", params={"name": name, "confirm": "true"})
    assert again.status_code == 404


def test_delete_requires_confirm(client: TestClient, data_dir: Path, project: Path) -> None:
    assert client.post("/api/v1/schedules", params={"confirm": "true"}, json=_body(project)).status_code == 200
    response = client.delete(
        "/api/v1/schedules", params={"name": iterate_batch.ITERATE_CRON_JOB_NAME}
    )
    assert response.status_code == 422
    assert len(load_cron_jobs()) == 1


def test_registry_created_by_the_console_is_readable_by_the_page(
    client: TestClient, data_dir: Path, project: Path
) -> None:
    assert client.post("/api/v1/schedules", params={"confirm": "true"}, json=_body(project)).status_code == 200
    view = client.get("/api/v1/schedules").json()
    assert view["scheduler"]["total_jobs"] == 1
    assert view["jobs"][0]["name"] == iterate_batch.ITERATE_CRON_JOB_NAME
    assert view["jobs"][0]["cwd"] == str(project.resolve())
