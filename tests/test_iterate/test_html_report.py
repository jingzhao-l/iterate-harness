"""Tests for the single-file HTML report (iterate_harness.iterate.html_report)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from iterate_harness.commands.iterate import iterate_command_handler
from iterate_harness.commands.registry import CommandContext
from iterate_harness.iterate import html_report
from iterate_harness.iterate.decision_log import DecisionLogEntry, make_entry

# ---- module fixtures ----------------------------------------------------


def _finding(severity: str = "high", file: str = "src/app.py", summary: str = "x < 1") -> dict:
    return {
        "dimension": "correctness",
        "file": file,
        "line": 12,
        "severity": severity,
        "summary": summary,
        "failure_scenario": "when x is 0",
        "suggested_fix": "guard x",
        "is_atomic": True,
    }


def _report_data(**overrides: object) -> dict:
    data: dict = {
        "mode": "dry-run",
        "goal": "review the project",
        "verdict": "approved",
        "findings": [_finding(), _finding("medium", "src/lib.py")],
        "convergence": {
            "totalRounds": 2,
            "findingsByRound": [3, 1],
            "converged": True,
            "stoppedReason": "converged",
        },
        "summary": {
            "totalFindings": 2,
            "critical": 0,
            "high": 1,
            "medium": 1,
            "low": 0,
            "byDimension": {"correctness": 2},
        },
    }
    data.update(overrides)
    return data


def _entries_with_report(data: dict) -> list[DecisionLogEntry]:
    return [
        make_entry(entry_type="round_start", round_number=1),
        make_entry(
            entry_type="atomic_fix",
            round_number=1,
            data={"file": "src/app.py", "summary": "guard x", "diff": "- x < 1\n+ x <= 1"},
        ),
        make_entry(entry_type="validation", round_number=1, data={"command": "pytest"}),
        DecisionLogEntry(
            timestamp="2026-08-15T10:00:00+00:00", round=2, type="report", data=data
        ),
    ]


# ---- build_html_report --------------------------------------------------


class TestBuildHtmlReport:
    def test_none_when_no_report_entry(self):
        assert html_report.build_html_report([]) is None
        assert (
            html_report.build_html_report([make_entry(entry_type="round_start", round_number=1)])
            is None
        )

    def test_renders_single_file_page(self):
        page = html_report.build_html_report(_entries_with_report(_report_data()))
        assert page is not None
        assert page.startswith("<!DOCTYPE html>")
        assert page.rstrip().endswith("</html>")
        assert "<script" not in page  # no scripts: static artifact
        assert "http://" not in page and "https://" not in page  # fully offline

    def test_renders_charts_tables_and_timeline(self):
        page = html_report.build_html_report(_entries_with_report(_report_data()))
        assert "<svg" in page  # convergence curve
        assert "Convergence curve" in page
        assert "Severity distribution" in page
        assert "Dimension distribution" in page
        assert "correctness" in page
        assert "Findings</h2>" in page
        assert "src/app.py" in page
        assert "Fix timeline" in page
        assert "atomic_fix" in page and "validation" in page

    def test_escapes_log_content(self):
        data = _report_data()
        data["findings"] = [_finding(summary="<img src=x onerror=alert(1)>")]
        page = html_report.build_html_report(_entries_with_report(data))
        assert "<img" not in page
        assert "&lt;img" in page

    def test_no_findings_renders_placeholder(self):
        data = _report_data()
        data["findings"] = []
        data["summary"] = {
            "totalFindings": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "byDimension": {},
        }
        page = html_report.build_html_report(_entries_with_report(data))
        assert "no findings recorded" in page

    def test_empty_convergence_series_degrades(self):
        data = _report_data()
        data["convergence"] = {"totalRounds": 1, "findingsByRound": [], "converged": False}
        page = html_report.build_html_report(_entries_with_report(data))
        assert "no per-round data recorded" in page

    def test_empty_timeline_renders_placeholder(self):
        data = _report_data()
        entries = [
            DecisionLogEntry(
                timestamp="2026-08-15T10:00:00+00:00", round=2, type="report", data=data
            )
        ]
        page = html_report.build_html_report(entries)
        assert "no fix/validation entries" in page

    def test_timeline_caps_at_limit(self):
        entries = [
            *(
                make_entry(entry_type="atomic_fix", round_number=1, data={"file": f"f{i}.py"})
                for i in range(html_report.MAX_TIMELINE_ENTRIES + 10)
            ),
            DecisionLogEntry(
                timestamp="2026-08-15T10:00:00+00:00",
                round=2,
                type="report",
                data=_report_data(),
            ),
        ]
        page = html_report.build_html_report(entries)
        assert page.count("atomic_fix") == html_report.MAX_TIMELINE_ENTRIES


# ---- CLI --html ---------------------------------------------------------


def _write_log(tmp_path: Path, entries: list[DecisionLogEntry]) -> None:
    log_file = tmp_path / ".iterate" / "decision-log.jsonl"
    log_file.parent.mkdir(exist_ok=True)
    with log_file.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(
                json.dumps(
                    {
                        "timestamp": entry.timestamp,
                        "round": entry.round,
                        "type": entry.type,
                        "data": entry.data,
                    }
                )
                + "\n"
            )


class TestCliHtmlOption:
    def test_writes_default_path(self, tmp_path, monkeypatch):
        from iterate_harness.cli import iterate_report

        monkeypatch.chdir(tmp_path)
        _write_log(tmp_path, _entries_with_report(_report_data()))

        try:
            iterate_report(github=False, html_out="-", fail_on="none")
        except (SystemExit, typer.Exit) as exc:
            assert getattr(exc, "code", 0) in (0, None)

        out = tmp_path / ".iterate" / "report.html"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "src/app.py" in content

    def test_missing_report_writes_nothing(self, tmp_path, monkeypatch, capsys):
        from iterate_harness.cli import iterate_report

        monkeypatch.chdir(tmp_path)
        try:
            iterate_report(github=False, html_out="-", fail_on="none")
        except (SystemExit, typer.Exit) as exc:
            assert getattr(exc, "code", 0) in (0, None)
        assert not (tmp_path / ".iterate" / "report.html").exists()
        assert "No report entry" in capsys.readouterr().err


# ---- slash command --html ----------------------------------------------


class TestSlashReportHtml:
    @pytest.mark.asyncio
    async def test_report_html_writes_file(self, tmp_path):
        _write_log(tmp_path, _entries_with_report(_report_data()))
        context = CommandContext(engine=None, cwd=str(tmp_path))  # type: ignore[arg-type]
        result = await iterate_command_handler("report --html", context)
        out = tmp_path / ".iterate" / "report.html"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "HTML report written" in (result.message or "")
