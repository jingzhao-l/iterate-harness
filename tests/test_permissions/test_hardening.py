"""Regression tests for permission-checker hardening.

Covers the review fixes in ``permissions/checker.py``:

- ``denied_commands`` is a hard boundary evaluated BEFORE the
  ``allowed_tools`` early return (an allowlisted tool may never run a
  denied command).
- Sensitive-path matching is case-folding, so a credential directory
  spelled ``.SSH`` still matches the built-in ``*/.ssh/*`` pattern on
  case-insensitive filesystems.
- ``mode_override`` only honors ``"plan"``; ``"default"`` / ``"full_auto"``
  / unknown values fall through to the configured mode.
"""

from __future__ import annotations

from pathlib import Path

from iterate_harness.config.settings import PermissionSettings
from iterate_harness.permissions import PermissionChecker, PermissionMode


def test_denied_command_beats_allowlisted_tool() -> None:
    checker = PermissionChecker(
        PermissionSettings(
            mode=PermissionMode.FULL_AUTO,
            allowed_tools=["bash"],
            denied_commands=["rm -rf *"],
        )
    )

    denied = checker.evaluate("bash", is_read_only=False, command="rm -rf /tmp/scratch")
    assert denied.allowed is False
    assert "deny pattern" in denied.reason

    # The allowlist still applies to commands that match no deny pattern.
    allowed = checker.evaluate("bash", is_read_only=False, command="ls -la")
    assert allowed.allowed is True


def test_sensitive_path_matching_is_case_insensitive(tmp_path: Path) -> None:
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
    credential = tmp_path / ".SSH" / "id_rsa"

    decision = checker.evaluate("read_file", is_read_only=True, file_path=str(credential))

    assert decision.allowed is False
    assert "sensitive credential path" in decision.reason


def test_default_override_is_a_noop_under_full_auto() -> None:
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))

    # "default" (the exit_plan_mode marker) must NOT pin the session back to
    # DEFAULT mode: the configured FULL_AUTO still wins.
    decision = checker.evaluate(
        "write_file",
        is_read_only=False,
        file_path=str(Path("/tmp/scratch.py")),
        mode_override="default",
    )
    assert decision.allowed is True


def test_plan_override_blocks_mutating_tools() -> None:
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))

    decision = checker.evaluate(
        "write_file",
        is_read_only=False,
        file_path=str(Path("/tmp/scratch.py")),
        mode_override="plan",
    )
    assert decision.allowed is False
    assert "Plan mode" in decision.reason


def test_unknown_override_falls_back_to_configured_mode() -> None:
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))

    decision = checker.evaluate(
        "write_file",
        is_read_only=False,
        file_path=str(Path("/tmp/scratch.py")),
        mode_override="full_auto",  # a tool-supplied escalation is ignored
    )
    assert decision.allowed is True  # configured mode, not the override

    decision = checker.evaluate(
        "write_file",
        is_read_only=False,
        file_path=str(Path("/tmp/scratch.py")),
        mode_override="not_a_mode",
    )
    assert decision.allowed is True  # configured FULL_AUTO, no crash


def test_bash_command_paths_cannot_bypass_sensitive_boundary(tmp_path: Path) -> None:
    """An allowlisted/full-auto ``bash`` must not read credential files.

    ``bash`` has no ``file_path`` field, so the engine derives path-looking
    tokens from the command (``command_paths``) and the checker evaluates every
    hard path boundary against them too — ``cat ~/.ssh/id_rsa`` stays denied
    even though the tool itself is fully allowed.
    """
    checker = PermissionChecker(
        PermissionSettings(mode=PermissionMode.FULL_AUTO, allowed_tools=["bash"])
    )

    decision = checker.evaluate(
        "bash",
        is_read_only=False,
        command="cat /Users/someone/.ssh/id_rsa",
        command_paths=["/Users/someone/.ssh/id_rsa"],
    )
    assert decision.allowed is False
    assert "sensitive credential path" in decision.reason

    # The same command with no paths supplied used to fall through to the mode
    # shortcut; it is now blocked whenever the engine derives the path.
    blocked = checker.evaluate(
        "bash",
        is_read_only=False,
        command="cat ~/.ssh/id_rsa",
        command_paths=[str(tmp_path / ".ssh" / "id_rsa")],
    )
    assert blocked.allowed is False


