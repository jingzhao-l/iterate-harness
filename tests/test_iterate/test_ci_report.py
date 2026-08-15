"""Tests for the CI report module (GitHub annotations + severity gate)."""

from __future__ import annotations

from typer.testing import CliRunner

import pytest

import openharness.cli as cli
from openharness.iterate import ci_report
from openharness.iterate.decision_log import append_entry, make_entry, read_entries


def report_entry(
    *,
    findings: list[dict] | None = None,
    verdict: str = "converged",
    mode: str = "dry-run",
    total: int | None = None,
    round_number: int = 2,
):
    data: dict[str, object] = {
        "verdict": verdict,
        "mode": mode,
        "findings": findings if findings is not None else [],
    }
    if total is not None:
        data["totalFindings"] = total
    return make_entry(entry_type="report", round_number=round_number, data=data)


class TestReportSummary:
    def test_parses_normal_entry(self):
        entry = report_entry(
            findings=[{"severity": "high", "file": "a.py", "line": 3, "summary": "x"}],
            verdict="converged",
            mode="normal",
            total=1,
        )
        summary = ci_report.ReportSummary.from_entry(entry)
        assert summary.verdict == "converged"
        assert summary.mode == "normal"
        assert summary.total_findings == 1
        assert len(summary.findings) == 1

    def test_none_entry_yields_defaults(self):
        summary = ci_report.ReportSummary.from_entry(None)
        assert summary.verdict == "unknown"
        assert summary.total_findings == 0
        assert summary.findings == []
        assert summary.mode == "dry-run"

    def test_malformed_findings_filtered(self):
        entry = report_entry(findings=[{"severity": "low"}, "junk", 42, None])  # type: ignore[list-item]
        summary = ci_report.ReportSummary.from_entry(entry)
        assert summary.findings == [{"severity": "low"}]

    def test_findings_not_a_list(self):
        entry = report_entry()
        entry.data["findings"] = "oops"
        summary = ci_report.ReportSummary.from_entry(entry)
        assert summary.findings == []

    def test_total_findings_falls_back_to_len(self):
        entry = report_entry(findings=[{"severity": "low"}, {"severity": "low"}], total="bad")  # type: ignore[arg-type]
        summary = ci_report.ReportSummary.from_entry(entry)
        assert summary.total_findings == 2

    def test_default_factory_isolation(self):
        """Two default instances must not share the findings list."""
        first = ci_report.ReportSummary.from_entry(None)
        second = ci_report.ReportSummary.from_entry(None)
        first.findings.append({"severity": "low"})
        assert second.findings == []


class TestLatestReportEntry:
    def test_returns_last_report_with_findings(self, tmp_path):
        append_entry(tmp_path, report_entry(findings=[{"severity": "low"}], round_number=1))
        append_entry(tmp_path, make_entry(entry_type="decision", round_number=1, data={"kind": "triage"}))
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "critical"}], verdict="cap-reached", round_number=3),
        )
        entry = ci_report.latest_report_entry(read_entries(tmp_path))
        assert entry is not None and entry.round == 3

    def test_skips_report_entries_without_findings_key(self, tmp_path):
        append_entry(
            tmp_path,
            make_entry(entry_type="report", round_number=1, data={"verdict": "converged"}),
        )
        entry = ci_report.latest_report_entry(read_entries(tmp_path))
        assert entry is None

    def test_empty_log_returns_none(self):
        assert ci_report.latest_report_entry([]) is None


class TestRenderGithub:
    def test_header_notice_line(self):
        text = ci_report.render_github(ci_report.ReportSummary.from_entry(report_entry(total=2)))
        assert text.splitlines()[0].startswith("::notice::")
        assert "2 finding(s)" in text
        assert "verdict=converged" in text

    def test_annotation_levels_by_severity(self):
        findings = [
            {"severity": "critical", "file": "a.py", "line": 1, "summary": "c", "dimension": "security"},
            {"severity": "high", "file": "b.py", "line": 2, "summary": "h", "dimension": "security"},
            {"severity": "medium", "file": "c.py", "line": 3, "summary": "m", "dimension": "performance"},
            {"severity": "low", "file": "d.py", "summary": "l", "dimension": "style"},
            {"severity": "bizarre", "summary": "?", "dimension": "x"},
        ]
        text = ci_report.render_github(ci_report.ReportSummary.from_entry(report_entry(findings=findings)))
        lines = text.splitlines()[1:]
        assert lines[0].startswith("::error file=a.py line=1::")
        assert lines[1].startswith("::error file=b.py line=2::")
        assert lines[2].startswith("::warning file=c.py line=3::")
        assert lines[3].startswith("::notice file=d.py::")  # no line → no line property
        assert lines[4].startswith("::notice::[bizarre]")  # unknown severity + no file

    def test_escapes_workflow_syntax(self):
        finding = {"severity": "high", "file": "pa,th:name.py", "line": 9, "summary": "100% bad\nnewline"}
        text = ci_report.render_github(ci_report.ReportSummary.from_entry(report_entry(findings=[finding])))
        annotation = text.splitlines()[1]
        assert "file=pa%2Cth%3Aname.py" in annotation
        assert "%0A" in annotation  # newline escaped
        assert "100%25 bad" in annotation
        assert "\n" not in annotation.split("::")[-1]  # single-line command


