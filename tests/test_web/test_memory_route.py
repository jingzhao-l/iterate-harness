"""Project-memory routes (design §17.3 P14) + injection preview.

The engine injects project memory into every runtime prompt; these tests pin
the console's branch: read-only inventory (root guards, query ranking, prompt
preview), the detail read, and the two audited write paths (create with
overwrite-refusal, delete) — including the 422-confirm contract every mutating
web route honours.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.memory import add_memory_entry
from iterate_harness.web import security
from iterate_harness.web.api import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("ITERATE_DATA_DIR", str(data))
    return data


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    add_memory_entry(root, "pytest quirks", "Pytest markers and fixtures")
    add_memory_entry(root, "docker notes", "Docker compose caveats")
    return root


def test_page_lists_entries(client: TestClient, project: Path) -> None:
    response = client.get("/api/v1/memory", params={"project_root": str(project)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["entries"] == 2
    assert {entry["name"] for entry in payload["entries"]} == {
        "pytest_quirks.md",
        "docker_notes.md",
    }


def test_query_ranks_matches(client: TestClient, project: Path) -> None:
    payload = client.get(
        "/api/v1/memory", params={"project_root": str(project), "query": "fix pytest"}
    ).json()
    assert [entry["name"] for entry in payload["matches"]] == ["pytest_quirks.md"]


def test_prompt_returns_injection_preview(client: TestClient, project: Path) -> None:
    payload = client.get(
        "/api/v1/memory",
        params={"project_root": str(project), "prompt": "fix pytest fixtures"},
    ).json()
    injection = payload["injection"]
    assert injection is not None
    assert injection["enabled"] is True
    assert "pytest_quirks.md" in injection["rendered"]
    assert injection["chars"] > 0


def test_entry_detail_and_traversal(client: TestClient, project: Path) -> None:
    ok = client.get(
        "/api/v1/memory/entry",
        params={"project_root": str(project), "name": "pytest_quirks.md"},
    )
    assert ok.status_code == 200
    assert ok.json()["content"] == "Pytest markers and fixtures\n"

    missing = client.get(
        "/api/v1/memory/entry",
        params={"project_root": str(project), "name": "nope"},
    )
    assert missing.status_code == 404

    traversal = client.get(
        "/api/v1/memory/entry",
        params={"project_root": str(project), "name": "../secret.md"},
    )
    assert traversal.status_code == 404


def test_create_requires_confirm_and_refuses_overwrite(client: TestClient, project: Path) -> None:
    no_confirm = client.post(
        "/api/v1/memory",
        params={"project_root": str(project)},
        json={"title": "new", "content": "hello"},
    )
    assert no_confirm.status_code == 422

    ok = client.post(
        "/api/v1/memory",
        params={"project_root": str(project), "confirm": "true"},
        json={"title": "canary", "content": "use canary"},
    )
    assert ok.status_code == 200
    assert ok.json()["target"] == "canary.md"

    conflict = client.post(
        "/api/v1/memory",
        params={"project_root": str(project), "confirm": "true"},
        json={"title": "canary", "content": "overwrite me"},
    )
    assert conflict.status_code == 409

    overwrite = client.post(
        "/api/v1/memory",
        params={"project_root": str(project), "confirm": "true"},
        json={"title": "canary", "content": "overwrite me", "overwrite": True},
    )
    assert overwrite.status_code == 200


def test_delete_requires_confirm_and_is_audited(client: TestClient, project: Path) -> None:
    no_confirm = client.delete(
        "/api/v1/memory",
        params={"project_root": str(project), "name": "pytest_quirks.md"},
    )
    assert no_confirm.status_code == 422

    ok = client.delete(
        "/api/v1/memory",
        params={"project_root": str(project), "name": "pytest_quirks.md", "confirm": "true"},
    )
    assert ok.status_code == 200
    remaining = client.get("/api/v1/memory", params={"project_root": str(project)}).json()
    assert "pytest_quirks.md" not in {entry["name"] for entry in remaining["entries"]}

    audit = client.get("/api/v1/audit", params={"project_root": str(project)}).json()
    assert any(entry["action"] == "memory.remove" for entry in audit["entries"])


def test_missing_root_is_404_and_outside_is_403(client: TestClient, tmp_path: Path) -> None:
    response = client.get("/api/v1/memory", params={"project_root": str(tmp_path / "missing")})
    assert response.status_code == 404

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    security.set_allowed_roots([allowed])
    try:
        response = client.get("/api/v1/memory", params={"project_root": str(outside)})
    finally:
        security.set_allowed_roots(None)
    assert response.status_code == 403


def test_cli_and_api_share_the_inventory(client: TestClient, project: Path) -> None:
    from typer.testing import CliRunner

    from iterate_harness.cli import app as cli_app

    api_payload = client.get("/api/v1/memory", params={"project_root": str(project)}).json()
    result = CliRunner().invoke(
        cli_app, ["memory", "list", "--project-root", str(project), "--json"]
    )
    assert result.exit_code == 0
    cli_payload = json.loads(result.output)
    assert {e["name"] for e in cli_payload["entries"]} == {
        e["name"] for e in api_payload["entries"]
    }
