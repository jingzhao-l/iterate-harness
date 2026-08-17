"""Integration tests for the WebUI API routes (design §17.3 P1–P7).

Uses FastAPI's TestClient against ``create_app()`` with an explicit
``project_root`` per request pointing at a ``tmp_path`` project so the tests
never touch the real working directory or user config.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.iterate.checkpoint import save_checkpoint
from iterate_harness.iterate.decision_log import append_entry
from iterate_harness.iterate.types import DecisionLogEntry
from iterate_harness.web import api as api_module
from iterate_harness.web import events as events_module
from iterate_harness.web.api import create_app

#: A config dict that passes ``validate_config``.
VALID_CONFIG: dict[str, object] = {
    "goal": "improve the codebase",
    "dimensions": ["code_review", "security"],
    "validation": {"command_whitelist": ["python"], "commands": {"python": ["python -m pytest"]}},
    "max_rounds": 5,
}


@pytest.fixture()
def client():
    return TestClient(create_app())


def build_entry(*, timestamp: str, round: int, type: str, data: dict[str, object]):
    return DecisionLogEntry(timestamp=timestamp, round=round, type=type, data=data)


def populate_log(tmp_path: Path) -> None:
    """Create a small decision log: 2 rounds, findings, fixes, and a report."""
    append_entry(
        tmp_path,
        build_entry(
            timestamp="2026-08-17T00:00:00+00:00",
            round=1,
            type="round_start",
            data={"phase": "start"},
        ),
    )
    append_entry(
        tmp_path,
        build_entry(
            timestamp="2026-08-17T00:01:00+00:00",
            round=1,
            type="review_result",
            data={
                "findings": [
                    {"file": "a.py", "line": 3, "dimension": "security", "severity": "high", "summary": "sql injection"},
                    {"file": "a.py", "line": 5, "dimension": "code_review", "severity": "low", "summary": "nit"},
                ]
            },
        ),
    )
    append_entry(
        tmp_path,
        build_entry(
            timestamp="2026-08-17T00:02:00+00:00",
            round=1,
            type="atomic_fix",
            data={"file": "a.py", "dimension": "security", "summary": "parameterized query"},
        ),
    )
    append_entry(
        tmp_path,
        build_entry(
            timestamp="2026-08-17T00:03:00+00:00",
            round=2,
            type="round_start",
            data={"phase": "round-2"},
        ),
    )
    append_entry(
        tmp_path,
        build_entry(
            timestamp="2026-08-17T00:04:00+00:00",
            round=2,
            type="review_result",
            data={
                # same finding re-reported in a later round must dedupe
                "findings": [
                    {"file": "a.py", "line": 3, "dimension": "security", "severity": "high", "summary": "sql injection"},
                    {"file": "b.py", "line": 1, "dimension": "code_review", "severity": "medium", "summary": "style"},
                ]
            },
        ),
    )
    append_entry(
        tmp_path,
        build_entry(
            timestamp="2026-08-17T00:05:00+00:00",
            round=2,
            type="report",
            data={
                "verdict": "pass",
                "mode": "dry-run",
                "totalFindings": 3,
                "findingsByRound": [2, 2],
                # latest_report_entry() requires a "findings" key — matches the
                # real report entry shape produced by review.py report_to_dict().
                "findings": [
                    {"file": "a.py", "line": 3, "dimension": "security", "severity": "high", "summary": "sql injection"},
                    {"file": "a.py", "line": 5, "dimension": "code_review", "severity": "low", "summary": "nit"},
                    {"file": "b.py", "line": 1, "dimension": "code_review", "severity": "medium", "summary": "style"},
                ],
            },
        ),
    )


class TestHealthAndStatus:
    def test_health(self, client: TestClient, tmp_path: Path):
        response = client.get("/api/v1/health", params={"project_root": str(tmp_path)})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_health_missing_root_still_ok(self, client: TestClient, tmp_path: Path):
        response = client.get("/api/v1/health", params={"project_root": str(tmp_path / "nope")})
        assert response.status_code == 200

    def test_status_empty_project(self, client: TestClient, tmp_path: Path):
        response = client.get("/api/v1/status", params={"project_root": str(tmp_path)})
        assert response.status_code == 200
        body = response.json()
        assert body["entry_count"] == 0
        assert body["latest_round"] == 0
        assert body["convergence"] == []
        assert body["project_root"] == str(tmp_path.resolve())

    def test_status_aggregates(self, client: TestClient, tmp_path: Path):
        populate_log(tmp_path)
        response = client.get("/api/v1/status", params={"project_root": str(tmp_path)})
        assert response.status_code == 200
        body = response.json()
        assert body["entry_count"] == 6
        assert body["latest_round"] == 2
        assert body["convergence"] == [2, 2]
        assert body["last_run"]["verdict"] == "pass"
        assert body["last_run"]["mode"] == "dry-run"

    def test_status_missing_root_404(self, client: TestClient, tmp_path: Path):
        response = client.get("/api/v1/status", params={"project_root": str(tmp_path / "missing")})
        assert response.status_code == 404


class TestRuns:
    def test_list_runs_paginates(self, client: TestClient, tmp_path: Path):
        populate_log(tmp_path)
        page = client.get("/api/v1/runs", params={"project_root": str(tmp_path), "limit": 2}).json()
        assert len(page) == 2
        assert page[0]["round"] == 1
        assert page[0]["type"] == "round_start"

    def test_list_runs_respects_offset(self, client: TestClient, tmp_path: Path):
        populate_log(tmp_path)
        page = client.get(
            "/api/v1/runs", params={"project_root": str(tmp_path), "offset": 2, "limit": 2}
        ).json()
        assert len(page) == 2
        assert page[0]["type"] == "atomic_fix"

    def test_timeline_filters_by_round(self, client: TestClient, tmp_path: Path):
        populate_log(tmp_path)
        entries = client.get(
            "/api/v1/runs/timeline", params={"project_root": str(tmp_path), "round": 2}
        ).json()
        assert all(e["round"] == 2 for e in entries)
        assert len(entries) == 3

    def test_timeline_filters_by_type(self, client: TestClient, tmp_path: Path):
        populate_log(tmp_path)
        entries = client.get(
            "/api/v1/runs/timeline", params={"project_root": str(tmp_path), "type": "atomic_fix"}
        ).json()
        assert [e["type"] for e in entries] == ["atomic_fix"]

    def test_timeline_rejects_unknown_type(self, client: TestClient, tmp_path: Path):
        response = client.get(
            "/api/v1/runs/timeline", params={"project_root": str(tmp_path), "type": "bogus"}
        )
        assert response.status_code == 422

    def test_findings_dedupes_and_filters(self, client: TestClient, tmp_path: Path):
        populate_log(tmp_path)
        body = client.get("/api/v1/runs/findings", params={"project_root": str(tmp_path)}).json()
        # a.py sql-injection deduped across rounds → 3 unique findings
        assert body["total"] == 3
        assert body["page"] == 3

        high = client.get(
            "/api/v1/runs/findings",
            params={"project_root": str(tmp_path), "severity": "high"},
        ).json()
        assert high["total"] == 1

        dim = client.get(
            "/api/v1/runs/findings",
            params={"project_root": str(tmp_path), "dimension": "code_review"},
        ).json()
        assert dim["total"] == 2

    def test_latest_report(self, client: TestClient, tmp_path: Path):
        populate_log(tmp_path)
        body = client.get("/api/v1/runs/report", params={"project_root": str(tmp_path)}).json()
        assert body["verdict"] == "pass"
        assert body["round"] == 2

    def test_latest_report_missing_404(self, client: TestClient, tmp_path: Path):
        response = client.get("/api/v1/runs/report", params={"project_root": str(tmp_path)})
        assert response.status_code == 404


class TestCheckpoints:
    def test_get_checkpoint_missing(self, client: TestClient, tmp_path: Path):
        body = client.get("/api/v1/checkpoints", params={"project_root": str(tmp_path)}).json()
        assert body["exists"] is False
        assert body["checkpoint"] is None

    def test_get_checkpoint_present(self, client: TestClient, tmp_path: Path):
        save_checkpoint(
            tmp_path, round=3, new_findings=1, total_findings=4, per_dimension={},
            converged=False, input_tokens=500, output_tokens=800, cost_usd=0.1, mode="dry-run",
        )
        body = client.get("/api/v1/checkpoints", params={"project_root": str(tmp_path)}).json()
        assert body["exists"] is True
        assert body["checkpoint"]["round"] == 3

    def test_restore_requires_confirm(self, client: TestClient, tmp_path: Path):
        save_checkpoint(
            tmp_path, round=3, new_findings=1, total_findings=4, per_dimension={},
            converged=False, input_tokens=500, output_tokens=800, cost_usd=0.1, mode="dry-run",
        )
        response = client.post(
            "/api/v1/checkpoints/restore", params={"project_root": str(tmp_path)}
        )
        assert response.status_code == 422

    def test_restore_ok_and_audited(self, client: TestClient, tmp_path: Path):
        save_checkpoint(
            tmp_path, round=3, new_findings=1, total_findings=4, per_dimension={},
            converged=False, input_tokens=500, output_tokens=800, cost_usd=0.1, mode="dry-run",
        )
        body = client.post(
            "/api/v1/checkpoints/restore",
            params={"project_root": str(tmp_path), "confirm": "true"},
        ).json()
        assert body["status"] == "ok"
        audit = (tmp_path / ".iterate" / "web-audit.jsonl").read_text(encoding="utf-8")
        assert "checkpoint.restore" in audit

    def test_restore_no_checkpoint_404(self, client: TestClient, tmp_path: Path):
        response = client.post(
            "/api/v1/checkpoints/restore",
            params={"project_root": str(tmp_path), "confirm": "true"},
        )
        assert response.status_code == 404

    def test_clear_requires_confirm(self, client: TestClient, tmp_path: Path):
        response = client.post(
            "/api/v1/checkpoints/clear", params={"project_root": str(tmp_path)}
        )
        assert response.status_code == 422

    def test_clear_ok(self, client: TestClient, tmp_path: Path):
        save_checkpoint(
            tmp_path, round=1, new_findings=1, total_findings=2, per_dimension={},
            converged=False, input_tokens=0, output_tokens=0, cost_usd=0.0, mode="dry-run",
        )
        body = client.post(
            "/api/v1/checkpoints/clear",
            params={"project_root": str(tmp_path), "confirm": "true"},
        ).json()
        assert body["status"] == "ok"
        assert (tmp_path / ".iterate" / "checkpoint.json").exists() is False


class TestConfig:
    def test_get_config_empty(self, client: TestClient, tmp_path: Path):
        body = client.get("/api/v1/config", params={"project_root": str(tmp_path)}).json()
        assert body["exists"] is False
        assert body["path"] == str(tmp_path / "iterate.config.yaml")

    def test_put_config_requires_confirm(self, client: TestClient, tmp_path: Path):
        response = client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path)},
            json=VALID_CONFIG,
        )
        assert response.status_code == 422

    def test_put_config_validation_failure(self, client: TestClient, tmp_path: Path):
        response = client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"dimensions": []},  # missing goal + validation
        )
        assert response.status_code == 422

    def test_put_config_writes_and_backs_up(self, client: TestClient, tmp_path: Path):
        first = client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json=VALID_CONFIG,
        )
        assert first.status_code == 200
        assert first.json()["status"] == "ok"
        assert (tmp_path / "iterate.config.yaml").exists()

        second_config = {**VALID_CONFIG, "max_rounds": 9}
        second = client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json=second_config,
        )
        assert second.status_code == 200
        assert (tmp_path / "iterate.config.yaml.bak.webui").exists()
        assert "config.update" in (tmp_path / ".iterate" / "web-audit.jsonl").read_text(encoding="utf-8")

    def test_get_config_after_write_echoes_redacted(self, client: TestClient, tmp_path: Path):
        config = dict(VALID_CONFIG)
        config["provider"] = {"api_key": "sk-super-secret-123456"}
        client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json=config,
        )
        body = client.get("/api/v1/config", params={"project_root": str(tmp_path)}).json()
        raw = body["raw"]
        assert raw["provider"]["api_key"] != "sk-super-secret-123456"
        assert "sk-super-secret" not in str(raw)

    def test_providers_route(self, client: TestClient, tmp_path: Path):
        body = client.get(
            "/api/v1/config/providers", params={"project_root": str(tmp_path)}
        ).json()
        assert "active" in body
        assert isinstance(body["profiles"], dict)


class TestReports:
    def test_list_reports(self, client: TestClient, tmp_path: Path):
        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".iterate" / "report.html").write_text("<html>report</html>", encoding="utf-8")
        body = client.get("/api/v1/reports", params={"project_root": str(tmp_path)}).json()
        assert [r["name"] for r in body] == ["report.html"]

    def test_preview_report(self, client: TestClient, tmp_path: Path):
        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".iterate" / "report.html").write_text("<html>report</html>", encoding="utf-8")
        body = client.get(
            "/api/v1/reports/preview",
            params={"project_root": str(tmp_path), "name": "report.html"},
        ).json()
        assert body["content"] == "<html>report</html>"

    def test_preview_blocks_traversal(self, client: TestClient, tmp_path: Path):
        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        response = client.get(
            "/api/v1/reports/preview",
            params={"project_root": str(tmp_path), "name": "../../etc/passwd"},
        )
        assert response.status_code == 422

    def test_preview_missing_file_404(self, client: TestClient, tmp_path: Path):
        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        response = client.get(
            "/api/v1/reports/preview",
            params={"project_root": str(tmp_path), "name": "nope.html"},
        )
        assert response.status_code == 404


class TestEvents:
    def test_events_route_registered(self):
        app = create_app()
        paths = {route.path for route in app.routes}
        assert "/api/v1/events" in paths

    def test_build_status_payload_keys(self, tmp_path: Path):
        payload = events_module._build_status_payload(tmp_path)
        for key in (
            "entryCount",
            "latestRound",
            "checkpointExists",
            "checkpointRound",
            "totalTokens",
            "totalCostUsd",
            "converged",
            "timestamp",
        ):
            assert key in payload

    def test_event_generator_yields_status(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(events_module, "_POLL_INTERVAL", 0.0)
        gen = events_module._event_generator(tmp_path)
        first = next(itertools.islice(gen, 1))
        assert first.startswith("event: status\n")


class TestFrontendMount:
    """Verify the static-bundle resolution order (api._frontend_dir)."""

    def test_prefers_bundled_dir(self, tmp_path: Path, monkeypatch):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        monkeypatch.setattr(api_module, "_FRONTEND_BUNDLE_DIR", bundled)
        monkeypatch.setattr(api_module, "_FRONTEND_SOURCE_DIR", tmp_path / "missing")
        assert api_module._frontend_dir() == bundled

    def test_falls_back_to_source_dir(self, tmp_path: Path, monkeypatch):
        source = tmp_path / "dist"
        source.mkdir()
        monkeypatch.setattr(api_module, "_FRONTEND_BUNDLE_DIR", tmp_path / "missing")
        monkeypatch.setattr(api_module, "_FRONTEND_SOURCE_DIR", source)
        assert api_module._frontend_dir() == source

    def test_returns_none_when_both_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(api_module, "_FRONTEND_BUNDLE_DIR", tmp_path / "missing-a")
        monkeypatch.setattr(api_module, "_FRONTEND_SOURCE_DIR", tmp_path / "missing-b")
        assert api_module._frontend_dir() is None

    def test_create_app_mounts_frontend_when_present(self, tmp_path: Path, monkeypatch):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "index.html").write_text("<html>ui</html>", encoding="utf-8")
        monkeypatch.setattr(api_module, "_FRONTEND_BUNDLE_DIR", bundled)
        monkeypatch.setattr(api_module, "_FRONTEND_SOURCE_DIR", tmp_path / "missing")
        app = api_module.create_app(tmp_path)
        response = TestClient(app).get("/")
        assert response.status_code == 200
        assert "<html>ui</html>" in response.text
