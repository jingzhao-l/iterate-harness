"""Tests for the CI report module (GitHub annotations + severity gate)."""

from __future__ import annotations

from typing import ClassVar

import pytest
from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.iterate import ci_report
from iterate_harness.iterate.decision_log import append_entry, make_entry, read_entries


def report_entry(
    *,
    findings: list[dict] | None = None,
    verdict: str = "converged",
    mode: str = "dry-run",
    total: int | None = None,
    round_number: int = 2,
    threshold_gate: dict | None = None,
):
    data: dict[str, object] = {
        "verdict": verdict,
        "mode": mode,
        "findings": findings if findings is not None else [],
    }
    if total is not None:
        data["totalFindings"] = total
    if threshold_gate is not None:
        data["thresholdGate"] = threshold_gate
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


class TestThresholdGate:
    """thresholdGate block extraction, rendering, and exit-code policy."""

    FAILED_GATE: ClassVar[dict] = {
        "passed": False,
        "violations": [
            {"scope": "global", "metric": "critical", "limit": 0, "actual": 2},
            {"scope": "dimension:security", "metric": "high", "limit": 1, "actual": 3},
        ],
    }

    def test_extracts_gate_from_entry(self):
        entry = report_entry(threshold_gate=self.FAILED_GATE)
        assert ci_report.threshold_gate(entry) == self.FAILED_GATE

    def test_missing_or_malformed_gate_yields_none(self):
        assert ci_report.threshold_gate(None) is None
        assert ci_report.threshold_gate(report_entry()) is None
        entry = report_entry()
        entry.data["thresholdGate"] = "junk"
        assert ci_report.threshold_gate(entry) is None

    def test_exit_code_zero_when_passed_or_absent(self):
        assert ci_report.threshold_exit_code(None) == 0
        assert ci_report.threshold_exit_code({"passed": True, "violations": []}) == 0
        assert ci_report.threshold_exit_code(self.FAILED_GATE) == 1

    def test_render_text_includes_gate_status_line(self):
        summary = ci_report.ReportSummary.from_entry(report_entry())
        text = ci_report.render_text(summary, self.FAILED_GATE)
        gate_line = text.splitlines()[1]
        assert gate_line.startswith("threshold gate: FAIL")
        assert "global:critical 2>0" in gate_line
        assert "dimension:security:high 3>1" in gate_line

    def test_render_text_passed_gate_without_violations(self):
        summary = ci_report.ReportSummary.from_entry(report_entry())
        assert "threshold gate: PASS" in ci_report.render_text(summary, {"passed": True, "violations": []})

    def test_render_text_caps_violations_at_five(self):
        gate = {
            "passed": False,
            "violations": [
                {"scope": "global", "metric": "low", "limit": 0, "actual": i} for i in range(7)
            ],
        }
        text = ci_report.render_text(ci_report.ReportSummary.from_entry(report_entry()), gate)
        assert "(+2 more)" in text

    def test_cli_exit_one_on_failed_gate_below_severity_gate(self, tmp_path, monkeypatch):
        """Findings below --fail-on but a failed threshold gate must still fail CI."""
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            report_entry(
                findings=[{"severity": "low", "file": "a.py", "line": 1, "summary": "s", "dimension": "style"}],
                threshold_gate=self.FAILED_GATE,
            ),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report"])
        assert result.exit_code == 1
        assert "threshold gate: FAIL" in result.output

    def test_cli_exit_zero_on_passed_gate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            report_entry(
                findings=[{"severity": "low", "file": "a.py", "line": 1, "summary": "s", "dimension": "style"}],
                threshold_gate={"passed": True, "violations": []},
            ),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report"])
        assert result.exit_code == 0
        assert "threshold gate: PASS" in result.output


class TestIterateReportCli:
    """`ih iterate report` CLI end-to-end (decision log → render + exit code)."""

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
        from iterate_harness.commands.iterate import iterate_command_handler
        from iterate_harness.commands.registry import CommandContext

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


class TestRenderTextL10N:
    """Multi-language support for render_text (issue #11)."""

    def test_english_is_default_and_identical(self):
        """Default language='en' must produce the same output as before."""
        text = ci_report.render_text(ci_report.ReportSummary.from_entry(
            report_entry(findings=[{"severity": "high", "file": "a.py", "line": 3, "summary": "x", "dimension": "s"}])
        ))
        expected = ci_report.render_text(ci_report.ReportSummary.from_entry(
            report_entry(findings=[{"severity": "high", "file": "a.py", "line": 3, "summary": "x", "dimension": "s"}])
        ), language="en")
        assert text == expected

    def test_header_zh(self):
        summary = ci_report.ReportSummary.from_entry(
            report_entry(findings=[{"severity": "low", "summary": "t", "dimension": "style"}], mode="normal", total=1)
        )
        text = ci_report.render_text(summary, language="zh")
        lines = text.splitlines()
        assert "iterate 报告（normal）：1 个问题，判定=converged" in lines[0]

    def test_gate_zh_text(self):
        summary = ci_report.ReportSummary.from_entry(report_entry())
        gate = {"passed": False, "violations": [{"scope": "global", "metric": "critical", "limit": 0, "actual": 2}]}
        text = ci_report.render_text(summary, gate, language="zh")
        assert "阈值门禁：未通过" in text

    def test_gate_passed_zh(self):
        summary = ci_report.ReportSummary.from_entry(report_entry())
        text = ci_report.render_text(summary, {"passed": True, "violations": []}, language="zh")
        assert "阈值门禁：通过" in text

    def test_unknown_language_falls_back_to_en(self):
        summary = ci_report.ReportSummary.from_entry(
            report_entry(findings=[{"severity": "low", "summary": "t", "dimension": "s"}])
        )
        text_en = ci_report.render_text(summary, language="en")
        text_xx = ci_report.render_text(summary, language="xx")
        assert text_en == text_xx

    def test_finding_rows_remain_english_in_zh_mode(self):
        """Per-finding rows stay in English even when the header is Chinese."""
        findings = [{"severity": "high", "file": "src/a.py", "line": 7, "summary": "sql injection", "dimension": "security"}]
        summary = ci_report.ReportSummary.from_entry(report_entry(findings=findings, mode="normal"))
        text = ci_report.render_text(summary, language="zh")
        assert "[high] src/a.py:7 security: sql injection" in text


