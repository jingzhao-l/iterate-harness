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
