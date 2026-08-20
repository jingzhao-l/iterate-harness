"""Integration tests for the WebUI API routes (design §17.3 P1–P7).

Uses FastAPI's TestClient against ``create_app()`` with an explicit
``project_root`` per request pointing at a ``tmp_path`` project so the tests
never touch the real working directory or user config.
"""

from __future__ import annotations

import json
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

    def test_put_config_preserves_redacted_secrets(self, client: TestClient, tmp_path: Path):
        """Saving the redacted editor draft must not clobber real credentials.

        Regression: GET /config redacts ``api_key`` to ``<redacted:...>`` and the
        editor submits that redacted draft verbatim; the write-back must restore
        the original value from the on-disk config.
        """
        import yaml

        config = dict(VALID_CONFIG)
        config["provider"] = {"api_key": "sk-super-secret-123456", "base_url": "https://api.example"}
        first = client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json=config,
        )
        assert first.status_code == 200

        # The editor's view (redacted)…
        redacted = client.get("/api/v1/config", params={"project_root": str(tmp_path)}).json()["raw"]
        assert redacted["provider"]["api_key"].startswith("<redacted:")
        assert "sk-super-secret-123456" not in str(redacted)

        # …is saved back after a non-secret edit.
        edited = {**redacted, "max_rounds": 7}
        result = client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json=edited,
        )
        assert result.status_code == 200

        on_disk = yaml.safe_load((tmp_path / "iterate.config.yaml").read_text(encoding="utf-8"))
        assert on_disk["provider"]["api_key"] == "sk-super-secret-123456"
        assert on_disk["max_rounds"] == 7
        # A brand-new secret typed by the user is kept as-is (not treated as a marker).
        fresh = dict(VALID_CONFIG)
        fresh["provider"] = {"api_key": "sk-fresh-secret-abcdef"}
        client.put(
            "/api/v1/config",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json=fresh,
        )
        on_disk2 = yaml.safe_load((tmp_path / "iterate.config.yaml").read_text(encoding="utf-8"))
        assert on_disk2["provider"]["api_key"] == "sk-fresh-secret-abcdef"

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

    def test_list_reports_populates_modified(self, client: TestClient, tmp_path: Path):
        """A normal report file must surface a usable ISO-8601 ``modified`` so
        the Reports page can show when each artifact was generated."""
        import re

        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".iterate" / "report.html").write_text("<html>report</html>", encoding="utf-8")
        body = client.get("/api/v1/reports", params={"project_root": str(tmp_path)}).json()
        assert len(body) == 1
        modified = body[0]["modified"]
        # e.g. 2026-08-17T00:00:00.123456+00:00 (fractional seconds optional)
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$", modified) is not None

    def test_modified_iso_overflow_returns_none(self, tmp_path: Path):
        """An mtime the platform cannot represent must degrade cleanly to None
        (with a logged warning) instead of raising / silently swallowing."""
        from types import SimpleNamespace

        from iterate_harness.web.routes import reports

        stat = SimpleNamespace(st_mtime=10**20)  # far beyond representable range
        assert reports._to_modified_iso(stat) is None  # type: ignore[arg-type]

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

    def test_preview_rejects_non_report_files_in_iterate(self, client: TestClient, tmp_path: Path):
        """Preview must not read arbitrary files under .iterate (e.g. the audit
        or triage journals) — only the whitelisted report artifacts."""
        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".iterate" / "web-audit.jsonl").write_text(
            '{"action":"config.update"}\n', encoding="utf-8"
        )
        (tmp_path / ".iterate" / "findings-triage.jsonl").write_text(
            '{"key":"a.py"}\n', encoding="utf-8"
        )
        # The file exists and sits inside .iterate (so resolve_within passes),
        # but its name is not a report artifact -> 404, content never returned.
        response = client.get(
            "/api/v1/reports/preview",
            params={"project_root": str(tmp_path), "name": "web-audit.jsonl"},
        )
        assert response.status_code == 404
        response = client.get(
            "/api/v1/reports/preview",
            params={"project_root": str(tmp_path), "name": "findings-triage.jsonl"},
        )
        assert response.status_code == 404

    def test_preview_still_allows_every_report_filename(self, client: TestClient, tmp_path: Path):
        (tmp_path / ".iterate").mkdir(parents=True, exist_ok=True)
        for name in ("report.html", "replay.html", "report.csv"):
            (tmp_path / ".iterate" / name).write_text(f"<{name}>", encoding="utf-8")
            response = client.get(
                "/api/v1/reports/preview",
                params={"project_root": str(tmp_path), "name": name},
            )
            assert response.status_code == 200, name


