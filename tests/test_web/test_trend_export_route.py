"""WebUI route tests for GET /api/v1/trend/export (design §17.3 P8).

The console could show the trend but not hand it to anyone: the export is the
new capability. Markdown is the paste-ready digest, CSV the spreadsheet form,
JSON the exact page payload. These tests pin the download contract — media
type, filename, format dispatch and the same root guards as the page.
"""

from __future__ import annotations

import csv
import io
import json
import re
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
    _add_report(tmp_path, "2026-01-02T00:00:00+00:00", [_finding("a.py")])
    return tmp_path


def test_markdown_export_is_a_download_named_after_the_report(
    client: TestClient, project: Path
) -> None:
    response = client.get(
        "/api/v1/trend/export", params={"project_root": str(project), "format": "md"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert re.fullmatch(
        r'attachment; filename="iterate-trend-\d{8}-\d{6}\.md"', disposition
    ), disposition
    assert response.text.startswith(f"# 迭代趋势 · {project}")
    assert "| 1 | 2026-01-01T00:00:00.000Z | R1 | approved | 2 | 2 | 0 | 0 |" in response.text


def test_format_defaults_to_markdown(client: TestClient, project: Path) -> None:
    response = client.get("/api/v1/trend/export", params={"project_root": str(project)})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")


def test_csv_export_has_the_stable_header_and_one_row_per_run(
    client: TestClient, project: Path
) -> None:
    response = client.get(
        "/api/v1/trend/export", params={"project_root": str(project), "format": "csv"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert re.fullmatch(
        r'attachment; filename="iterate-trend-\d{8}-\d{6}\.csv"',
        response.headers["content-disposition"],
    )
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][0] == "index" and rows[0][-1] == "unchanged"
    assert [row[0] for row in rows[1:]] == ["1", "2"]
    assert rows[2][7] == "0"  # run 2 added nothing new


def test_history_limit_trims_the_csv(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/trend/export",
        params={"project_root": str(project), "format": "csv", "history": 1},
    )
    rows = list(csv.reader(io.StringIO(response.text)))
    assert len(rows) == 2
    assert rows[1][0] == "2"


def test_json_export_matches_the_page_payload(client: TestClient, project: Path) -> None:
    exported = client.get(
        "/api/v1/trend/export", params={"project_root": str(project), "format": "json"}
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/json")
    assert exported.headers["content-disposition"].endswith('.json"')
    payload = json.loads(exported.text)
    page = client.get("/api/v1/trend", params={"project_root": str(project)}).json()
    assert payload["runs"] == page["runs"]
    assert payload["latest"] == page["latest"]
    assert payload["open_by_dimension"] == page["open_by_dimension"]


def test_unknown_format_is_rejected(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/trend/export", params={"project_root": str(project), "format": "pdf"}
    )
    assert response.status_code == 422


def test_export_respects_the_pinned_root_set(client: TestClient, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    security.set_allowed_roots([allowed])
    try:
        response = client.get(
            "/api/v1/trend/export", params={"project_root": str(outside), "format": "md"}
        )
    finally:
        security.set_allowed_roots(None)
    assert response.status_code == 403


def test_export_404s_for_a_missing_root(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/v1/trend/export",
        params={"project_root": str(tmp_path / "missing"), "format": "md"},
    )
    assert response.status_code == 404