def test_bash_command_paths_respect_deny_rules_and_risk_areas(tmp_path: Path) -> None:
    checker = PermissionChecker(
        PermissionSettings(
            mode=PermissionMode.FULL_AUTO,
            path_rules=[{"pattern": "*/secrets/*", "allow": False}],
        ),
        risk_area_patterns=["*/db/prod.sql"],
    )

    # Deny rule: a path referenced only inside the command string is denied.
    denied = checker.evaluate(
        "bash",
        is_read_only=False,
        command="cat secrets/token",
        command_paths=[str(tmp_path / "secrets" / "token")],
    )
    assert denied.allowed is False
    assert "deny rule" in denied.reason

    # Risk area: a mutating bash command touching the risk path requires
    # explicit approval even in full_auto mode.
    risky = checker.evaluate(
        "bash",
        is_read_only=False,
        command='echo "drop" >> db/prod.sql',
        command_paths=[str(tmp_path / "db" / "prod.sql")],
    )
    assert risky.allowed is False
    assert risky.requires_confirmation is True


def test_bash_legitimate_command_without_path_tokens_is_not_blocked(tmp_path: Path) -> None:
    checker = PermissionChecker(
        PermissionSettings(mode=PermissionMode.FULL_AUTO, allowed_tools=["bash"])
    )

    # A benign command derives no path tokens (flags/bare words only), so
    # nothing feeds the hard boundaries and the mode shortcut applies.
    decision = checker.evaluate(
        "bash",
        is_read_only=False,
        command="ls -la && make build",
        command_paths=None,
    )
    assert decision.allowed is True


def test_denied_command_cannot_be_bypassed_by_shell_chaining() -> None:
    """A deny pattern matches every simple command, not just the raw string.

    Previously only the whole command was fnmatch'd, so ``cd /tmp && rm -rf …``,
    ``true; rm -rf …``, a trailing comment, or ``bash -c '…'`` all slipped past
    ``rm -rf *`` — a "hard boundary" that was only a first-token boundary.
    """
    checker = PermissionChecker(
        PermissionSettings(
            mode=PermissionMode.FULL_AUTO,
            allowed_tools=["bash"],
            denied_commands=["rm -rf *"],
        )
    )

    for command in (
        "cd /tmp && rm -rf build",
        "true; rm -rf /",
        "echo hi | rm -rf /",
        "bash -c 'rm -rf /'",
        "sh -c \"rm -rf build\"",
        "\nrm -rf /",
        "rm -rf / # harmless",
    ):
        decision = checker.evaluate("bash", is_read_only=False, command=command)
        assert decision.allowed is False, command
        assert "deny pattern" in decision.reason

    # A command that merely *mentions* the words is still allowed.
    allowed = checker.evaluate(
        "bash", is_read_only=False, command="echo 'rm -rf is denied'"
    )
    assert allowed.allowed is True


def test_extract_command_paths_derives_path_tokens(tmp_path: Path) -> None:
    """The engine's command-tokenizer feeds the checker's command_paths."""
    from iterate_harness.engine.query import _extract_command_paths

    cwd = tmp_path / "proj"
    cwd.mkdir()

    paths = _extract_command_paths("cat ~/.ssh/id_rsa && rm -rf ./build", cwd)
    assert str(Path.home() / ".ssh" / "id_rsa") in paths
    assert str(cwd / "build") in paths

    # Flags, bare words, operators and env assignments are NOT paths: a benign
    # command must not trip the boundaries.
    assert _extract_command_paths("ls -la && make build", cwd) == []
    assert _extract_command_paths("FOO=bar python ./run.py", cwd) == [str(cwd / "run.py")]
    # Bare relative names without a separator ("build") are not path-looking;
    # dot-relative ones are ("./test").
    assert _extract_command_paths("cd build && ./test", cwd) == [str(cwd / "test")]