class TestEvents:
    def test_events_route_registered(self):
        app = create_app()
        assert app.url_path_for("stream_events") == "/api/v1/events"

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

    @pytest.mark.asyncio
    async def test_event_generator_yields_status(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(events_module, "_POLL_INTERVAL", 0.0)
        gen = events_module._event_generator(tmp_path, stream_all=True)
        first = await anext(gen)
        assert first.startswith("event: status\n")


class TestFindingsTriage:
    def test_list_empty(self, client: TestClient, tmp_path: Path):
        body = client.get("/api/v1/runs/findings/triage", params={"project_root": str(tmp_path)}).json()
        assert body == []

    def test_record_requires_confirm(self, client: TestClient, tmp_path: Path):
        response = client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path)},
            json={"file": "a.py", "line": 3, "dimension": "security", "decision": "approve"},
        )
        assert response.status_code == 422
        assert "triage requires confirm=true" in response.json()["detail"]

    def test_record_ok_and_audited(self, client: TestClient, tmp_path: Path):
        body = client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"file": "a.py", "line": 3, "dimension": "security", "decision": "approve", "note": "this is correct"},
        ).json()
        assert body["status"] == "ok"
        assert "已记录审批" in body["message"]
        assert "approve" in body["message"]
        assert body["detail"]["record"]["file"] == "a.py"
        assert body["detail"]["record"]["line"] == 3
        assert body["detail"]["record"]["decision"] == "approve"
        assert (tmp_path / ".iterate" / "findings-triage.jsonl").exists()
        audit = (tmp_path / ".iterate" / "web-audit.jsonl").read_text(encoding="utf-8")
        assert "findings.triage" in audit

    def test_list_after_record(self, client: TestClient, tmp_path: Path):
        # Add two decisions
        client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"file": "a.py", "line": 3, "dimension": "security", "decision": "approve"},
        )
        client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"file": "b.py", "line": 1, "dimension": "code_review", "decision": "reject"},
        )
        body = client.get("/api/v1/runs/findings/triage", params={"project_root": str(tmp_path)}).json()
        assert len(body) == 2
        # Most recent first — second was added later, so first in list
        assert body[0]["file"] == "b.py"
        assert body[1]["file"] == "a.py"

    def test_null_line_key_canonicalized(self, client: TestClient, tmp_path: Path):
        """A finding without a line number must use a key matching the frontend.

        Regression: the backend key previously rendered ``None`` as the literal
        string "None" while the frontend rendered a missing line as "", so the
        persisted decision never matched the row after a reload.
        """
        body = client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"file": "arch.py", "dimension": "architecture", "decision": "approve"},
        ).json()
        record = body["detail"]["record"]
        assert record["line"] is None
        # file + "" + dimension → "arch.py::::::architecture"
        assert record["key"] == "arch.py::::::architecture"

        # Re-triaging the same (file, None, dimension) must reuse the same key.
        body2 = client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"file": "arch.py", "dimension": "architecture", "decision": "reject"},
        ).json()
        assert body2["detail"]["record"]["key"] == "arch.py::::::architecture"
        assert len(client.get("/api/v1/runs/findings/triage", params={"project_root": str(tmp_path)}).json()) == 1

    def test_clear_requires_confirm(self, client: TestClient, tmp_path: Path):
        response = client.delete(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path)},
        )
        assert response.status_code == 422

    def test_clear_clears_all(self, client: TestClient, tmp_path: Path):
        # Add two
        client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"file": "a.py", "line": 3, "dimension": "security", "decision": "approve"},
        )
        client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"file": "b.py", "line": 1, "dimension": "code_review", "decision": "reject"},
        )
        assert len(client.get("/api/v1/runs/findings/triage", params={"project_root": str(tmp_path)}).json()) == 2

        body = client.delete(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
        ).json()
        assert body["status"] == "ok"
        assert body["detail"]["removed"] == 2
        assert len(client.get("/api/v1/runs/findings/triage", params={"project_root": str(tmp_path)}).json()) == 0

    def _record(self, client: TestClient, tmp_path: Path, *, file: str, line, dimension: str, decision: str):
        return client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={
                "file": file,
                "line": line,
                "dimension": dimension,
                "decision": decision,
            },
        )

    def _dismiss(self, client: TestClient, tmp_path: Path, *, file: str, line, dimension: str, confirm: bool = False):
        # The bundled starlette TestClient (requests-backed) ``delete()``
        # helper forwards no body kwargs; route through the generic
        # ``request()`` wrapper so the JSON body + content-type reach FastAPI.
        return client.request(
            "DELETE",
            "/api/v1/runs/findings/triage/dismiss",
            params={"project_root": str(tmp_path), "confirm": str(confirm).lower()},
            content=json.dumps({"file": file, "line": line, "dimension": dimension}),
            headers={"Content-Type": "application/json"},
        )

    def test_dismiss_requires_confirm(self, client: TestClient, tmp_path: Path):
        response = self._dismiss(
            client, tmp_path, file="a.py", line=3, dimension="security", confirm=False
        )
        assert response.status_code == 422
        assert "dismiss triage requires confirm=true" in response.json()["detail"]

    def test_dismiss_removes_single_decision(self, client: TestClient, tmp_path: Path):
        self._record(client, tmp_path, file="a.py", line=3, dimension="security", decision="approve")
        self._record(client, tmp_path, file="b.py", line=1, dimension="code_review", decision="reject")
        assert len(client.get("/api/v1/runs/findings/triage", params={"project_root": str(tmp_path)}).json()) == 2

        response = self._dismiss(
            client, tmp_path, file="a.py", line=3, dimension="security", confirm=True
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        # Only the targeted decision is removed (not a full clear).
        assert body["detail"]["removed"] == 1
        remaining = client.get("/api/v1/runs/findings/triage", params={"project_root": str(tmp_path)}).json()
        assert [r["file"] for r in remaining] == ["b.py"]

    def test_dismiss_is_audited(self, client: TestClient, tmp_path: Path):
        self._record(client, tmp_path, file="a.py", line=3, dimension="security", decision="approve")
        self._dismiss(client, tmp_path, file="a.py", line=3, dimension="security", confirm=True)
        audit = (tmp_path / ".iterate" / "web-audit.jsonl").read_text(encoding="utf-8")
        assert "findings.triage.dismiss" in audit

    def test_dismiss_missing_finding_returns_removed_zero(self, client: TestClient, tmp_path: Path):
        response = self._dismiss(
            client, tmp_path, file="never.json", line=1, dimension="security", confirm=True
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["detail"]["removed"] == 0
        assert "未找到" in body["message"]


class TestWorkspaces:
    def test_list_includes_primary(self, client: TestClient, tmp_path: Path):
        # tmp_path is an empty directory — should still return the primary workspace
        body = client.get("/api/v1/workspaces", params={"project_root": str(tmp_path)}).json()
        assert len(body) >= 1  # at least primary
        primary = next(w for w in body if w["detail"]["slug"] == "main")
        assert primary["kind"] == "primary"
        assert primary["active"] is True
        assert "entryCount" in primary["detail"]

    def test_remove_requires_confirm(self, client: TestClient, tmp_path: Path):
        response = client.post(
            "/api/v1/workspaces/remove",
            params={"project_root": str(tmp_path)},
            json={"slug": "test-round-1"},
        )
        assert response.status_code == 422
        assert "requires confirm=true" in response.json()["detail"]

    def test_remove_rejects_traversal_slug(self, client: TestClient, tmp_path: Path):
        response = client.post(
            "/api/v1/workspaces/remove",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"slug": "../etc/passwd"},
        )
        assert response.status_code == 422
        assert "must not contain" in response.json()["detail"]

    def test_remove_404_on_missing(self, client: TestClient, tmp_path: Path):
        response = client.post(
            "/api/v1/workspaces/remove",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"slug": "nonexistent-round-99"},
        )
        assert response.status_code == 404


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
