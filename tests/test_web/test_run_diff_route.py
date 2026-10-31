"""WebUI route tests for GET /api/v1/runs/diff (design §17.3 P2).

The console's comparison view needs one call: every finished run for its
pickers plus the classified finding sets for the selected pair. Out-of-range
indices must name the valid range, and an empty project must render an
explained empty state rather than a 500.
"""

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


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    _add_report(tmp_path, "2026-01-01T00:00:00+00:00", [_finding("a.py"), _finding("b.py")])
    _add_report(tmp_path, "2026-01-02T00:00:00+00:00", [_finding("a.py"), _finding("c.py")])
    return tmp_path


def test_default_compares_the_last_two_runs(client: TestClient, project: Path) -> None:
    payload = client.get(
        "/api/v1/runs/diff", params={"project_root": str(project)}
    ).json()

    assert payload["from_run"]["index"] == 1
    assert payload["to_run"]["index"] == 2
    assert payload["counts"] == {"new": 1, "fixed": 1, "regressed": 0, "unchanged": 1}
    assert [item["file"] for item in payload["new"]] == ["c.py"]
    assert [item["file"] for item in payload["fixed"]] == ["b.py"]
    assert [run["index"] for run in payload["runs"]] == [1, 2]
    assert payload["runs"][0]["total"] == 2


def test_explicit_from_and_to_are_honoured(client: TestClient, project: Path) -> None:
    payload = client.get(
        "/api/v1/runs/diff",
        params={"project_root": str(project), "from": 1, "to": 2},
    ).json()
    assert payload["from_run"]["index"] == 1
    assert payload["to_run"]["index"] == 2


def test_out_of_range_index_is_a_400_naming_the_range(
    client: TestClient, project: Path
) -> None:
    response = client.get(
        "/api/v1/runs/diff", params={"project_root": str(project), "from": 9}
    )
    assert response.status_code == 400
    assert "between 1 and 2" in response.json()["detail"]


def test_an_empty_project_returns_null_pair_and_no_findings(
    client: TestClient, tmp_path: Path
) -> None:
    payload = client.get(
        "/api/v1/runs/diff", params={"project_root": str(tmp_path)}
    ).json()
    assert payload["runs"] == []
    assert payload["from_run"] is None
    assert payload["to_run"] is None
    assert payload["counts"] == {"new": 0, "fixed": 0, "regressed": 0, "unchanged": 0}
    assert payload["new"] == []


def test_one_run_is_still_listed_but_not_compared(client: TestClient, tmp_path: Path) -> None:
    _add_report(tmp_path, "2026-01-01T00:00:00+00:00", [_finding("a.py")])
    payload = client.get(
        "/api/v1/runs/diff", params={"project_root": str(tmp_path)}
    ).json()
    assert len(payload["runs"]) == 1
    assert payload["from_run"] is None


def test_missing_root_is_a_404(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/v1/runs/diff", params={"project_root": str(tmp_path / "missing")}
    )
    assert response.status_code == 404


def test_root_outside_the_pinned_set_is_a_403(client: TestClient, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    security.set_allowed_roots([allowed])
    try:
        response = client.get(
            "/api/v1/runs/diff", params={"project_root": str(outside)}
        )
    finally:
        security.set_allowed_roots(None)
    assert response.status_code == 403


def test_a_reversed_pair_is_a_400(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/runs/diff",
        params={"project_root": str(project), "from": 2, "to": 1},
    )
    assert response.status_code == 400
    assert "earlier than" in response.json()["detail"]
