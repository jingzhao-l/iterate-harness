"""Tests for the iterate kickoff template presets (#10)."""

from __future__ import annotations

from iterate_harness.iterate import TEMPLATE_PRESETS, list_templates, prompts


class TestTemplatePresets:
    def test_presets_contain_all_three_templates(self):
        names = set(TEMPLATE_PRESETS)
        assert {"standard", "strict", "quick"} <= names

    def test_each_preset_carries_suffixes_and_description(self):
        for name, data in TEMPLATE_PRESETS.items():
            assert set(data) == {"dry_run_suffix", "normal_suffix", "description"}
            assert isinstance(data["dry_run_suffix"], str)
            assert isinstance(data["normal_suffix"], str)
            assert data["description"]

    def test_standard_preset_has_empty_suffixes(self):
        standard = TEMPLATE_PRESETS["standard"]
        assert standard["dry_run_suffix"] == ""
        assert standard["normal_suffix"] == ""

    def test_list_templates_includes_all_presets(self):
        templates = list_templates()
        names = [entry["name"] for entry in templates]
        assert "standard" in names
        assert "strict" in names
        assert "quick" in names
        # Each entry is a plain name/description dict usable by menus/CLI.
        for entry in templates:
            assert set(entry) == {"name", "description"}
            assert entry["name"] and entry["description"]


class TestKickoffTemplates:
    def test_default_template_is_standard_and_unchanged(self):
        baseline = prompts.dry_run_kickoff("goal-x", 4)
        # The default (template="standard") must equal the old behavior:
        # no preset suffix is appended.
        assert baseline == prompts.dry_run_kickoff("goal-x", 4, template="standard")
        assert baseline.endswith("Do NOT modify any file.")
        assert "Be thorough" not in baseline
        assert "Be conservative" not in baseline
        assert "Focus on the most impactful" not in baseline

    def test_normal_default_template_is_unchanged(self):
        baseline = prompts.normal_kickoff("g", 2)
        assert baseline == prompts.normal_kickoff("g", 2, template="standard")
        assert "Fix the most impactful findings first" not in baseline

    def test_strict_dry_run_appends_thoroughness(self):
        text = prompts.dry_run_kickoff("g", 3, template="strict")
        assert "Be thorough" in text
        assert "failure scenario" in text
        assert "Prefer false positives over missed issues" in text

    def test_strict_normal_appends_conservatism(self):
        text = prompts.normal_kickoff("g", 3, template="strict")
        assert "Be conservative" in text
        assert "regression" in text

    def test_quick_dry_run_appends_focus(self):
        text = prompts.dry_run_kickoff("g", 3, template="quick")
        assert "Focus on the most impactful findings only" in text
        assert "Respond quickly" in text

    def test_quick_normal_appends_fast_pass(self):
        text = prompts.normal_kickoff("g", 3, template="quick")
        assert "Fix the most impactful findings first" in text
        assert "fast pass" in text

    def test_unknown_template_degrades_to_standard(self):
        text = prompts.dry_run_kickoff("g", 3, template="does-not-exist")
        assert text == prompts.dry_run_kickoff("g", 3, template="standard")

    def test_kickoff_keeps_other_arguments(self):
        text = prompts.dry_run_kickoff(
            "g", 3, changed_files=["src/a.py"], cwd=None, template="quick"
        )
        assert "CHANGED-ONLY" in text and "src/a.py" in text

    def test_resume_kickoff_dry_run_mode_uses_dry_run_suffix(self):
        summary = {
            "mode": "dry-run",
            "verdict": "converged",
            "rounds": 2,
            "totalFindings": 1,
            "preview": [],
        }
        text = prompts.resume_kickoff("g", 3, summary, template="strict")
        assert "Be thorough" in text

    def test_resume_kickoff_normal_mode_uses_normal_suffix(self):
        summary = {
            "mode": "normal",
            "verdict": "done",
            "rounds": 2,
            "totalFindings": 0,
            "preview": [],
        }
        text = prompts.resume_kickoff("g", 3, summary, template="strict")
        assert "Be conservative" in text

    def test_resume_kickoff_standard_is_unchanged(self):
        summary = {
            "mode": "dry-run",
            "verdict": "converged",
            "rounds": 1,
            "totalFindings": 0,
            "preview": [],
        }
        text = prompts.resume_kickoff("g", 3, summary)
        assert "Be thorough" not in text
        assert "Be conservative" not in text
