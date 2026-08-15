"""Tests for the changed-only quick review (git_scope + prompt/plan wiring)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from iterate_harness.commands.iterate import _parse_changed_flags, iterate_command_handler
from iterate_harness.commands.registry import CommandContext
from iterate_harness.iterate import git_scope, prompts, review
from iterate_harness.iterate.config_loader import default_config
from iterate_harness.iterate.types import IterateConfig
from iterate_harness.tools.base import ToolExecutionContext
from iterate_harness.tools.iterate_tools import IterateReviewInput, IterateReviewTool


def make_command_context(cwd: Path) -> CommandContext:
    return CommandContext(engine=None, cwd=str(cwd))  # type: ignore[arg-type]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A committed git repo with a clean tree (one tracked file)."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "base.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


class TestValidateRef:
    def test_accepts_common_refs(self):
        assert git_scope.validate_ref("HEAD") == "HEAD"
        assert git_scope.validate_ref("main") == "main"
        assert git_scope.validate_ref("origin/release-1.2") == "origin/release-1.2"
        assert git_scope.validate_ref("HEAD~3") == "HEAD~3"

    def test_rejects_option_like_or_shell_tokens(self):
        for bad in ("", "  ", "--exec=x", "-b", "a;rm", "a b", "a|b"):
            with pytest.raises(ValueError):
                git_scope.validate_ref(bad)


class TestCollectChangedFiles:
    def test_outside_git_repo_is_empty(self, tmp_path):
        assert git_scope.collect_changed_files(tmp_path) == []

    def test_clean_tree_is_empty(self, git_repo):
        assert git_scope.collect_changed_files(git_repo) == []

    def test_collects_diff_untracked_and_rename(self, git_repo):
        (git_repo / "base.py").write_text("x = 2\n", encoding="utf-8")  # modified
        (git_repo / "new.py").write_text("y = 1\n", encoding="utf-8")  # untracked
        git(git_repo, "mv", "base.py", "renamed.py")  # staged rename
        files = git_scope.collect_changed_files(git_repo)
        assert "renamed.py" in files
        assert "base.py" not in files  # rename contributes only the new path
        assert "new.py" in files

    def test_cap_limits_result_size(self, git_repo, monkeypatch):
        monkeypatch.setattr(git_scope, "MAX_CHANGED_FILES", 2)
        for index in range(5):
            (git_repo / f"f{index}.py").write_text("z\n", encoding="utf-8")
        assert len(git_scope.collect_changed_files(git_repo)) == 2

    def test_nonexistent_ref_fails_softly_to_status(self, git_repo):
        (git_repo / "extra.py").write_text("z\n", encoding="utf-8")
        files = git_scope.collect_changed_files(git_repo, "no-such-ref")
        assert files == ["extra.py"]  # diff fails -> status fallback still works


class TestReviewPlanChangedScope:
    def test_plan_pins_changed_files(self):
        config: IterateConfig = default_config()
        config.dimensions = ["security"]
        plan = review.build_review_plan(
            config=config,
            mode="dry-run",
            max_review_rounds=2,
            changed_files=["src/a.py", "src/b.py"],
        )
        assert plan.scope == "changed-only"
        assert "src/a.py" in plan.dimensions[0].reviewer_prompt
        assert "review ONLY these files" in plan.dimensions[0].reviewer_prompt

    def test_plan_without_files_keeps_config_scope(self):
        config = default_config()
        config.dimensions = ["security"]
        config.review.scope = "full"
        plan = review.build_review_plan(
            config=config, mode="dry-run", max_review_rounds=2
        )
        assert plan.scope == "full"
        assert "review ONLY these files" not in plan.dimensions[0].reviewer_prompt

    def test_blank_entries_are_dropped(self):
        config = default_config()
        config.dimensions = ["security"]
        plan = review.build_review_plan(
            config=config,
            mode="dry-run",
            max_review_rounds=2,
            changed_files=["  ", "src/real.py"],
        )
        assert plan.scope == "changed-only"
        assert "src/real.py" in plan.dimensions[0].reviewer_prompt


class TestKickoffPrompts:
    def test_dry_run_kickoff_embeds_delta(self):
        kickoff = prompts.dry_run_kickoff("g", 3, changed_files=["src/a.py"])
        assert "CHANGED-ONLY" in kickoff
        assert 'changed_files=["src/a.py"]' in kickoff
        assert "- src/a.py" in kickoff
        assert "Do NOT modify any file" in kickoff

    def test_normal_kickoff_embeds_delta(self):
        kickoff = prompts.normal_kickoff("g", 3, changed_files=["src/a.py", "src/b.py"])
        assert "CHANGED-ONLY" in kickoff
        assert "- src/b.py" in kickoff

    def test_kickoff_without_delta_unchanged(self):
        assert "CHANGED-ONLY" not in prompts.dry_run_kickoff("g", 3)
        assert "CHANGED-ONLY" not in prompts.normal_kickoff("g", 3)


class TestReviewToolPlan:
    async def test_plan_operation_accepts_changed_files(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            "dimensions:\n  - security\n", encoding="utf-8"
        )
        result = await IterateReviewTool().execute(
            IterateReviewInput(
                operation="plan",
                mode="dry-run",
                max_review_rounds=2,
                changed_files=["src/a.py"],
            ),
            ToolExecutionContext(cwd=tmp_path),
        )
        plan = json.loads(result.output)["plan"]
        assert plan["scope"] == "changed-only"
        assert "src/a.py" in plan["dimensions"][0]["reviewerPrompt"]


class TestSlashCommandChanged:
    async def test_review_changed_submits_scoped_kickoff(self, git_repo):
        (git_repo / "delta.py").write_text("d = 1\n", encoding="utf-8")
        result = await iterate_command_handler(
            "review --changed", make_command_context(git_repo)
        )
        assert result.submit_prompt is not None
        assert "CHANGED-ONLY" in result.submit_prompt
        assert "- delta.py" in result.submit_prompt
        assert "changed-only, 1 file(s)" in (result.message or "")

    async def test_review_changed_with_ref_token(self, git_repo):
        (git_repo / "delta.py").write_text("d = 1\n", encoding="utf-8")
        git(git_repo, "add", "-A")
        git(git_repo, "commit", "-q", "-m", "delta")
        result = await iterate_command_handler(
            "review --changed --ref HEAD~1", make_command_context(git_repo)
        )
        assert result.submit_prompt is not None
        assert "- delta.py" in result.submit_prompt

    async def test_review_changed_clean_tree_is_friendly(self, git_repo):
        result = await iterate_command_handler(
            "review --changed", make_command_context(git_repo)
        )
        assert result.submit_prompt is None
        assert "No changed files detected" in (result.message or "")

    async def test_review_changed_invalid_ref_rejected(self, git_repo):
        result = await iterate_command_handler(
            "review --changed --ref --exec=bad", make_command_context(git_repo)
        )
        assert result.submit_prompt is None
        assert "Rejected" in (result.message or "")

    async def test_run_changed_submits_scoped_kickoff(self, git_repo):
        (git_repo / "delta.py").write_text("d = 1\n", encoding="utf-8")
        result = await iterate_command_handler(
            "run --changed", make_command_context(git_repo)
        )
        assert result.submit_prompt is not None
        assert "CHANGED-ONLY" in result.submit_prompt

    async def test_review_without_flag_stays_full(self, tmp_path):
        result = await iterate_command_handler("review", make_command_context(tmp_path))
        assert result.submit_prompt is not None
        assert "CHANGED-ONLY" not in result.submit_prompt


class TestParseChangedFlags:
    def test_defaults(self):
        assert _parse_changed_flags([]) == (False, "HEAD")

    def test_changed_with_ref(self):
        assert _parse_changed_flags(["--changed", "--ref", "main"]) == (True, "main")

    def test_ref_without_value_falls_back(self):
        assert _parse_changed_flags(["--ref"]) == (False, "HEAD")
