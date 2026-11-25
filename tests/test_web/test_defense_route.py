"""Defensive-kernel web routes (design §17.3 P17).

Pins the console contract: the inventory resolves the same guard as the
kernel, the journal pages newest-first with exact filters and a 422 for an
unknown one, and every export carries the whole filtered window with the
server-owned filename. Root allow-listing is the same as every other route.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.defensive.journal import (
    KIND_INVARIANT,
    KIND_MANUAL_CHECK,
    KIND_ROLLBACK,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    record_defense_event,
)
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


def _write_config(project: Path, body: str) -> None:
    (project / "iterate.config.yaml").write_text(body, encoding="utf-8")


def test_page_returns_the_resolved_guard(client: TestClient, project: Path) -> None:
    (project / "README.md").write_text("ok", encoding="utf-8")
    _write_config(project, "invariants:\n  ensure:\n    - README.md\n")
    record_defense_event(
        project,
        kind=KIND_INVARIANT,
        outcome=OUTCOME_PASS,
        tool="write_file",
        checks_run=1,
    )

    response = client.get("/api/v1/defense", params={"project_root": str(project)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_root"] == str(project.resolve())
    assert payload["policy"]["source"] == "invariants"
    assert payload["policy"]["checks"] == 1
    assert payload["has_problems"] is False
    assert payload["summary"]["invariant_runs"] == 1
    assert payload["recent_events"][0]["kind"] == KIND_INVARIANT


def test_page_reports_dead_entries(client: TestClient, project: Path) -> None:
    _write_config(
        project,
        "invariants:\n"
        "  ensure:\n"
        '    - ""\n'
        "  commands:\n"
        "    core:\n"
        "      - pytest -q && rm -rf build\n",
    )
    payload = client.get(
        "/api/v1/defense", params={"project_root": str(project)}
    ).json()
    assert payload["has_problems"] is True
    assert {problem["kind"] for problem in payload["problems"]} == {"command", "ensure"}


def test_journal_pages_newest_first(client: TestClient, project: Path) -> None:
    for index in range(3):
        record_defense_event(
            project,
            kind=KIND_ROLLBACK if index == 2 else KIND_INVARIANT,
            outcome=OUTCOME_FAIL if index == 2 else OUTCOME_PASS,
            tool=f"tool-{index}",
        )

    payload = client.get(
        "/api/v1/defense/journal",
        params={"project_root": str(project), "limit": 2},
    ).json()

    assert payload["total"] == 3
    assert payload["returned"] == 2
    assert payload["truncated"] is True
    assert payload["entries"][0]["tool"] == "tool-2"
    assert payload["kinds"] == [KIND_INVARIANT, KIND_ROLLBACK]


def test_journal_filters(client: TestClient, project: Path) -> None:
    record_defense_event(project, kind=KIND_INVARIANT, outcome=OUTCOME_PASS, tool="write_file")
    record_defense_event(project, kind=KIND_ROLLBACK, outcome=OUTCOME_FAIL, tool="edit_file")

    by_kind = client.get(
        "/api/v1/defense/journal",
        params={"project_root": str(project), "kind": "rollback"},
    ).json()
    assert by_kind["total"] == 1
    assert by_kind["entries"][0]["tool"] == "edit_file"

    by_tool = client.get(
        "/api/v1/defense/journal",
        params={"project_root": str(project), "tool": "write_file"},
    ).json()
    assert by_tool["total"] == 1

    by_outcome = client.get(
        "/api/v1/defense/journal",
        params={"project_root": str(project), "outcome": "fail"},
    ).json()
    assert by_outcome["total"] == 1


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "bogus"},
        {"outcome": "bogus"},
    ],
)
def test_journal_rejects_unknown_filters(
    client: TestClient, project: Path, params: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/defense/journal",
        params={"project_root": str(project), **params},
    )
    assert response.status_code == 422


def test_journal_export_json(client: TestClient, project: Path) -> None:
    record_defense_event(
        project,
        kind=KIND_MANUAL_CHECK,
        outcome=OUTCOME_PASS,
        tool="ih defense check",
    )
    response = client.get(
        "/api/v1/defense/journal/export",
        params={"project_root": str(project), "format": "json"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "iterate-defense-" in response.headers["content-disposition"]
    assert response.headers["x-defense-total"] == "1"
    payload = json.loads(response.text)
    assert payload["total"] == 1
    assert payload["entries"][0]["kind"] == KIND_MANUAL_CHECK


def test_journal_export_markdown_and_csv(client: TestClient, project: Path) -> None:
    record_defense_event(
        project,
        kind=KIND_ROLLBACK,
        outcome=OUTCOME_FAIL,
        tool="write_file",
        path="a.py",
        reason="boom",
    )
    markdown = client.get(
        "/api/v1/defense/journal/export",
        params={"project_root": str(project), "format": "md"},
    )
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "# iterate-harness defensive kernel events" in markdown.text

    csv_response = client.get(
        "/api/v1/defense/journal/export",
        params={"project_root": str(project), "format": "csv"},
    )
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "timestamp,id,kind,outcome" in csv_response.text


def test_export_filters_are_exact(client: TestClient, project: Path) -> None:
    record_defense_event(project, kind=KIND_INVARIANT, outcome=OUTCOME_PASS)
    record_defense_event(project, kind=KIND_ROLLBACK, outcome=OUTCOME_FAIL)

    payload = json.loads(
        client.get(
            "/api/v1/defense/journal/export",
            params={"project_root": str(project), "format": "json", "kind": "rollback"},
        ).text
    )
    assert payload["total"] == 1
    assert payload["entries"][0]["kind"] == KIND_ROLLBACK


def test_missing_root_is_404(client: TestClient) -> None:
    response = client.get(
        "/api/v1/defense", params={"project_root": "/nonexistent/iterate-defense-probe"}
    )
    assert response.status_code == 404


def test_root_outside_allow_list_is_403(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(security, "_ALLOWED_ROOTS", {str(allowed.resolve())})
    response = client.get("/api/v1/defense", params={"project_root": str(outside)})
    assert response.status_code == 403
