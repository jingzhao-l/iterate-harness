"""WebUI route tests for GET /api/v1/trend (design §17.3 P8)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.iterate.decision_log import DecisionLogEntry, append_entry
from iterate_harness.web import security
from iterate_harness.web.api import create_app


def _finding(file: str, severity: str = "high", line: int = 1) -> dict[str, object]:
    return {
        "file": file,
        "line": line,
        "dimension": "security",
        "severity": severity,
        "summary": f"problem in {file}",
    }


def _add_report(root: Path, timestamp: str, findings: list[dict[str, object]]) -> None:
    append_entry(
        root,
        DecisionLogEntry(
            timestamp=timestamp,
            round=1,
            type="report",
            data={
                "findings": findings,
                "totalFindings": len(findings),
                "verdict": "approved",
                "mode": "review",
            },
        ),
    )


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_empty_project_returns_no_data_view(client: TestClient, tmp_path: Path) -> None:
    response = client.get("/api/v1/trend", params={"project_root": str(tmp_path)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_data"] is False
    assert payload["total_runs"] == 0
    assert payload["runs"] == []
    assert payload["latest"] is None
    assert payload["stubborn_findings"] == []


def test_route_returns_run_history_and_latest_delta(client: TestClient, tmp_path: Path) -> None:
    _add_report(tmp_path, "2026-01-01T00:00:00+00:00", [_finding("a.py"), _finding("b.py")])
    _add_report(tmp_path, "2026-01-02T00:00:00+00:00", [_finding("a.py")])
    _add_report(tmp_path, "2026-01-03T00:00:00+00:00", [_finding("a.py"), _finding("b.py")])

    response = client.get("/api/v1/trend", params={"project_root": str(tmp_path)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_data"] is True
    assert payload["total_runs"] == 3
    assert [run["index"] for run in payload["runs"]] == [1, 2, 3]
    latest = payload["latest"]
    assert latest["regressed"] == 1
    assert latest["fixed"] == 0
    assert [item["file"] for item in latest["regressed_findings"]] == ["b.py"]
    assert payload["open_by_dimension"] == {}


def test_history_and_top_query_bounds(client: TestClient, tmp_path: Path) -> None:
    for index in range(4):
        _add_report(
            tmp_path,
            f"2026-01-0{index + 1}T00:00:00+00:00",
            [_finding(f"{index}.py")],
        )

    payload = client.get(
        "/api/v1/trend",
        params={"project_root": str(tmp_path), "history": 2, "top": 1},
    ).json()
    assert payload["total_runs"] == 4
    assert [run["index"] for run in payload["runs"]] == [3, 4]

    assert (
        client.get(
            "/api/v1/trend", params={"project_root": str(tmp_path), "top": 0}
        ).status_code
        == 422
    )


def test_route_rejects_a_root_outside_the_pinned_set(
    client: TestClient, tmp_path: Path
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    security.set_allowed_roots([allowed])
    try:
        response = client.get("/api/v1/trend", params={"project_root": str(outside)})
    finally:
        security.set_allowed_roots(None)
    assert response.status_code == 403
    assert "outside the roots" in response.json()["detail"]


def test_route_404s_for_missing_root(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/v1/trend", params={"project_root": str(tmp_path / "missing")}
    )
    assert response.status_code == 404