class TestRenderCsv:
    """CSV export (issue #15)."""

    def test_writes_header_and_rows(self, tmp_path):
        findings = [
            {"severity": "high", "dimension": "security", "file": "a.py", "line": 3, "summary": "x", "failure_scenario": "y", "suggested_fix": "z"},
            {"severity": "low", "dimension": "style", "file": "b.py", "line": 5, "summary": "s", "failure_scenario": "", "suggested_fix": ""},
        ]
        summary = ci_report.ReportSummary.from_entry(report_entry(findings=findings))
        out = tmp_path / "out.csv"
        result = ci_report.render_csv(summary, str(out))
        assert result == str(out)
        assert out.exists()
        content = out.read_bytes()
        # UTF-8 BOM
        assert content[:3] == b"\xef\xbb\xbf"
        lines = out.read_text(encoding="utf-8-sig").splitlines()
        assert len(lines) == 3  # header + 2 rows
        assert lines[0] == "severity,dimension,file,line,summary,failure_scenario,suggested_fix"
        assert lines[1] == "high,security,a.py,3,x,y,z"
        assert lines[2] == "low,style,b.py,5,s,,"

    def test_empty_findings_produces_header_only(self, tmp_path):
        summary = ci_report.ReportSummary.from_entry(report_entry())
        out = tmp_path / "empty.csv"
        result = ci_report.render_csv(summary, str(out))
        assert result == str(out)
        content = out.read_text(encoding="utf-8-sig")
        assert content == "severity,dimension,file,line,summary,failure_scenario,suggested_fix\n"

    def test_creates_parent_directories(self, tmp_path):
        summary = ci_report.ReportSummary.from_entry(
            report_entry(findings=[{"severity": "low", "dimension": "s", "summary": "t"}])
        )
        out = tmp_path / "sub" / "nested" / "report.csv"
        result = ci_report.render_csv(summary, str(out))
        assert result == str(out)
        assert out.exists()

    def test_os_error_returns_error_string(self):
        summary = ci_report.ReportSummary.from_entry(
            report_entry(findings=[{"severity": "low", "dimension": "s", "summary": "t"}])
        )
        result = ci_report.render_csv(summary, "/nonexistent-dir-xyz/abc/def.csv")
        assert result.startswith("error:")

    def test_csv_uses_utf8_sig_bom(self, tmp_path):
        """Verify Excel-compatible UTF-8 BOM encoding."""
        summary = ci_report.ReportSummary.from_entry(
            report_entry(findings=[{"severity": "medium", "dimension": "perf", "summary": "slow", "file": "x.py"}])
        )
        out = tmp_path / "bom.csv"
        ci_report.render_csv(summary, str(out))
        raw = out.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf"
        assert raw[3:].startswith(b"severity,dimension")


class TestIterateReportCliL10NCsv:
    """CLI integration for --lang and --csv."""

    def test_lang_zh_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "low", "file": "a.py", "line": 1, "summary": "s", "dimension": "style"}]),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--lang", "zh"])
        assert result.exit_code == 0
        assert "iterate 报告" in result.output

    def test_config_language_fallback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "iterate.config.yaml").write_text("language: zh\n", encoding="utf-8")
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "low", "file": "a.py", "line": 1, "summary": "s", "dimension": "style"}]),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report"])
        assert result.exit_code == 0
        assert "iterate 报告" in result.output

    def test_lang_overrides_config_language(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "iterate.config.yaml").write_text("language: zh\n", encoding="utf-8")
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "low", "file": "a.py", "line": 1, "summary": "s", "dimension": "style"}]),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--lang", "en"])
        assert result.exit_code == 0
        assert "iterate report (dry-run): 1 finding(s)" in result.output

    def test_csv_flag_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "high", "file": "a.py", "line": 3, "summary": "x", "dimension": "security"}]),
        )
        csv_path = tmp_path / "report.csv"
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--csv", str(csv_path)])
        # severity high → fail_on default (high) → exit code 1
        assert result.exit_code == 1
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8-sig")
        assert "severity,dimension,file,line" in content
        assert "high,security,a.py" in content

    def test_csv_shortcut_dash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        append_entry(
            tmp_path,
            report_entry(findings=[{"severity": "low", "file": "b.py", "line": 5, "summary": "s", "dimension": "style"}]),
        )
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--csv", "-"])
        assert result.exit_code == 0
        csv_path = tmp_path / ".iterate" / "report.csv"
        assert csv_path.exists()
        assert "low,style,b.py" in csv_path.read_text(encoding="utf-8-sig")

    def test_invalid_lang_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli.app, ["iterate", "report", "--lang", "fr"])
        assert result.exit_code == 2  # typer.BadParameter
        assert "must be one of" in result.output
