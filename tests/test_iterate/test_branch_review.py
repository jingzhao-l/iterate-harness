"""Tests for #19 branch review entry: ``ih iterate review|run --branch``.

Covers:
- CLI surface: both ``iterate review`` and ``iterate run`` expose the
  ``--branch`` option (parameter parsing);
- :func:`iterate_harness.cli._current_git_branch` — branch detection;
- :func:`iterate_harness.cli._ensure_review_branch` — plain checkout when
  the tree is clean, isolated-worktree fallback when the checkout fails
  (dirty tree), same-branch no-op, and a hard error on a missing branch.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import iterate_harness.cli as cli


def _init_git_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=str(path), check=True, capture_output=True
    )
    (path / "file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(path), check=True, capture_output=True
    )


def _current_branch(path: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(path),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


# -- CLI surface: --branch is a parseable option ------------------------------


def test_iterate_review_help_shows_branch_option():
    result = CliRunner().invoke(
        cli.app, ["iterate", "review", "--help"], env={"NO_COLOR": "1", "COLUMNS": "160"}
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert result.exit_code == 0
    assert "--branch" in plain
    assert "Target branch for the review" in plain


def test_iterate_run_help_shows_branch_option():
    result = CliRunner().invoke(
        cli.app, ["iterate", "run", "--help"], env={"NO_COLOR": "1", "COLUMNS": "160"}
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert result.exit_code == 0
    assert "--branch" in plain


def test_iterate_review_rejects_unknown_flag():
    result = CliRunner().invoke(
        cli.app, ["iterate", "review", "--branch"], env={"NO_COLOR": "1"}
    )
    # --branch requires a value; typer reports a usage error.
    assert result.exit_code != 0


# -- _current_git_branch ------------------------------------------------------


def test_current_git_branch_inside_repo(tmp_path):
    _init_git_repo(tmp_path)
    assert cli._current_git_branch(tmp_path) == "main"


def test_current_git_branch_outside_repo(tmp_path):
    assert cli._current_git_branch(tmp_path) is None


# -- _ensure_review_branch ----------------------------------------------------


def test_same_branch_is_noop(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli._ensure_review_branch("main") is None
    assert "Already on target branch main" in capsys.readouterr().out


def test_empty_branch_is_noop(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli._ensure_review_branch("") is None
    assert _current_branch(tmp_path) == "main"


def test_plain_checkout_succeeds_when_clean(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    subprocess.run(
        ["git", "checkout", "-b", "feature-x"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(tmp_path)
    assert cli._ensure_review_branch("feature-x") is None  # no cwd change
    assert _current_branch(tmp_path) == "feature-x"
    assert "Switched to branch feature-x" in capsys.readouterr().out


def test_worktree_fallback_on_dirty_tree(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    # Make file.txt differ between main and feature-x.
    subprocess.run(
        ["git", "checkout", "-b", "feature-x"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    (tmp_path / "file.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feature change"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    # Dirty the tree: local modification to a file whose content differs on
    # feature-x forces `git checkout feature-x` to fail, so the isolated
    # worktree path is taken instead.
    (tmp_path / "file.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    prev = cli._ensure_review_branch("feature-x")
    out = capsys.readouterr().out
    assert prev == str(tmp_path)  # cwd was changed; caller must restore it
    assert Path.cwd() != tmp_path  # now inside the linked worktree
    assert "Created isolated worktree for branch feature-x" in out

    # The linked worktree must hold the target branch checked out.
    assert _current_branch(Path.cwd()) == "feature-x"


def test_missing_branch_raises_exit(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(typer.Exit) as excinfo:
        cli._ensure_review_branch("does-not-exist")
    assert excinfo.value.exit_code == 1
    assert "Could not switch to branch does-not-exist" in capsys.readouterr().err
