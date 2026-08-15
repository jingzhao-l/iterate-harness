"""Tests for the skill↔harness dimension-system consistency doctor."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.commands.iterate import iterate_command_handler
from iterate_harness.commands.registry import CommandContext
from iterate_harness.iterate import dimension_check

CANONICAL_ORDER = [
    "correctness",
    "security",
    "performance",
    "architecture",
    "style-tests",
    "tech-debt",
    "spec-compliance",
    "frontend-backend",
    "ui-ux",
]


def make_context(cwd) -> CommandContext:
    return CommandContext(engine=None, cwd=str(cwd))  # type: ignore[arg-type]


# ---- canonical loading -----------------------------------------------------


class TestCanonicalLoading:
    def test_loads_nine_dimensions_in_order(self):
        definitions, errors = dimension_check.load_canonical_dimensions()
        assert errors == []
        assert list(definitions) == CANONICAL_ORDER

    def test_definitions_carry_required_fields(self):
        definitions, _ = dimension_check.load_canonical_dimensions()
        correctness = definitions["correctness"]
        assert correctness.name == "正确性"
        assert correctness.name_en == "Correctness"
        assert correctness.priority == "critical"
        assert "Crash risks" in correctness.focus

    def test_bundled_file_exists_and_is_packaged(self):
        assert dimension_check.DIMENSIONS_DATA_PATH.is_file()


# ---- internal + clean project ---------------------------------------------


class TestDoctorHealthy:
    def test_clean_project_ok(self, tmp_path):
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert report.ok
        assert report.canonical_order == CANONICAL_ORDER
        assert report.config_source == "defaults"

    def test_render_healthy_report(self, tmp_path):
        text = dimension_check.render_doctor_report(
            dimension_check.run_dimension_doctor(tmp_path)
        )
        assert text.startswith("iterate dimension doctor")
        assert "canonical: 9 dimensions" in text
        assert "internal ALL_DIMENSIONS matches canonical (9, same order)" in text
        assert "default config dimensions match canonical (9)" in text
        assert "verdict: OK" in text

    def test_full_config_project_ok(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            """
goal: test
dimensions: [correctness, security]
dimension_resources:
  security:
    model: claude-opus-4
thresholds:
  dimensions:
    correctness:
      max_critical: 0
""",
            encoding="utf-8",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert report.ok
        assert report.config_source == "override"
        assert report.enabled_dimensions == ["correctness", "security"]

    def test_canonical_not_enabled_is_info_not_error(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            "goal: test\ndimensions: [security]",
            encoding="utf-8",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert report.ok
        assert any("not enabled (informational)" in w for w in report.warnings)


# ---- dangling references ---------------------------------------------------


class TestDoctorDrift:
    def test_unknown_enabled_dimension(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            "goal: test\ndimensions: [correctness, securty]",  # typo
            encoding="utf-8",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert not report.ok
        assert any("'securty'" in e for e in report.errors)

    def test_unknown_dimension_resources_key(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            """
goal: test
dimensions: [security]
dimension_resources:
  performace:
    model: claude-haiku
""",
            encoding="utf-8",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert not report.ok
        assert any("dimension_resources: unknown dimension(s) ['performace']" in e for e in report.errors)

    def test_unknown_thresholds_dimension_key(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            """
goal: test
dimensions: [security]
thresholds:
  dimensions:
    style-test:
      max_critical: 1
""",
            encoding="utf-8",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert not report.ok
        assert any("thresholds.dimensions: unknown dimension(s) ['style-test']" in e for e in report.errors)

    def test_inert_reference_is_warning_not_error(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            """
goal: test
dimensions: [security]
dimension_resources:
  ui-ux:
    model: claude-haiku
""",
            encoding="utf-8",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert report.ok
        assert any("inert" in w for w in report.warnings)

    def test_render_failure_report(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            "goal: test\ndimensions: [securty]",
            encoding="utf-8",
        )
        text = dimension_check.render_doctor_report(
            dimension_check.run_dimension_doctor(tmp_path)
        )
        assert "✗" in text
        assert "verdict: FAIL — 1 error(s)" in text


class TestDoctorPersonalization:
    def _write(self, tmp_path, personalization_yaml: str) -> None:
        (tmp_path / "iterate.config.yaml").write_text(
            f"goal: test\ndimensions: [security]\n{personalization_yaml}",
            encoding="utf-8",
        )

    def test_fix_priority_order_outside_enabled(self, tmp_path):
        self._write(
            tmp_path,
            "personalization:\n  fix_priority_order: [security, ui-ux]\n",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert not report.ok
        assert any("fix_priority_order[1]='ui-ux'" in e for e in report.errors)

    def test_dimension_focus_outside_enabled(self, tmp_path):
        self._write(
            tmp_path,
            "personalization:\n  dimension_focus:\n    - dimension: performance\n      focus: extra\n",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert not report.ok
        assert any("dimension_focus[0]='performance'" in e for e in report.errors)

    def test_known_intentional_outside_enabled(self, tmp_path):
        self._write(
            tmp_path,
            "personalization:\n  known_intentional:\n    - file: a.py\n      dimension: tech-debt\n      reason: ok\n",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert not report.ok
        assert any("known_intentional[0]='tech-debt'" in e for e in report.errors)

    def test_resolving_personalization_ok(self, tmp_path):
        self._write(
            tmp_path,
            "personalization:\n  fix_priority_order: [security]\n  dimension_focus:\n    - dimension: security\n      focus: extra\n",
        )
        report = dimension_check.run_dimension_doctor(tmp_path)
        assert report.ok
        assert any("all resolve" in e for e in [c.text for c in report.checks if c.status])


# ---- TUI / CLI endpoints ---------------------------------------------------


class TestDoctorEndpoints:
    @pytest.mark.asyncio
    async def test_slash_doctor_renders_report(self, tmp_path):
        result = await iterate_command_handler("doctor", make_context(tmp_path))
        assert "iterate dimension doctor" in (result.message or "")
        assert "verdict: OK" in (result.message or "")

    @pytest.mark.asyncio
    async def test_slash_doctor_reports_drift(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            "goal: test\ndimensions: [securty]", encoding="utf-8"
        )
        result = await iterate_command_handler("doctor", make_context(tmp_path))
        assert "verdict: FAIL" in (result.message or "")

    def test_cli_doctor_exit_zero_on_healthy(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli.app, ["iterate", "doctor"])
        assert result.exit_code == 0
        assert "verdict: OK" in result.output

    def test_cli_doctor_exit_one_on_drift(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "iterate.config.yaml").write_text(
            "goal: test\ndimensions: [correctness, securty]", encoding="utf-8"
        )
        result = CliRunner().invoke(cli.app, ["iterate", "doctor"])
        assert result.exit_code == 1
        assert "verdict: FAIL" in result.output
