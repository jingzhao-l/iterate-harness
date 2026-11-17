"""``ih permissions`` CLI (design §17.3 P16).

The exit codes are part of the contract (0 allowed / 2 needs confirmation /
1 denied or invalid input), so they are pinned here rather than only exercised
by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.config.paths import get_config_file_path
from iterate_harness.config.settings import PathRuleConfig, PermissionSettings, Settings


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config"
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(config))
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    return config


def _write_settings(settings: Settings) -> None:
    path = get_config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")


def test_show_json_returns_the_policy(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app, ["permissions", "show", "--project-root", str(tmp_path), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["project_root"] == str(tmp_path.resolve())
    assert payload["mode"] in {"default", "plan", "full_auto"}
    assert isinstance(payload["path_rules"], list)
    assert payload["summary"]["sensitive_paths"] > 0


def test_show_strict_fails_on_a_dead_rule(tmp_path: Path) -> None:
    _write_settings(
        Settings(permission=PermissionSettings(path_rules=[PathRuleConfig(pattern="", allow=False)]))
    )

    result = CliRunner().invoke(
        cli.app, ["permissions", "show", "--project-root", str(tmp_path), "--strict"]
    )

    assert result.exit_code == 1
    assert "path_rule" in result.output
    assert "remedy" in result.output


def test_show_without_strict_does_not_gate_on_problems(tmp_path: Path) -> None:
    _write_settings(
        Settings(permission=PermissionSettings(path_rules=[PathRuleConfig(pattern="", allow=False)]))
    )

    result = CliRunner().invoke(
        cli.app, ["permissions", "show", "--project-root", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert "Problems (1)" in result.output


def test_check_allowed_exits_zero(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "permissions",
            "check",
            "read_file",
            "--project-root",
            str(tmp_path),
            "--path",
            "README.md",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output.startswith("ALLOWED: read_file")


def test_check_confirmation_exits_two(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "permissions",
            "check",
            "write_file",
            "--project-root",
            str(tmp_path),
            "--path",
            "src/app.py",
            "--mode",
            "default",
        ],
    )

    assert result.exit_code == 2
    assert result.output.startswith("NEEDS CONFIRMATION: write_file")


def test_check_denied_exits_one(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "permissions",
            "check",
            "read_file",
            "--project-root",
            str(tmp_path),
            "--path",
            ".env",
            "--mode",
            "full_auto",
        ],
    )

    assert result.exit_code == 1
    assert "sensitive credential path" in result.output


def test_check_json_shape(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "permissions",
            "check",
            "bash",
            "--project-root",
            str(tmp_path),
            "--command",
            "cat ~/.ssh/id_rsa",
            "--mode",
            "full_auto",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["verdict"] == "denied"
    assert payload["read_only"] is False
    assert payload["command"] == "cat ~/.ssh/id_rsa"
    assert any(path.endswith(".ssh/id_rsa") for path in payload["command_paths"])


def test_check_rejects_unknown_mode(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        ["permissions", "check", "read_file", "--project-root", str(tmp_path), "--mode", "god"],
    )

    assert result.exit_code == 1
    assert "unknown mode" in result.output


def test_check_rejects_conflicting_read_only_flags(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "permissions",
            "check",
            "read_file",
            "--project-root",
            str(tmp_path),
            "--read-only",
            "--mutating",
        ],
    )

    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_group_help_lists_both_subcommands() -> None:
    result = CliRunner().invoke(cli.app, ["permissions", "--help"])

    assert result.exit_code == 0
    assert "show" in result.output
    assert "check" in result.output
