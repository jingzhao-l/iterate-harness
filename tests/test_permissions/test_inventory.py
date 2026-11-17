"""Permission policy inspection + decision simulation (design §17.3 P16).

These tests pin the two promises the page and the CLI make: the policy shown is
the *merged* policy the engine enforces (iterate boundaries included), and a
simulated verdict comes from the real checker rather than a parallel
re-implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iterate_harness.config.settings import (
    PathRuleConfig,
    PermissionSettings,
    Settings,
)
from iterate_harness.iterate.settings import IterateSettings
from iterate_harness.permissions.inventory import (
    build_permission_policy,
    render_permission_decision,
    render_permission_policy,
    simulate_permission_decision,
)
from iterate_harness.permissions.modes import PermissionMode


def _settings(**permission: object) -> Settings:
    return Settings(
        permission=PermissionSettings(**permission),  # type: ignore[arg-type]
        iterate=IterateSettings(protected_paths=[]),
    )


# ---------------------------------------------------------------------------
# Policy projection
# ---------------------------------------------------------------------------


def test_policy_reports_mode_lists_and_counts(tmp_path: Path) -> None:
    settings = Settings(
        permission=PermissionSettings(
            mode=PermissionMode.DEFAULT,
            allowed_tools=["read_file"],
            denied_tools=["bash"],
            allowed_commands=["pytest -q"],
            denied_commands=["rm -rf *"],
            path_rules=[
                PathRuleConfig(pattern="*/secrets/*", allow=False),
                PathRuleConfig(pattern="*/docs/*", allow=True),
            ],
        ),
        iterate=IterateSettings(
            protected_paths=[".env"],
            risk_area_paths=["db/*"],
            forbidden_fix_patterns=["DROP TABLE"],
        ),
    )

    policy = build_permission_policy(tmp_path, settings=settings)

    assert policy["mode"] == "default"
    assert policy["mode_label"] == "默认"
    assert policy["has_problems"] is False
    assert policy["allowed_tools"] == ["read_file"]
    assert policy["denied_tools"] == ["bash"]
    assert policy["denied_commands"] == ["rm -rf *"]
    assert policy["allowed_commands"] == ["pytest -q"]
    assert policy["risk_areas"] == ["*/db/*"]
    assert policy["forbidden_patterns"] == ["DROP TABLE"]
    # The iterate protected path is merged into the deny rules (absolute glob).
    assert {"pattern": "*/.env", "allow": False} in policy["path_rules"]
    assert policy["summary"]["path_rules"] == 3
    assert policy["summary"]["deny_path_rules"] == 2
    assert policy["summary"]["allow_path_rules"] == 1
    assert policy["summary"]["sensitive_paths"] > 0
    assert any(tool["name"] == "read_file" and tool["read_only"] is True for tool in policy["tools"])


def test_policy_merges_project_personalization_boundaries(tmp_path: Path) -> None:
    (tmp_path / "iterate.config.yaml").write_text(
        "personalization:\n"
        "  protected_paths: ['private/*']\n"
        "  risk_areas:\n"
        "    - path: 'billing/*'\n"
        "      reason: money\n",
        encoding="utf-8",
    )
    settings = _settings(mode=PermissionMode.DEFAULT)

    policy = build_permission_policy(tmp_path, settings=settings)

    assert {"pattern": "*/private/*", "allow": False} in policy["path_rules"]
    assert "*/billing/*" in policy["risk_areas"]


def test_policy_names_rules_that_can_never_win(tmp_path: Path) -> None:
    settings = Settings(
        permission=PermissionSettings(
            allowed_tools=["bash"],
            denied_tools=["bash"],
            allowed_commands=["git status"],
            denied_commands=["git status"],
            path_rules=[
                PathRuleConfig(pattern="", allow=False),
                PathRuleConfig(pattern="*/x/*", allow=True),
                PathRuleConfig(pattern="*/x/*", allow=False),
            ],
        ),
        iterate=IterateSettings(
            protected_paths=[],
            risk_area_paths=[""],
            forbidden_fix_patterns=["[unclosed"],
        ),
    )

    policy = build_permission_policy(tmp_path, settings=settings)
    kinds = {problem["kind"] for problem in policy["problems"]}

    assert policy["has_problems"] is True
    assert {"path_rule", "tool_rule", "command_rule", "risk_area", "forbidden_pattern"} <= kinds
    assert all(problem["remedy"] for problem in policy["problems"])


def test_disabled_iterate_boundaries_are_reported(tmp_path: Path) -> None:
    settings = Settings(
        permission=PermissionSettings(mode=PermissionMode.FULL_AUTO),
        iterate=IterateSettings(enabled=False),
    )

    policy = build_permission_policy(tmp_path, settings=settings)

    assert policy["iterate_enabled"] is False
    assert any(problem["kind"] == "iterate" for problem in policy["problems"])
    # Nothing is merged when the boundaries are off.
    assert policy["path_rules"] == []


def test_policy_render_surfaces_problems_and_sections(tmp_path: Path) -> None:
    policy = build_permission_policy(
        tmp_path,
        settings=_settings(mode=PermissionMode.PLAN, denied_tools=["bash"]),
    )
    rendered = render_permission_policy(policy)

    assert "permission policy" in rendered
    assert "mode: plan" in rendered
    assert "Denied tools (1)" in rendered
    assert "bash" in rendered


# ---------------------------------------------------------------------------
# Decision simulation
# ---------------------------------------------------------------------------


def test_simulation_infers_read_only_from_the_registered_tool(tmp_path: Path) -> None:
    decision = simulate_permission_decision(
        "read_file",
        project_root=tmp_path,
        file_path="README.md",
        settings=_settings(mode=PermissionMode.DEFAULT),
    )

    assert decision["read_only"] is True
    assert decision["read_only_source"] == "inferred"
    assert decision["verdict"] == "allowed"


def test_unknown_tool_is_treated_as_mutating(tmp_path: Path) -> None:
    decision = simulate_permission_decision(
        "not_a_tool",
        project_root=tmp_path,
        settings=_settings(mode=PermissionMode.DEFAULT),
    )

    assert decision["read_only"] is False
    assert decision["read_only_source"] == "assumed"
    assert decision["verdict"] == "confirm"


def test_explicit_read_only_overrides_inference(tmp_path: Path) -> None:
    decision = simulate_permission_decision(
        "write_file",
        project_root=tmp_path,
        file_path="src/app.py",
        read_only=True,
        settings=_settings(mode=PermissionMode.DEFAULT),
    )

    assert decision["read_only_source"] == "explicit"
    assert decision["verdict"] == "allowed"


def test_sensitive_path_is_denied_even_in_full_auto(tmp_path: Path) -> None:
    decision = simulate_permission_decision(
        "read_file",
        project_root=tmp_path,
        file_path=".env",
        mode="full_auto",
        settings=_settings(mode=PermissionMode.FULL_AUTO),
    )

    assert decision["verdict"] == "denied"
    assert "sensitive credential path" in decision["reason"]
    assert decision["mode"] == "full_auto"


def test_command_paths_are_extracted_like_the_engine(tmp_path: Path) -> None:
    decision = simulate_permission_decision(
        "bash",
        project_root=tmp_path,
        command="cat ~/.ssh/id_rsa",
        settings=_settings(mode=PermissionMode.FULL_AUTO),
    )

    assert decision["verdict"] == "denied"
    assert any(path.endswith(".ssh/id_rsa") for path in decision["command_paths"])


def test_default_mode_asks_and_plan_mode_blocks(tmp_path: Path) -> None:
    default = simulate_permission_decision(
        "write_file",
        project_root=tmp_path,
        file_path="src/app.py",
        settings=_settings(mode=PermissionMode.DEFAULT),
    )
    plan = simulate_permission_decision(
        "write_file",
        project_root=tmp_path,
        file_path="src/app.py",
        settings=_settings(mode=PermissionMode.PLAN),
    )

    assert default["verdict"] == "confirm"
    assert default["requires_confirmation"] is True
    assert plan["verdict"] == "denied"
    assert "Plan mode" in plan["reason"]


def test_simulated_mode_overrides_the_configured_mode(tmp_path: Path) -> None:
    settings = _settings(mode=PermissionMode.PLAN)

    decision = simulate_permission_decision(
        "write_file",
        project_root=tmp_path,
        file_path="src/app.py",
        mode="full_auto",
        settings=settings,
    )

    assert decision["verdict"] == "allowed"
    assert decision["mode"] == "full_auto"
    assert decision["simulated_mode"] == "full_auto"


def test_forbidden_write_payload_is_denied(tmp_path: Path) -> None:
    settings = Settings(
        permission=PermissionSettings(mode=PermissionMode.FULL_AUTO),
        iterate=IterateSettings(protected_paths=[], forbidden_fix_patterns=["DROP TABLE"]),
    )

    decision = simulate_permission_decision(
        "write_file",
        project_root=tmp_path,
        file_path="db/schema.sql",
        content="DROP TABLE users;",
        settings=settings,
    )

    assert decision["verdict"] == "denied"
    assert decision["content_checked"] is True
    assert "forbidden fix pattern" in decision["reason"]


def test_risk_area_requires_confirmation_even_in_full_auto(tmp_path: Path) -> None:
    settings = Settings(
        permission=PermissionSettings(mode=PermissionMode.FULL_AUTO),
        iterate=IterateSettings(protected_paths=[], risk_area_paths=["db/*"]),
    )

    decision = simulate_permission_decision(
        "write_file",
        project_root=tmp_path,
        file_path="db/migrations/001.sql",
        settings=settings,
    )

    assert decision["verdict"] == "confirm"
    assert "risk area" in decision["reason"]


def test_denied_tool_and_command_lists_win_over_mode(tmp_path: Path) -> None:
    settings = _settings(
        mode=PermissionMode.FULL_AUTO,
        denied_tools=["bash"],
        denied_commands=["git push --force*"],
    )

    tool = simulate_permission_decision("bash", project_root=tmp_path, settings=settings)
    command = simulate_permission_decision(
        "write_file",
        project_root=tmp_path,
        command="git push --force origin main",
        settings=settings,
    )

    assert tool["verdict"] == "denied"
    assert command["verdict"] == "denied"
    assert "deny pattern" in command["reason"]


def test_invalid_simulation_input_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tool_name is required"):
        simulate_permission_decision("  ", project_root=tmp_path, settings=_settings())
    with pytest.raises(ValueError, match="unknown mode"):
        simulate_permission_decision(
            "read_file", project_root=tmp_path, mode="god_mode", settings=_settings()
        )


def test_decision_render_names_the_verdict_and_reason(tmp_path: Path) -> None:
    decision = simulate_permission_decision(
        "read_file",
        project_root=tmp_path,
        file_path=".env",
        settings=_settings(mode=PermissionMode.FULL_AUTO),
    )
    rendered = render_permission_decision(decision)

    assert rendered.startswith("DENIED: read_file")
    assert "sensitive credential path" in rendered
    assert "read-only: True (inferred)" in rendered
