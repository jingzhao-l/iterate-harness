"""v1.5 tests: full onboarding (ITERATE.md + fingerprints + drift + refresh)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from iterate_harness.iterate import onboard_cmd, onboarding, prompts

VALID_MD = f"""# ITERATE.md — test

| Onboarding | |
|---|---|
| completed_at | 2026-08-15T00:00:00Z |
| channel | ai |

---

{onboarding.AI_START_MARKER}

## 项目概述

Test project.

{onboarding.AI_END_MARKER}

---

{onboarding.USER_START_MARKER}

## 手动批注 / Manual Notes

keep-me

{onboarding.USER_END_MARKER}
"""


def _write_project(tmp_path: Path, *, with_config: bool = True) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    (tmp_path / "ITERATE.md").write_text(VALID_MD, encoding="utf-8")
    if with_config:
        stored = onboarding.capture_fingerprints(tmp_path)
        config = {
            "goal": "g",
            "dimensions": ["correctness"],
            "onboarding": onboarding.build_onboarding_section(
                channel="ai", fingerprints=stored, completed_at="2026-08-15T00:00:00Z"
            ),
        }
        (tmp_path / "iterate.config.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
        )


# --- fingerprints ---------------------------------------------------------------


class TestFingerprints:
    def test_capture_existing_manifests_sorted(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("name='x'", encoding="utf-8")
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        entries = onboarding.capture_fingerprints(tmp_path)
        assert [e.path for e in entries] == ["package.json", "pyproject.toml"]
        assert all(len(e.sha256) == 64 for e in entries)

    def test_capture_skips_ignored_patterns(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        entries = onboarding.capture_fingerprints(tmp_path, ["package.json"])
        assert entries == []

    def test_capture_empty_root(self, tmp_path: Path):
        assert onboarding.capture_fingerprints(tmp_path) == []

    def test_compare_detects_changed_added_removed(self):
        stored = [
            onboarding.FingerprintEntry("a", "1" * 64),
            onboarding.FingerprintEntry("b", "2" * 64),
        ]
        current = [
            onboarding.FingerprintEntry("a", "1" * 64),
            onboarding.FingerprintEntry("b", "3" * 64),
            onboarding.FingerprintEntry("c", "4" * 64),
        ]
        drift = onboarding.compare_fingerprints(stored, current)
        assert drift.unchanged == ["a"]
        assert drift.changed == ["b"]
        assert drift.added == ["c"]
        assert drift.removed == []
        assert drift.has_drift

    def test_compare_reports_removed(self):
        stored = [onboarding.FingerprintEntry("gone", "1" * 64)]
        drift = onboarding.compare_fingerprints(stored, [])
        assert drift.removed == ["gone"]
        assert drift.has_drift

    def test_check_drift_filters_both_sides(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        stored = onboarding.capture_fingerprints(tmp_path)
        drift = onboarding.check_drift(tmp_path, stored, ["package.json"])
        assert not drift.has_drift

    def test_fingerprints_roundtrip_and_bad_rows(self):
        entries = [onboarding.FingerprintEntry("a", "1" * 64)]
        assert onboarding.fingerprints_from_dict(onboarding.fingerprints_to_dict(entries)) == entries
        assert onboarding.fingerprints_from_dict("nope") == []
        assert onboarding.fingerprints_from_dict([{"path": "a"}, {"sha256": "x"}, 5]) == []


# --- ITERATE.md region operations -----------------------------------------------


class TestRegionOperations:
    def test_validate_accepts_wellformed_md(self, tmp_path: Path):
        path = tmp_path / "ITERATE.md"
        path.write_text(VALID_MD, encoding="utf-8")
        assert onboarding.validate_iterate_md(path) == []

    def test_validate_missing_file(self, tmp_path: Path):
        errors = onboarding.validate_iterate_md(tmp_path / "ITERATE.md")
        assert errors and "not created" in errors[0]

    def test_validate_missing_markers(self, tmp_path: Path):
        path = tmp_path / "ITERATE.md"
        path.write_text("no markers here", encoding="utf-8")
        errors = onboarding.validate_iterate_md(path)
        assert len(errors) == 4

    def test_validate_user_region_before_ai_region(self, tmp_path: Path):
        path = tmp_path / "ITERATE.md"
        path.write_text(
            f"{onboarding.USER_START_MARKER}\n{onboarding.USER_END_MARKER}\n"
            f"{onboarding.AI_START_MARKER}\n{onboarding.AI_END_MARKER}\n",
            encoding="utf-8",
        )
        errors = onboarding.validate_iterate_md(path)
        assert any("after" in e for e in errors)

    def test_extract_user_owned_section_verbatim(self):
        section = onboarding.extract_user_owned_section(VALID_MD)
        assert "keep-me" in section
        assert section.startswith(onboarding.USER_START_MARKER)
        assert section.endswith(onboarding.USER_END_MARKER)

    def test_extract_falls_back_to_default_when_markers_missing(self):
        section = onboarding.extract_user_owned_section("corrupt")
        assert section.startswith(onboarding.USER_START_MARKER)

    def test_replace_user_owned_section(self):
        fresh = VALID_MD.replace("keep-me", "fresh-default")
        preserved = onboarding.extract_user_owned_section(VALID_MD)
        result = onboarding.replace_user_owned_section(fresh, preserved)
        assert "keep-me" in result
        assert "fresh-default" not in result

    def test_update_completed_at_row(self):
        updated = onboarding.update_completed_at_in_md(VALID_MD, "2026-09-01T00:00:00Z")
        assert "| completed_at | 2026-09-01T00:00:00Z |" in updated
        assert onboarding.update_completed_at_in_md("no table", "x") == "no table"


# --- config section / drift ------------------------------------------------------


class TestConfigOnboardingSection:
    def test_build_onboarding_section_shape(self):
        section = onboarding.build_onboarding_section(
            channel="ai", fingerprints=[], completed_at="2026-08-15T00:00:00Z"
        )
        assert section["version"] == onboarding.ONBOARDING_VERSION
        assert section["channel"] == "ai"
        assert section["drift_check"] is True
        assert section["fingerprints"] == []

    def test_load_stored_fingerprints_from_config(self, tmp_path: Path):
        _write_project(tmp_path)
        stored = onboarding.load_stored_fingerprints(tmp_path)
        assert {e.path for e in stored} == {"package.json", "pyproject.toml"}

    def test_load_stored_fingerprints_no_config(self, tmp_path: Path):
        assert onboarding.load_stored_fingerprints(tmp_path) == []

    def test_drift_check_enabled_default_true(self, tmp_path: Path):
        _write_project(tmp_path)
        assert onboarding.drift_check_enabled(tmp_path) is True

    def test_drift_check_disabled(self, tmp_path: Path):
        _write_project(tmp_path)
        config_path = tmp_path / "iterate.config.yaml"
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["onboarding"]["drift_check"] = False
        config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        assert onboarding.drift_check_enabled(tmp_path) is False
        assert onboarding.check_onboarding_drift(tmp_path) is None

    def test_check_onboarding_drift_detects_change(self, tmp_path: Path):
        _write_project(tmp_path)
        (tmp_path / "package.json").write_text('{"name":"changed"}', encoding="utf-8")
        drift = onboarding.check_onboarding_drift(tmp_path)
        assert drift is not None and drift.changed == ["package.json"]

    def test_check_onboarding_drift_none_without_md(self, tmp_path: Path):
        assert onboarding.check_onboarding_drift(tmp_path) is None


# --- kickoff ----------------------------------------------------------------------


class TestOnboardingKickoff:
    def test_kickoff_contains_markers_evidence_and_sensitive_list(self):
        kickoff = prompts.onboarding_kickoff(
            project_root="/proj",
            goal="improve",
            dimensions=["correctness"],
            evidence_lines=["pyproject.toml found"],
            channel="ai",
            completed_at="2026-08-15T00:00:00Z",
        )
        assert onboarding.AI_START_MARKER in kickoff
        assert onboarding.USER_START_MARKER in kickoff
        assert "pyproject.toml found" in kickoff
        assert ".env" in kickoff
        assert "/proj/ITERATE.md" in kickoff
        assert "RE-ONBOARD" not in kickoff

    def test_kickoff_preserve_clause_embeds_user_section(self):
        section = onboarding.default_user_owned_section()
        kickoff = prompts.onboarding_kickoff(
            project_root="/proj",
            goal="g",
            dimensions=["correctness"],
            evidence_lines=[],
            channel="ai",
            completed_at="2026-08-15T00:00:00Z",
            preserve_user_section=section,
        )
        assert "RE-ONBOARD MODE" in kickoff
        assert section in kickoff

    def test_template_loads_from_bundled_data(self):
        template = prompts.load_onboarding_template()
        assert onboarding.AI_START_MARKER in template
        assert "{{PROJECT_OVERVIEW}}" in template


# --- detection rendering + onboard e2e ---------------------------------------------


class TestDetectionRenderAndOnboard:
    def test_render_detection_md_fills_all_placeholders(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")
        from iterate_harness.iterate.init_wizard import ProjectProfile

        profile = ProjectProfile(languages=["python"], test_command="pytest -q")
        text = onboard_cmd.render_detection_iterate_md(
            profile=profile,
            goal="g",
            dimensions=["correctness"],
            channel="cli",
            completed_at="2026-08-15T00:00:00Z",
            project_root=str(tmp_path),
        )
        assert "{{" not in text
        assert onboarding.validate_iterate_md(tmp_path / "ITERATE.md") == [] or True
        path = tmp_path / "ITERATE.md"
        path.write_text(text, encoding="utf-8")
        assert onboarding.validate_iterate_md(path) == []

    def test_run_onboard_no_ai_writes_both_files(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        code = onboard_cmd.run_onboard(yes=True, no_ai=True)
        assert code == 0
        md = tmp_path / "ITERATE.md"
        assert md.exists()
        assert onboarding.validate_iterate_md(md) == []
        raw = yaml.safe_load((tmp_path / "iterate.config.yaml").read_text(encoding="utf-8"))
        assert raw["onboarding"]["channel"] == "cli"
        assert raw["onboarding"]["drift_check"] is True
        assert len(raw["onboarding"]["fingerprints"]) == 1

    def test_run_onboard_refuses_existing_md(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ITERATE.md").write_text(VALID_MD, encoding="utf-8")
        assert onboard_cmd.run_onboard(yes=True, no_ai=True) == 1

    def test_run_onboard_ai_path_requires_auth(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")
        monkeypatch.setattr(onboard_cmd, "check_auth_configured", lambda: "no auth")
        code = onboard_cmd.run_onboard(yes=True)
        assert code == 1
        assert not (tmp_path / "ITERATE.md").exists()

    def test_run_onboard_ai_path_validates_model_output(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")

        class _FakeRun:
            async def __call__(self, *, prompt, permission_mode):
                (tmp_path / "ITERATE.md").write_text("garbage without markers", encoding="utf-8")

        import iterate_harness.ui.app as app_mod

        monkeypatch.setattr(app_mod, "run_print_mode", _FakeRun())
        code = onboard_cmd.run_onboard(yes=True)
        assert code == 1
        assert not (tmp_path / "iterate.config.yaml").exists()


# --- refresh / reonboard ------------------------------------------------------------


class TestRefreshReonboard:
    def test_refresh_updates_fingerprints_and_metadata(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_project(tmp_path)
        (tmp_path / "package.json").write_text('{"name":"drifted"}', encoding="utf-8")
        old_md = (tmp_path / "ITERATE.md").read_text(encoding="utf-8")
        assert onboard_cmd.run_refresh() == 0
        raw = yaml.safe_load((tmp_path / "iterate.config.yaml").read_text(encoding="utf-8"))
        assert raw["onboarding"]["completed_at"] != "2026-08-15T00:00:00Z"
        assert len(raw["onboarding"]["fingerprints"]) == 2
        new_md = (tmp_path / "ITERATE.md").read_text(encoding="utf-8")
        assert "keep-me" in new_md
        assert new_md != old_md or "completed_at | 2026-08-15T00:00:00Z" not in new_md

    def test_refresh_requires_onboarding(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert onboard_cmd.run_refresh() == 1

    def test_reonboard_preserves_user_region_with_backups(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_project(tmp_path)
        (tmp_path / "package.json").unlink()
        code = onboard_cmd.run_reonboard(yes=True, no_ai=True)
        assert code == 0
        new_md = (tmp_path / "ITERATE.md").read_text(encoding="utf-8")
        assert "keep-me" in new_md
        backups = list(tmp_path.glob("ITERATE.md.bak-*"))
        assert len(backups) == 1
        raw = yaml.safe_load((tmp_path / "iterate.config.yaml").read_text(encoding="utf-8"))
        assert raw["onboarding"]["channel"] == "cli"

    def test_reonboard_restores_backup_on_failure(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_project(tmp_path)
        monkeypatch.setattr(
            onboard_cmd, "run_onboard", lambda **_kwargs: 1
        )
        assert onboard_cmd.run_reonboard(yes=True, no_ai=True) == 1
        restored = (tmp_path / "ITERATE.md").read_text(encoding="utf-8")
        assert restored == VALID_MD

    def test_reonboard_preserves_existing_config_sections(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_project(tmp_path)
        config_path = tmp_path / "iterate.config.yaml"
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["personalization"] = {"code_style_preferences": {"indent": "2"}}
        raw["review"] = {"focus": ["security"]}
        config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        (tmp_path / "package.json").unlink()
        assert onboard_cmd.run_reonboard(yes=True, no_ai=True) == 0
        new_raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert new_raw["personalization"]["code_style_preferences"]["indent"] == "2"
        assert new_raw["review"]["focus"] == ["security"]

    def test_reonboard_requires_onboarding(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert onboard_cmd.run_reonboard(yes=True) == 1


# --- status lines / drift warning ----------------------------------------------------


class TestStatusAndWarning:
    def test_status_lines_not_onboarded(self, tmp_path: Path):
        lines = onboard_cmd.render_status_onboarding_lines(tmp_path)
        assert lines == ["Onboarding: not onboarded (`ih iterate onboard`)"]

    def test_status_lines_onboarded_with_drift(self, tmp_path: Path, capsys):
        _write_project(tmp_path)
        (tmp_path / "package.json").write_text('{"name":"drift"}', encoding="utf-8")
        lines = onboard_cmd.render_status_onboarding_lines(tmp_path)
        joined = "\n".join(lines)
        assert "channel=ai" in joined
        assert "DRIFTED" in joined

    def test_warn_if_drifted_prints_once(self, tmp_path: Path, capsys):
        _write_project(tmp_path)
        (tmp_path / "pyproject.toml").write_text("changed", encoding="utf-8")
        onboard_cmd.warn_if_drifted(tmp_path)
        out = capsys.readouterr().out
        assert "warning" in out

    def test_warn_if_drifted_silent_when_clean(self, tmp_path: Path, capsys):
        _write_project(tmp_path)
        onboard_cmd.warn_if_drifted(tmp_path)
        assert capsys.readouterr().out == ""


# --- system prompt injection -----------------------------------------------------------


class TestSystemPromptInjection:
    def test_section_injected_when_iterate_md_present(self, tmp_path: Path):
        (tmp_path / "ITERATE.md").write_text("# knowledge base\nrules here", encoding="utf-8")
        from iterate_harness.prompts.context import _build_iterate_project_section

        section = _build_iterate_project_section(tmp_path)
        assert "Iterate Project Knowledge" in section
        assert "rules here" in section

    def test_section_empty_without_file(self, tmp_path: Path):
        from iterate_harness.prompts.context import _build_iterate_project_section

        assert _build_iterate_project_section(tmp_path) == ""

    def test_section_truncates_long_content(self, tmp_path: Path):
        (tmp_path / "ITERATE.md").write_text("x" * 10_000, encoding="utf-8")
        from iterate_harness.prompts.context import _build_iterate_project_section

        section = _build_iterate_project_section(tmp_path)
        assert "truncated" in section
        assert len(section) < 10_000

    def test_section_walks_up_to_project_root(self, tmp_path: Path):
        (tmp_path / "ITERATE.md").write_text("root kb", encoding="utf-8")
        nested = tmp_path / "src" / "deep"
        nested.mkdir(parents=True)
        from iterate_harness.prompts.context import _build_iterate_project_section

        assert "root kb" in _build_iterate_project_section(nested)


# --- TUI /iterate onboard ------------------------------------------------------------


@pytest.mark.asyncio
async def test_tui_onboard_submits_kickoff(tmp_path: Path, monkeypatch):
    from iterate_harness.commands.iterate import iterate_command_handler
    from tests.test_commands.test_registry import _make_context

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")
    context = _make_context(tmp_path)
    result = await iterate_command_handler("onboard", context)
    assert result.submit_prompt is not None
    assert onboarding.AI_START_MARKER in result.submit_prompt


@pytest.mark.asyncio
async def test_tui_onboard_refuses_existing(tmp_path: Path, monkeypatch):
    from iterate_harness.commands.iterate import iterate_command_handler
    from tests.test_commands.test_registry import _make_context

    monkeypatch.chdir(tmp_path)
    (tmp_path / "ITERATE.md").write_text(VALID_MD, encoding="utf-8")
    context = _make_context(tmp_path)
    result = await iterate_command_handler("onboard", context)
    assert result.submit_prompt is None
    assert "reonboard" in result.message


@pytest.mark.asyncio
async def test_tui_status_includes_onboarding(tmp_path: Path, monkeypatch):
    from iterate_harness.commands.iterate import iterate_command_handler
    from tests.test_commands.test_registry import _make_context

    _write_project(tmp_path)
    context = _make_context(tmp_path)
    result = await iterate_command_handler("status", context)
    assert "Onboarding: onboarded" in result.message
