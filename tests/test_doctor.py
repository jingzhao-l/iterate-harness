"""Environment self-check tests (``ih doctor`` + ``GET /api/v1/doctor``).

The checks must be deterministic, network-free and never raise: doctor is the
one command that has to work when everything else is broken. These tests pin
the contract — statuses, ordering, JSON shape, and the route's response —
and mutate the environment to prove each status is reachable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.doctor import (
    FAIL,
    OK,
    WARN,
    DoctorReport,
    render_doctor,
    run_doctor,
)
from iterate_harness.web.api import create_app


def _check_ids(report: DoctorReport) -> list[str]:
    return [check.id for check in report.checks]


def test_report_shape_and_ordering(tmp_path: Path) -> None:
    """Failures and warnings sort before oks; every check is well-formed."""
    report = run_doctor(tmp_path)
    ids = _check_ids(report)
    # 11 checks, no duplicates, ordered failures→warnings→ok.
    assert len(ids) == len(set(ids)) == 11
    statuses = [check.status for check in report.checks]
    first_warn = statuses.index(WARN) if WARN in statuses else len(statuses)
    first_ok = statuses.index(OK) if OK in statuses else len(statuses)
    assert statuses[:first_warn] == [FAIL] * first_warn
    assert statuses[first_warn:first_ok] == [WARN] * (first_ok - first_warn)
    assert statuses[first_ok:] == [OK] * (len(statuses) - first_ok)
    # Every fail/warn carries an executable remedy; ok checks are pure.
    for check in report.checks:
        assert check.title
        if check.status in (FAIL, WARN):
            assert check.remedy, f"{check.id} must carry a remedy"
        assert check.id in {
            "python",
            "package",
            "config",
            "auth",
            "storage",
            "project",
            "iterate_config",
            "validation",
            "lease",
            "sandbox",
            "hook",
        }


def test_json_roundtrip(tmp_path: Path) -> None:
    """to_dict is JSON-serialisable and carries the summary counts."""
    report = run_doctor(tmp_path)
    payload = report.to_dict()
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert decoded["project_root"] == str(tmp_path.resolve())
    assert decoded["has_failures"] == report.has_failures
    assert len(decoded["checks"]) == 11
    assert decoded["summary"]["ok"] == sum(
        1 for c in report.checks if c.status == OK
    )


def test_render_mentions_summary_and_remedy(tmp_path: Path) -> None:
    """The terminal render shows statuses and surfaces a remedy line."""
    text = render_doctor(run_doctor(tmp_path))
    assert "summary:" in text
    assert "remedy:" in text
    assert "iterate-harness doctor" in text


def test_project_check_reports_missing_root() -> None:
    """A nonexistent project root is a fail, not an exception."""
    report = run_doctor(Path("/nonexistent/iterate-doctor-probe"))
    project = next(c for c in report.checks if c.id == "project")
    assert project.status == FAIL
    assert project.remedy


def test_auth_check_missing_credential_is_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no credential and no env var, the active profile is a hard fail."""

    # Force settings to load with no stored credential and clear env vars.
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    from iterate_harness.auth import manager as auth_manager
    from iterate_harness.auth import storage as auth_storage
    from iterate_harness.config import settings as settings_module

    def _no_credential(*_args: object, **_kwargs: object) -> str | None:
        return None

    # manager.py imports load_credential by name; patch both the module and the
    # manager namespace so no credential path can find a key.
    monkeypatch.setattr(auth_storage, "load_credential", _no_credential)
    monkeypatch.setattr(auth_manager, "load_credential", _no_credential)
    # A default Settings() has no credential anywhere: active_profile is the
    # built-in claude-api profile (anthropic_api_key), whose env vars and
    # stored credentials are all absent after the patches above.
    monkeypatch.setattr(settings_module, "load_settings", lambda *a, **k: settings_module.Settings())

    report = run_doctor(Path.cwd())
    auth = next(c for c in report.checks if c.id == "auth")
    assert auth.status == FAIL
    assert "auth login" in auth.remedy


