"""Permission policy web routes (design §17.3 P16).

Pins the console contract: the merged policy is returned with the same root
guards as every other route, and the simulator maps a verdict to the same
values the CLI uses (allowed / confirm / denied) with a 422 for bad input.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.config.paths import get_config_file_path
from iterate_harness.config.settings import PathRuleConfig, PermissionSettings, Settings
from iterate_harness.web import security
from iterate_harness.web.api import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("ITERATE_DATA_DIR", str(data))
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path / "config"))
    return data


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    security.set_allowed_roots([root])
    yield root
    security.set_allowed_roots(None)


def _write_settings(settings: Settings) -> None:
    path = get_config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")


def test_policy_page_returns_the_merged_policy(client: TestClient, project: Path) -> None:
    response = client.get("/api/v1/permissions", params={"project_root": str(project)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_root"] == str(project.resolve())
    assert payload["mode"] in {"default", "plan", "full_auto"}
    assert payload["summary"]["sensitive_paths"] > 0
    assert payload["tools"], "the registered tools power the simulator datalist"
    assert {"pattern": "*/.env", "allow": False} in payload["path_rules"]


def test_policy_page_reports_dead_rules(client: TestClient, project: Path) -> None:
    _write_settings(
        Settings(permission=PermissionSettings(path_rules=[PathRuleConfig(pattern="", allow=False)]))
    )

    response = client.get("/api/v1/permissions", params={"project_root": str(project)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_problems"] is True
    assert payload["problems"][0]["kind"] == "path_rule"
    assert payload["problems"][0]["remedy"]


def test_check_sensitive_path_is_denied(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/permissions/check",
        params={
            "project_root": str(project),
            "tool": "read_file",
            "path": ".env",
            "mode": "full_auto",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["verdict"] == "denied"
    assert payload["mode"] == "full_auto"
    assert "sensitive credential path" in payload["reason"]
    assert payload["read_only"] is True
    assert payload["read_only_source"] == "inferred"


def test_check_default_mode_needs_confirmation(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/permissions/check",
        params={
            "project_root": str(project),
            "tool": "write_file",
            "path": "src/app.py",
            "mode": "default",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["verdict"] == "confirm"
    assert payload["requires_confirmation"] is True


def test_check_read_only_flag_is_honoured(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/permissions/check",
        params={
            "project_root": str(project),
            "tool": "write_file",
            "path": "src/app.py",
            "read_only": "true",
            "mode": "default",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only_source"] == "explicit"
    assert payload["verdict"] == "allowed"


def test_check_rejects_unknown_mode(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/permissions/check",
        params={"project_root": str(project), "tool": "read_file", "mode": "god"},
    )

    assert response.status_code == 422
    assert "Unknown mode" in response.json()["detail"]


def test_check_rejects_empty_tool(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/permissions/check",
        params={"project_root": str(project), "tool": "   "},
    )

    assert response.status_code == 422


def test_missing_root_is_404(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/permissions", params={"project_root": str(project / "missing")}
    )

    assert response.status_code == 404


def test_outside_root_is_403(client: TestClient, project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    response = client.get("/api/v1/permissions", params={"project_root": str(outside)})

    assert response.status_code == 403
