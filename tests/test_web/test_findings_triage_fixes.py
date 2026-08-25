"""Regression tests for findings-triage journal + findings filtering fixes.

Covers:
- ``record_decision`` must surface a failed journal append (OSError) instead
  of silently returning an "ok" record that was never persisted.
- ``clear_decision`` / ``clear_all`` must hold an exclusive file lock around
  the read-modify-rewrite so concurrent appends are never lost.
- ``GET /runs/findings`` must apply severity/dimension filters *before*
  deduplication so a finding re-reported in a later round with a different
  severity stays visible under a filtered view.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.iterate.decision_log import append_entry
from iterate_harness.iterate.types import DecisionLogEntry
from iterate_harness.web import findings_triage
from iterate_harness.web.api import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())


def build_entry(*, timestamp: str, round: int, type: str, data: dict[str, object]):
    return DecisionLogEntry(timestamp=timestamp, round=round, type=type, data=data)


# ---------------------------------------------------------------------------
# Defect 4: silent OSError swallow + lock-free read-modify-write races
# ---------------------------------------------------------------------------


class TestRecordDecisionFailure:
    @staticmethod
    def _flaky_journal_open(monkeypatch: pytest.MonkeyPatch) -> None:
        """Make appending to the triage journal raise OSError (Path.open does
        not route through builtins.open, so patch Path.open directly)."""
        real_path_open = Path.open

        def flaky_open(self, mode="r", *args, **kwargs):
            if str(self).endswith("findings-triage.jsonl") and "a" in mode:
                raise OSError("simulated disk full")
            return real_path_open(self, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", flaky_open)

    def test_append_failure_raises_oserror(self, tmp_path: Path, monkeypatch):
        """A failed journal append must raise (never return a phantom record
        claiming success without persisting anything)."""
        self._flaky_journal_open(monkeypatch)
        with pytest.raises(OSError, match="simulated disk full"):
            findings_triage.record_decision(
                tmp_path, file="a.py", line=1, dimension="security", decision="approve"
            )

    def test_route_returns_500_on_append_failure(
        self, client: TestClient, tmp_path: Path, monkeypatch
    ):
        self._flaky_journal_open(monkeypatch)
        response = client.post(
            "/api/v1/runs/findings/triage",
            params={"project_root": str(tmp_path), "confirm": "true"},
            json={"file": "a.py", "line": 3, "dimension": "security", "decision": "approve"},
        )
        assert response.status_code == 500
        assert "write failed" in response.json()["detail"]


class TestClearUsesLock:
    def test_record_and_clear_hold_exclusive_lock(self, tmp_path: Path, monkeypatch):
        """Every mutation (append / clear) must serialize on the journal lock
        file so a clear's read-modify-rewrite never races an append."""
        calls: list[str] = []
        real_lock = findings_triage.exclusive_file_lock

        def spy_lock(lock_path, *, platform_name=None):
            calls.append(str(lock_path))
            return real_lock(lock_path, platform_name=platform_name)

        monkeypatch.setattr(findings_triage, "exclusive_file_lock", spy_lock)

        findings_triage.record_decision(
            tmp_path, file="a.py", line=1, dimension="security", decision="approve"
        )
        findings_triage.clear_decision(tmp_path, file="a.py", line=1, dimension="security")

        assert len(calls) == 2
        assert all(call.endswith("findings-triage.jsonl.lock") for call in calls)

    def test_concurrent_record_and_clear_never_lose_appends(self, tmp_path: Path):
        """Concurrent appends + clears must leave every appended decision
        visible; an unlocked clear read stale state and rewrote it away."""
        findings_triage.record_decision(
            tmp_path, file="seed.py", line=1, dimension="security", decision="approve"
        )

        def appender() -> None:
            for i in range(15):
                findings_triage.record_decision(
                    tmp_path, file=f"f{i}.py", line=1, dimension="security", decision="approve"
                )

        def clearer() -> None:
            for _ in range(10):
                findings_triage.clear_decision(
                    tmp_path, file="seed.py", line=1, dimension="security"
                )

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(appender) for _ in range(4)] + [
                pool.submit(clearer) for _ in range(2)
            ]
            for future in futures:
                future.result()

        decisions = findings_triage.load_decisions(tmp_path)
        files = {record.get("file") for record in decisions.values()}
        for i in range(15):
            assert f"f{i}.py" in files, f"appended decision f{i}.py was lost by clear race"

    def test_rewrite_failure_raises(self, tmp_path: Path, monkeypatch):
        findings_triage.record_decision(
            tmp_path, file="a.py", line=1, dimension="security", decision="approve"
        )

        def failing_replace(self, target):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(Path, "replace", failing_replace)
        with pytest.raises(OSError, match="simulated rename failure"):
            findings_triage.clear_all(tmp_path)
        # The original journal is untouched after a failed compaction.
        remaining = findings_triage.load_decisions(tmp_path)
        assert len(remaining) == 1


# ---------------------------------------------------------------------------
# Defect 9: findings filter applied after dedup hides re-reported findings
# ---------------------------------------------------------------------------


class TestFindingsFilterBeforeDedup:
    def _populate_severity_changed_finding(self, tmp_path: Path) -> None:
        append_entry(
            tmp_path,
            build_entry(
                timestamp="2026-08-17T00:00:00+00:00",
                round=1,
                type="review_result",
                data={
                    "findings": [
                        {
                            "file": "a.py",
                            "line": 3,
                            "dimension": "security",
                            "severity": "high",
                            "summary": "round1-high",
                        }
                    ]
                },
            ),
        )
        append_entry(
            tmp_path,
            build_entry(
                timestamp="2026-08-17T00:01:00+00:00",
                round=2,
                type="review_result",
                data={
                    "findings": [
                        {
                            "file": "a.py",
                            "line": 3,
                            "dimension": "security",
                            "severity": "low",
                            "summary": "round2-low",
                        }
                    ]
                },
            ),
        )

    def test_low_severity_view_shows_reported_low_finding(self, client: TestClient, tmp_path: Path):
        """The same (file, line, dimension) was high in round 1 and low in
        round 2. Filtering by severity=low must still surface it — the old
        dedup-first ordering hid it behind the round-1 high occurrence."""
        self._populate_severity_changed_finding(tmp_path)
        body = client.get(
            "/api/v1/runs/findings",
            params={"project_root": str(tmp_path), "severity": "low"},
        ).json()
        assert body["total"] == 1
        assert body["findings"][0]["summary"] == "round2-low"

    def test_high_severity_view_shows_first_occurrence(self, client: TestClient, tmp_path: Path):
        self._populate_severity_changed_finding(tmp_path)
        body = client.get(
            "/api/v1/runs/findings",
            params={"project_root": str(tmp_path), "severity": "high"},
        ).json()
        assert body["total"] == 1
        assert body["findings"][0]["summary"] == "round1-high"

    def test_unfiltered_view_dedupes_to_one(self, client: TestClient, tmp_path: Path):
        self._populate_severity_changed_finding(tmp_path)
        body = client.get(
            "/api/v1/runs/findings", params={"project_root": str(tmp_path)}
        ).json()
        assert body["total"] == 1  # same key deduped across rounds
        assert body["findings"][0]["summary"] == "round1-high"
