"""Tests for the managed pre-commit hook (`oh iterate hook`, v1.2-b)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from openharness.iterate import git_hook

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "HOME": "/tmp",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


@pytest.fixture(autouse=True)
def _ensure_git_on_path(monkeypatch):
    """Sandboxed test runners may lack git/sh on PATH; inject standard bins."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/local/bin")


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, env=_GIT_ENV, capture_output=True)
    return repo


class TestRenderHookScript:
    def test_script_contains_guard_binary_and_gate(self):
        script = git_hook.render_hook_script(oh_binary="/usr/local/bin/oh", fail_on="high")
        assert git_hook.HOOK_MARKER in script
        assert 'OH="/usr/local/bin/oh"' in script
        assert "ITERATE_SKIP_HOOK" in script
        assert "iterate review --changed" in script
        assert f"--rounds {git_hook.HOOK_ROUNDS}" in script
        assert "iterate report --fail-on high" in script

    def test_script_is_valid_posix_sh(self):
        script = git_hook.render_hook_script(oh_binary="oh", fail_on="critical")
        result = subprocess.run(
            ["sh", "-n"], input=script, text=True, capture_output=True, check=False
        )
        assert result.returncode == 0, result.stderr

    def test_hook_rounds_is_single_round(self):
        assert git_hook.HOOK_ROUNDS == 1  # keeps commits fast


class TestInstallHook:
    def test_install_writes_managed_executable_hook(self, git_repo):
        target = git_hook.install_hook(git_repo, fail_on="medium")
        assert target == git_repo / ".git" / "hooks" / "pre-commit"
        assert target.exists()
        assert git_hook.HOOK_MARKER in target.read_text(encoding="utf-8")
        assert target.stat().st_mode & 0o111  # executable

    def test_install_replaces_own_managed_hook(self, git_repo):
        git_hook.install_hook(git_repo, fail_on="low")
        target = git_hook.install_hook(git_repo, fail_on="critical")
        assert "fail-on critical" in target.read_text(encoding="utf-8")

    def test_install_refuses_foreign_hook(self, git_repo):
        target = git_repo / ".git" / "hooks" / "pre-commit"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\nhusky\n", encoding="utf-8")
        with pytest.raises(git_hook.HookError, match="not managed by iterate"):
            git_hook.install_hook(git_repo)

    def test_install_rejects_unknown_severity(self, git_repo):
        with pytest.raises(git_hook.HookError, match="fail_on"):
            git_hook.install_hook(git_repo, fail_on="extreme")

    def test_install_outside_git_repo_is_refused(self, tmp_path):
        with pytest.raises(git_hook.HookError, match="not a git repo"):
            git_hook.install_hook(tmp_path / "nowhere")


class TestUninstallHook:
    def test_uninstall_removes_managed_hook(self, git_repo):
        git_hook.install_hook(git_repo)
        assert git_hook.uninstall_hook(git_repo) is True
        assert not (git_repo / ".git" / "hooks" / "pre-commit").exists()

    def test_uninstall_absent_hook_returns_false(self, git_repo):
        assert git_hook.uninstall_hook(git_repo) is False

    def test_uninstall_refuses_foreign_hook(self, git_repo):
        target = git_repo / ".git" / "hooks" / "pre-commit"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\nhusky\n", encoding="utf-8")
        with pytest.raises(git_hook.HookError, match="not managed by iterate"):
            git_hook.uninstall_hook(git_repo)


class TestHookStatus:
    def test_status_when_installed(self, git_repo):
        git_hook.install_hook(git_repo, fail_on="low")
        status = git_hook.hook_status(git_repo)
        assert status["installed"] is True
        assert status["managed"] is True
        assert status["path"].endswith("pre-commit")
        assert "ITERATE_SKIP_HOOK" in status["skippable"]

    def test_status_when_absent(self, git_repo):
        status = git_hook.hook_status(git_repo)
        assert status["installed"] is False
        assert "error" not in status

    def test_status_outside_git_repo_reports_error(self, tmp_path):
        status = git_hook.hook_status(tmp_path / "nowhere")
        assert status["installed"] is False
        assert "not a git repo" in status["error"]


class TestSubdirResolution:
    def test_hook_path_resolves_from_subdirectory(self, git_repo):
        sub = git_repo / "src" / "deep"
        sub.mkdir(parents=True)
        target = git_hook.install_hook(sub)
        assert target == git_repo / ".git" / "hooks" / "pre-commit"
