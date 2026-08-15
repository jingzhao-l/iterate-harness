"""Tests for iterate_harness.iterate.validate (port of the validate tool core)."""

from __future__ import annotations

import sys
from pathlib import Path

from iterate_harness.iterate.validate import run_command, run_validation


def write_config(project: Path, content: str) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / "iterate.config.yaml").write_text(content, encoding="utf-8")


class TestRunCommand:
    def test_captures_stdout_exit_code_and_duration(self, tmp_path):
        result = run_command("echo hello", str(tmp_path), 30_000)
        assert result.exit_code == 0
        assert result.stdout.strip() == "hello"
        assert result.stderr == ""
        assert result.timed_out is False
        assert result.duration_ms >= 0

    def test_captures_nonzero_exit_and_stderr(self, tmp_path):
        result = run_command("echo oops >&2; exit 3", str(tmp_path), 30_000)
        assert result.exit_code == 3
        assert result.stderr.strip() == "oops"

    def test_reports_timeout_without_raising(self, tmp_path):
        slow_cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
        result = run_command(slow_cmd, str(tmp_path), 300)
        assert result.timed_out is True
        assert result.exit_code == -1


class TestRunValidationSecurityGate:
    def test_rejects_when_no_config_file_exists(self, tmp_path):
        result = run_validation("pytest tests/", tmp_path)
        assert result.allowed is False
        assert result.exit_code == -1
        assert result.reject_reason is not None
        assert "No iterate.config.yaml" in result.reject_reason

    def test_rejects_when_config_has_no_commands(self, tmp_path):
        write_config(tmp_path, 'goal: "g"\n')
        result = run_validation("pytest tests/", tmp_path)
        assert result.allowed is False
        assert "No validation.commands configured" in (result.reject_reason or "")

    def test_rejects_non_exact_command_match(self, tmp_path):
        write_config(
            tmp_path,
            "validation:\n  commands:\n    python:\n      - 'pytest tests/ -x -q'\n",
        )
        # Prefix is NOT enough.
        result = run_validation("pytest", tmp_path)
        assert result.allowed is False
        assert "exactly match" in (result.reject_reason or "")
        # Suffix injection is NOT allowed either.
        result = run_validation("pytest tests/ -x -q --evil-flag", tmp_path)
        assert result.allowed is False

    def test_allows_and_runs_exact_match_with_trim(self, tmp_path):
        write_config(
            tmp_path,
            "validation:\n  commands:\n    python:\n      - 'echo ok-from-config'\n",
        )
        result = run_validation("  echo ok-from-config  ", tmp_path)
        assert result.allowed is True
        assert result.reject_reason is None
        assert result.exit_code == 0
        assert result.stdout.strip() == "ok-from-config"

    def test_injection_cannot_reuse_whitelisted_prefix(self, tmp_path):
        write_config(
            tmp_path,
            "validation:\n  commands:\n    python:\n      - 'python3 -c \"print(1)\"'\n",
        )
        result = run_validation('python3 -c "import os; os.system(\'rm -rf /\')"', tmp_path)
        assert result.allowed is False