def test_storage_check_unwritable_is_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unwritable data dir is a hard fail with a remedy."""
    import stat

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(stat.S_IRUSR | stat.S_IXUSR)  # read-only

    monkeypatch.setenv("ITERATE_DATA_DIR", str(blocked))
    try:
        report = run_doctor(Path.cwd())
    finally:
        blocked.chmod(stat.S_IRWXU)
    storage = next(c for c in report.checks if c.id == "storage")
    assert storage.status == FAIL
    assert "ITERATE_DATA_DIR" in storage.remedy


def test_sandbox_check_not_enabled_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sandbox disabled by config is an ok (not a fail)."""
    from iterate_harness.config import settings as settings_module

    settings = settings_module.Settings()
    settings.sandbox.enabled = False
    monkeypatch.setattr(settings_module, "load_settings", lambda *a, **k: settings)
    report = run_doctor(Path.cwd())
    sandbox = next(c for c in report.checks if c.id == "sandbox")
    assert sandbox.status == OK


def test_validation_check_without_config_warns(tmp_path: Path) -> None:
    """No iterate.config.yaml → advisory warn (built-in defaults)."""
    report = run_doctor(tmp_path)
    validation = next(c for c in report.checks if c.id == "validation")
    assert validation.status == WARN
    assert "iterate init" in validation.remedy


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_route_returns_report(client: TestClient, tmp_path: Path) -> None:
    response = client.get("/api/v1/doctor", params={"project_root": str(tmp_path)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["project_root"] == str(tmp_path.resolve())
    assert isinstance(payload["ok"], bool)
    assert len(payload["checks"]) == 11
    for check in payload["checks"]:
        assert set(check) >= {"id", "title", "status", "detail", "remedy"}


def test_route_404s_for_missing_root(client: TestClient) -> None:
    response = client.get(
        "/api/v1/doctor",
        params={"project_root": "/nonexistent/iterate-doctor-probe"},
    )
    assert response.status_code == 404


def test_route_respects_allowed_root_guard(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root outside the pinned allow-list is 403 (not a silent run)."""
    from iterate_harness.web import security

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(security, "_ALLOWED_ROOTS", {str(allowed.resolve())})
    response = client.get("/api/v1/doctor", params={"project_root": str(outside)})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# CLI (``ih doctor``)
# ---------------------------------------------------------------------------

def test_cli_doctor_json_roundtrip(tmp_path: Path) -> None:
    """``ih doctor --json`` emits a parseable report and exits 0 on ok/warn."""
    from typer.testing import CliRunner

    from iterate_harness.cli import app

    result = CliRunner().invoke(
        app, ["doctor", "--project-root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["checks"]) == 11
    assert payload["project_root"] == str(tmp_path.resolve())


def test_cli_doctor_strict_exits_nonzero_on_warn(tmp_path: Path) -> None:
    """A warn-only project (no iterate.config.yaml) fails under --strict."""
    from typer.testing import CliRunner

    from iterate_harness.cli import app

    result = CliRunner().invoke(
        app,
        ["doctor", "--project-root", str(tmp_path), "--strict"],
    )
    # A fresh tmp dir is not a git repo / has no iterate config: warnings only.
    assert result.exit_code == 1, result.output
    assert "warn" in result.output


def test_cli_doctor_fail_exits_nonzero(tmp_path: Path) -> None:
    """A hard failure exits 1 without --strict."""
    import stat

    from typer.testing import CliRunner

    from iterate_harness.cli import app

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    result = CliRunner().invoke(
        app,
        ["doctor", "--project-root", str(tmp_path), "--json"],
        env={"ITERATE_DATA_DIR": str(blocked)},
    )
    blocked.chmod(stat.S_IRWXU)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["has_failures"] is True
    storage = next(c for c in payload["checks"] if c["id"] == "storage")
    assert storage["status"] == "fail"