class TestRenderText:
    def test_header_only_when_clean(self):
        text = ci_report.render_text(ci_report.ReportSummary.from_entry(report_entry()))
        assert text == "iterate report (dry-run): 0 finding(s), verdict=converged"

    def test_rows_include_location(self):
        findings = [
            {"severity": "high", "file": "src/a.py", "line": 7, "summary": "sql injection", "dimension": "security"},
            {"severity": "low", "summary": "unclear naming", "dimension": "readability"},
        ]
        text = ci_report.render_text(ci_report.ReportSummary.from_entry(report_entry(findings=findings, mode="normal")))
        lines = text.splitlines()
        assert lines[0].startswith("iterate report (normal):")
        assert "[high] src/a.py:7 security: sql injection" in lines[1]
        assert "[low] (no file) readability: unclear naming" in lines[2]


class TestSeverityGate:
    def make_summary(self, severities: list[str]) -> ci_report.ReportSummary:
        return ci_report.ReportSummary.from_entry(
            report_entry(findings=[{"severity": s, "summary": s} for s in severities])
        )

    def test_default_gate_high(self):
        assert ci_report.severity_gate(self.make_summary(["low", "medium"])) == 0
        assert ci_report.severity_gate(self.make_summary(["high"])) == 1
        assert ci_report.severity_gate(self.make_summary(["critical"])) == 1

    def test_threshold_medium(self):
        assert ci_report.severity_gate(self.make_summary(["low"]), fail_on="medium") == 0
        assert ci_report.severity_gate(self.make_summary(["medium"]), fail_on="medium") == 1

    def test_threshold_none_always_passes(self):
        assert ci_report.severity_gate(self.make_summary(["critical"]), fail_on="none") == 0

    def test_invalid_threshold_falls_back_to_high(self):
        assert ci_report.severity_gate(self.make_summary(["medium"]), fail_on="garbage") == 0
        assert ci_report.severity_gate(self.make_summary(["high"]), fail_on="garbage") == 1

    def test_unknown_severity_ignored(self):
        assert ci_report.severity_gate(self.make_summary(["catastrophic"])) == 0

    def test_empty_report_passes(self):
        assert ci_report.severity_gate(ci_report.ReportSummary.from_entry(None)) == 0


class TestIterateReportCli:
    """`oh iterate report` CLI end-to-end (decision log → render + exit code)."""

    def test_text_render_and_exit_zero_when_below_gate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "medium", "file": "a.py", "line": 2, "summary": "s", "dimension": "security"}]),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report"])
        assert result.exit_code == 0
        assert "iterate report (dry-run): 1 finding(s)" in result.output
        assert "[medium] a.py:2" in result.output

    def test_exit_one_when_at_or_above_gate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "critical", "file": "a.py", "line": 1, "summary": "s", "dimension": "security"}]),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report"])
        assert result.exit_code == 1

    def test_github_mode_emits_workflow_commands(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "high", "file": "a.py", "line": 4, "summary": "s", "dimension": "security"}]),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--github"])
        assert result.exit_code == 1
        assert "::notice::iterate report" in result.output
        assert "::error file=a.py line=4::" in result.output

    def test_fail_on_none_overrides_gate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "critical", "file": "a.py", "line": 1, "summary": "s", "dimension": "security"}]),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--fail-on", "none"])
        assert result.exit_code == 0

    def test_missing_report_degrades_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli.app, ["iterate", "report"])
        assert result.exit_code == 0
        assert "No report entry" in result.output

    def test_invalid_fail_on_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--fail-on", "nope"])
        assert result.exit_code == 2  # typer.BadParameter usage error
        assert "must be one of" in result.output


class TestIterateReportSlashCommand:
    """/iterate report parity in the REPL."""

    @staticmethod
    async def _run(args: str, cwd):
        from openharness.commands.iterate import iterate_command_handler
        from openharness.commands.registry import CommandContext

        return await iterate_command_handler(args, CommandContext(engine=None, cwd=str(cwd)))  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_renders_latest_report(self, tmp_path):
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "low", "file": "a.py", "line": 5, "summary": "s", "dimension": "style"}]),
        )
        result = await self._run("report", tmp_path)
        assert "[low] a.py:5" in (result.message or "")

    @pytest.mark.asyncio
    async def test_friendly_when_no_report(self, tmp_path):
        result = await self._run("report", tmp_path)
        assert "No report entry" in (result.message or "")
