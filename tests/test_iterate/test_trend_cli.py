"""CLI tests for ``ih iterate trend`` (the cross-run detail command)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.iterate.decision_log import DecisionLogEntry, append_entry


def _finding(file: str, severity: str = "high", line: int = 1) -> dict[str, object]:
    return {
        "file": file,
        "line": line,
        "dimension": "security",
        "severity": severity,
        "summary": f"problem in {file}",
    }


def _report(root: Path, timestamp: str, findings: list[dict[str, object]]) -> None:
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


def _three_runs(root: Path) -> None:
    # run 1: a + b, run 2: a, run 3: a + b (b regressed).
    _report(root, "2026-01-01T00:00:00+00:00", [_finding("a.py"), _finding("b.py")])
    _report(root, "2026-01-02T00:00:00+00:00", [_finding("a.py")])
    _report(root, "2026-01-03T00:00:00+00:00", [_finding("a.py"), _finding("b.py")])


def test_trend_without_runs_exits_zero_and_explains_next_step(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    result = CliRunner().invoke(cli.app, ["iterate", "trend"])
    assert result.exit_code == 0
    assert "No trend data yet" in result.output
    assert "ih iterate trend" in result.output


def test_trend_text_reports_history_and_latest_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _three_runs(tmp_path)
    result = CliRunner().invoke(cli.app, ["iterate", "trend"])
    assert result.exit_code == 0, result.output
    assert "3 finished run(s)" in result.output
    assert "latest run regressions (1)" in result.output
    assert "b.py:1" in result.output


def test_trend_json_is_machine_readable(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _three_runs(tmp_path)
    result = CliRunner().invoke(cli.app, ["iterate", "trend", "--json", "--history", "2"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_runs"] == 3
    assert len(payload["runs"]) == 2
    assert payload["latest"]["regressed"] == 1
    assert payload["latest"]["regressed_findings"][0]["file"] == "b.py"


def test_fail_on_regression_gate(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _three_runs(tmp_path)
    result = CliRunner().invoke(cli.app, ["iterate", "trend", "--fail-on-regression", "--json"])
    assert result.exit_code == 1
    assert "trend gate: 1 regression(s)" in result.output


def test_fail_on_new_gate_and_clean_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _report(tmp_path, "2026-01-01T00:00:00+00:00", [_finding("a.py", severity="low")])
    assert (
        CliRunner().invoke(cli.app, ["iterate", "trend", "--fail-on-new", "high"]).exit_code
        == 0
    )
    assert (
        CliRunner().invoke(cli.app, ["iterate", "trend", "--fail-on-new", "low"]).exit_code
        == 1
    )


def test_unknown_fail_on_new_severity_is_a_usage_error(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["iterate", "trend", "--fail-on-new", "urgent"])
    assert result.exit_code != 0
    assert "unknown severity" in result.output


def test_log_trend_flag_still_works(tmp_path, monkeypatch) -> None:
    # Backwards compatibility: the old thin summary stays reachable.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    result = CliRunner().invoke(cli.app, ["iterate", "log", "--trend"])
    assert result.exit_code == 0
    assert "trend library" in result.output
