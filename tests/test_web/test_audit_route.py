"""Audit-trail route + CLI tests (design §17.3 P13).

The journal was write-only from an operator's point of view (the dashboard
showed ten lines). These tests pin the new read side: paging, filters, three
export shapes, the action list, and the same root guards every other route
uses. Corrupt lines must degrade to a skipped row, never a 500.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from iterate_harness.audit import AUDIT_FILE
from iterate_harness.cli import app as cli_app
from iterate_harness.web import security
from iterate_harness.web.api import create_app

#: Distinct record timestamps (ascending) used by the fixtures below.
TS = [
    "2026-11-01T08:00:00+00:00",
    "2026-11-02T09:00:00+00:00",
    "2026-11-03T10:00:00+00:00",
]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _record(root: Path, action: str, target: str, summary: dict, ts: str) -> None:
    """Append one record directly, so the fixture controls the timestamps."""
    journal = root / ".iterate" / AUDIT_FILE
    journal.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": ts, "action": action, "target": target, "summary": summary}
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    _record(tmp_path, "config.save", "iterate.config.yaml", {"ok": True}, TS[0])
    _record(tmp_path, "checkpoint.clear", "checkpoint.json", {"had": True}, TS[1])
    _record(tmp_path, "schedule.upsert", "iterate.review-changed", {"rounds": 3}, TS[2])
    return tmp_path


def test_page_is_newest_first_with_total_and_actions(client: TestClient, project: Path) -> None:
    response = client.get("/api/v1/audit", params={"project_root": str(project)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["returned"] == 3
    assert payload["truncated"] is False
    assert payload["actions"] == ["checkpoint", "config", "schedule"]
    # Newest first.
    assert [entry["action"] for entry in payload["entries"]] == [
        "schedule.upsert",
        "checkpoint.clear",
        "config.save",
    ]
    assert payload["entries"][0]["summary"] == {"rounds": 3}


def test_action_prefix_filter(client: TestClient, project: Path) -> None:
    payload = client.get(
        "/api/v1/audit", params={"project_root": str(project), "action": "config"}
    ).json()
    assert payload["total"] == 1
    assert payload["entries"][0]["action"] == "config.save"


def test_target_substring_filter(client: TestClient, project: Path) -> None:
    payload = client.get(
        "/api/v1/audit", params={"project_root": str(project), "target": "checkpoint"}
    ).json()
    assert payload["total"] == 1
    assert payload["entries"][0]["action"] == "checkpoint.clear"


def test_since_filter(client: TestClient, project: Path) -> None:
    payload = client.get(
        "/api/v1/audit",
        params={"project_root": str(project), "since": "2026-11-02T12:00:00+00:00"},
    ).json()
    assert payload["total"] == 1
    assert payload["entries"][0]["action"] == "schedule.upsert"


def test_pagination_offsets_from_the_newest(client: TestClient, project: Path) -> None:
    page1 = client.get(
        "/api/v1/audit", params={"project_root": str(project), "limit": 2}
    ).json()
    assert [entry["action"] for entry in page1["entries"]] == [
        "schedule.upsert",
        "checkpoint.clear",
    ]
    assert page1["truncated"] is True
    page2 = client.get(
        "/api/v1/audit", params={"project_root": str(project), "limit": 2, "offset": 2}
    ).json()
    assert [entry["action"] for entry in page2["entries"]] == ["config.save"]
    assert page2["truncated"] is False


def test_bad_since_is_422(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/audit", params={"project_root": str(project), "since": "yesterday"}
    )
    assert response.status_code == 422


def test_missing_root_is_404(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/v1/audit", params={"project_root": str(tmp_path / "missing")}
    )
    assert response.status_code == 404


def test_outside_pinned_root_is_403(client: TestClient, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    security.set_allowed_roots([allowed])
    try:
        response = client.get(
            "/api/v1/audit", params={"project_root": str(outside)}
        )
    finally:
        security.set_allowed_roots(None)
    assert response.status_code == 403


def test_corrupt_lines_are_skipped_not_a_500(client: TestClient, tmp_path: Path) -> None:
    journal = tmp_path / ".iterate" / AUDIT_FILE
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("not-json\n\n{\"timestamp\":\"2026-01-01T00:00:00+00:00\",\"action\":\"a\",\"target\":\"t\"}\n", encoding="utf-8")
    response = client.get("/api/v1/audit", params={"project_root": str(tmp_path)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["entries"][0]["action"] == "a"


def test_markdown_export_is_a_download(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/audit/export", params={"project_root": str(project), "format": "md"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["x-audit-total"] == "3"
    assert re.fullmatch(
        r'attachment; filename="iterate-audit-\d{8}-\d{6}\.md"',
        response.headers["content-disposition"],
    )
    assert "| 2026-11-03T10:00:00+00:00 | schedule.upsert |" in response.text


def test_csv_export_has_stable_header_and_rows(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/audit/export", params={"project_root": str(project), "format": "csv"}
    )
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["timestamp", "action", "target", "summary"]
    assert [row[1] for row in rows[1:]] == [
        "schedule.upsert",
        "checkpoint.clear",
        "config.save",
    ]


def test_json_export_matches_the_page_payload(client: TestClient, project: Path) -> None:
    exported = client.get(
        "/api/v1/audit/export", params={"project_root": str(project), "format": "json"}
    )
    payload = json.loads(exported.text)
    page = client.get("/api/v1/audit", params={"project_root": str(project)}).json()
    assert payload["entries"] == page["entries"]
    assert payload["actions"] == page["actions"]
    assert payload["total"] == page["total"]


def test_unknown_format_is_rejected(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/audit/export", params={"project_root": str(project), "format": "pdf"}
    )
    assert response.status_code == 422


def test_export_respects_filters(client: TestClient, project: Path) -> None:
    response = client.get(
        "/api/v1/audit/export",
        params={"project_root": str(project), "format": "csv", "action": "schedule"},
    )
    assert response.headers["x-audit-total"] == "1"


def test_cli_audit_json_round_trip(project: Path) -> None:
    result = CliRunner().invoke(cli_app, ["audit", "--project-root", str(project), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["total"] == 3
    assert [entry["action"] for entry in payload["entries"]] == [
        "schedule.upsert",
        "checkpoint.clear",
        "config.save",
    ]


def test_cli_audit_table_and_filters(project: Path) -> None:
    result = CliRunner().invoke(
        cli_app,
        ["audit", "--project-root", str(project), "--action", "config", "--limit", "5"],
    )
    assert result.exit_code == 0
    assert "config.save" in result.output
    assert "checkpoint.clear" not in result.output


def test_cli_audit_csv_to_file(project: Path, tmp_path: Path) -> None:
    out = tmp_path / "audit.csv"
    result = CliRunner().invoke(
        cli_app,
        ["audit", "--project-root", str(project), "--format", "csv", "--out", str(out)],
    )
    assert result.exit_code == 0
    rows = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8"))))
    assert rows[0] == ["timestamp", "action", "target", "summary"]
    assert len(rows) == 4


def test_cli_audit_bad_since_is_an_error(project: Path) -> None:
    result = CliRunner().invoke(
        cli_app,
        ["audit", "--project-root", str(project), "--since", "yesterday"],
    )
    assert result.exit_code == 1
    assert "invalid --since" in result.output


def test_cli_audit_bad_format_is_an_error(project: Path) -> None:
    result = CliRunner().invoke(
        cli_app,
        ["audit", "--project-root", str(project), "--format", "xml"],
    )
    assert result.exit_code == 1
    assert "unknown --format" in result.output


def test_cli_audit_absent_journal_is_empty(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli_app, ["audit", "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["total"] == 0
