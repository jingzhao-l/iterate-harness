"""Tests for iterate settings bridge, personalization storage, prompts, worktree flow."""

from __future__ import annotations

from pathlib import Path

from openharness.iterate import prompts
from openharness.iterate.personalization import (
    PersonalizationData,
    known_intentional_of,
    load,
    save,
)
from openharness.iterate.settings import (
    IterateSettings,
    effective_review_rounds,
    project_config,
)
from openharness.iterate.types import KnownIntentional


class TestSettingsBridge:
    def test_defaults_round_trip_through_settings_model(self):
        from openharness.config.settings import Settings

        settings = Settings()
        assert settings.iterate.enabled is True
        assert settings.iterate.max_review_rounds == 3
        dumped = settings.model_dump()
        restored = Settings.model_validate(dumped)
        assert restored.iterate.max_review_rounds == settings.iterate.max_review_rounds

    def test_effective_rounds_is_min_of_kernel_and_project(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text("max_rounds: 99\n")
        kernel = IterateSettings(max_review_rounds=5)
        assert effective_review_rounds(kernel, project_config(tmp_path)) == 5
        (tmp_path / "iterate.config.yaml").write_text("max_rounds: 2\n")
        assert effective_review_rounds(kernel, project_config(tmp_path)) == 2

    def test_effective_rounds_floor_is_one(self):
        kernel = IterateSettings(max_review_rounds=5)
        assert effective_review_rounds(kernel, project_config("/nonexistent")) == 5


class TestPersonalization:
    def test_save_load_round_trip(self, tmp_path):
        data = PersonalizationData(
            known_intentional=[KnownIntentional(file="a.py", dimension="security", reason="ok", line=5)],
            review_focus_areas=["security"],
            code_style_preferences={"quotes": "double"},
            project_quirks="legacy auth shim",
        )
        path = save(tmp_path, tmp_path, data)
        assert path.exists()
        loaded = load(tmp_path, tmp_path)
        assert loaded.known_intentional[0].file == "a.py"
        assert loaded.known_intentional[0].line == 5
        assert loaded.review_focus_areas == ["security"]
        assert loaded.code_style_preferences == {"quotes": "double"}
        assert loaded.project_quirks == "legacy auth shim"

    def test_projects_are_isolated_by_cwd_hash(self, tmp_path):
        other = tmp_path / "other-project"
        other.mkdir()
        save(tmp_path, tmp_path, PersonalizationData(project_quirks="one"))
        save(tmp_path, other, PersonalizationData(project_quirks="two"))
        assert load(tmp_path, tmp_path).project_quirks == "one"
        assert load(tmp_path, other).project_quirks == "two"

    def test_corrupt_file_resets_to_empty(self, tmp_path):
        from openharness.iterate.personalization import storage_dir

        storage_dir(tmp_path, tmp_path)
        (storage_dir(tmp_path, tmp_path) / "personalization.json").write_text("{not json")
        assert load(tmp_path, tmp_path) == PersonalizationData()

    def test_known_intentional_of(self, tmp_path):
        save(
            tmp_path,
            tmp_path,
            PersonalizationData(
                known_intentional=[KnownIntentional(file="x.py", dimension="correctness", reason="r")]
            ),
        )
        entries = known_intentional_of(tmp_path, tmp_path)
        assert len(entries) == 1
        assert entries[0].dimension == "correctness"

    def test_all_nine_categories_survive_roundtrip(self, tmp_path):
        data = PersonalizationData(
            naming_conventions={"modules": "snake_case"},
            preferred_libraries={"http": "httpx"},
            validation_preferences={"strict": "true"},
            risk_tolerances={"security": "low"},
            communication_preferences="concise english",
        )
        save(tmp_path, tmp_path, data)
        loaded = load(tmp_path, tmp_path)
        assert loaded.naming_conventions == {"modules": "snake_case"}
        assert loaded.preferred_libraries == {"http": "httpx"}
        assert loaded.validation_preferences == {"strict": "true"}
        assert loaded.risk_tolerances == {"security": "low"}
        assert loaded.communication_preferences == "concise english"


class TestPrompts:
    def test_skill_prompt_teaches_all_five_tools(self):
        for tool in (
            "iterate_config",
            "iterate_validate",
            "iterate_decision_log",
            "iterate_context",
            "iterate_review",
        ):
            assert tool in prompts.ITERATE_SKILL_PROMPT

    def test_dry_run_kickoff_is_read_only_and_bounded(self):
        text = prompts.dry_run_kickoff("goal-x", 4)
        assert "goal-x" in text
        assert "4" in text
        assert "Do NOT modify any file" in text

    def test_normal_kickoff_mentions_validate_and_rollback(self):
        text = prompts.normal_kickoff("g", 2)
        assert "validate" in text
        assert "roll back" in text

    def test_next_round_and_stop_notices(self):
        nxt = prompts.next_round_instruction(2, 5)
        assert "Round 2" in nxt and "NEW" in nxt
        stop = prompts.convergence_stop_notice("converged", 7)
        assert "converged" in stop and "7" in stop


class TestWorktreeFlow:
    def _git_repo(self, tmp_path: Path) -> Path:
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"}
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, env=env, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, env=env, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, env=env, capture_output=True)
        (repo / "f.txt").write_text("v1")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, env=env, capture_output=True)
        return repo

    def test_enter_commit_exit_merge_flow(self, tmp_path, monkeypatch):
        import asyncio

        repo = self._git_repo(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))  # isolate ~/.openharness worktrees
        from openharness.iterate import worktree_flow
        from openharness.swarm.worktree import WorktreeManager

        manager = WorktreeManager(base_dir=tmp_path / "wts")

        async def scenario() -> None:
            session = await worktree_flow.enter(repo, manager, round_number=1, target_branch="main")
            assert session.branch == "iterate/round-1"
            assert session.worktree_path.exists()
            assert (session.worktree_path / "f.txt").exists()
            (session.worktree_path / "f.txt").write_text("v2")
            sha = await worktree_flow.commit(session, "iterate fix round 1")
            assert sha
            merged = await worktree_flow.exit_session(session, manager, merged=True)
            assert merged is True
            assert (repo / "f.txt").read_text() == "v2"
            assert not session.worktree_path.exists()

        asyncio.run(scenario())

    def test_exit_without_merge_rolls_back(self, tmp_path, monkeypatch):
        import asyncio

        repo = self._git_repo(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        from openharness.iterate import worktree_flow
        from openharness.swarm.worktree import WorktreeManager

        manager = WorktreeManager(base_dir=tmp_path / "wts")

        async def scenario() -> None:
            session = await worktree_flow.enter(repo, manager, round_number=2)
            (session.worktree_path / "f.txt").write_text("bad-change")
            await worktree_flow.commit(session, "will be dropped")
            await worktree_flow.rollback(session, manager)
            assert (repo / "f.txt").read_text() == "v1"
            assert not session.worktree_path.exists()

        asyncio.run(scenario())

    def test_commit_with_no_changes_returns_none(self, tmp_path, monkeypatch):
        import asyncio

        repo = self._git_repo(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        from openharness.iterate import worktree_flow
        from openharness.swarm.worktree import WorktreeManager

        manager = WorktreeManager(base_dir=tmp_path / "wts")

        async def scenario() -> None:
            session = await worktree_flow.enter(repo, manager, round_number=3)
            assert await worktree_flow.commit(session, "empty") is None
            await worktree_flow.exit_session(session, manager, merged=False)

        asyncio.run(scenario())
