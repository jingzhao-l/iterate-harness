"""Kernel integration tests for the iterate wiring (8 fixed-point diffs).

Covers: engine control block (ReviewProgressEvent + convergence stop +
next-round injection), permission allowlist / read-write path rules,
claudemd ITERATE.md injection, Settings.iterate round-trip, compact
attachment preservation, tool/command/bundled-skill registration.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from openharness.api.client import ApiMessageCompleteEvent, ApiTextDeltaEvent
from openharness.api.usage import UsageSnapshot
from openharness.config.settings import PathRuleConfig, PermissionSettings, Settings
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import (
    AssistantTurnComplete,
    ReviewProgressEvent,
    StatusEvent,
)
from openharness.iterate.loop_policy import IterateLoopPolicy
from openharness.permissions.checker import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.tools import create_default_tool_registry


@dataclass
class _FakeResponse:
    message: ConversationMessage
    usage: UsageSnapshot


class FakeApiClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)

    async def stream_message(self, request):
        del request
        response = self._responses.pop(0)
        for block in response.message.content:
            if isinstance(block, TextBlock) and block.text:
                yield ApiTextDeltaEvent(text=block.text)
        yield ApiMessageCompleteEvent(
            message=response.message,
            usage=response.usage,
            stop_reason=None,
        )


AGGREGATE_ROUNDS_CONVERGED = [
    {"round": 1, "findings": [
        {
            "dimension": "security",
            "file": "src/a.py",
            "severity": "high",
            "summary": "issue one",
            "failure_scenario": "x",
            "suggested_fix": "y",
            "is_atomic": True,
            "line": 3,
        },
        {
            "dimension": "security",
            "file": "src/a.py",
            "severity": "high",
            "summary": "issue one",  # duplicate
            "failure_scenario": "x",
            "suggested_fix": "y",
            "is_atomic": True,
            "line": 3,
        },
    ]},
    {"round": 2, "findings": []},
]
AGGREGATE_ROUNDS_ACTIVE = [{"round": 1, "findings": AGGREGATE_ROUNDS_CONVERGED[0]["findings"]}]


def aggregate_call(rounds, call_id="toolu_agg"):
    return ToolUseBlock(
        id=call_id,
        name="iterate_review",
        input={
            "operation": "aggregate",
            "mode": "dry-run",
            "rounds": rounds,
            "max_review_rounds": 3,
        },
    )


@pytest.mark.asyncio
async def test_engine_emits_progress_and_stops_on_convergence(tmp_path):
    engine = QueryEngine(
        api_client=FakeApiClient([
            _FakeResponse(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        TextBlock(text="Aggregating."),
                        aggregate_call(AGGREGATE_ROUNDS_CONVERGED),
                    ],
                ),
                usage=UsageSnapshot(input_tokens=100, output_tokens=50),
            ),
            _FakeResponse(  # would run if the loop (wrongly) continued
                message=ConversationMessage(role="assistant", content=[TextBlock(text="unreachable")]),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            ),
        ]),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(
            PermissionSettings(mode=PermissionMode.FULL_AUTO)
        ),
        cwd=tmp_path,
        model="claude-sonnet-4-6",
        system_prompt="system",
        iterate_policy=IterateLoopPolicy(max_review_rounds=3),
    )

    events = [event async for event in engine.submit_message("run dry-run")]

    progress = [e for e in events if isinstance(e, ReviewProgressEvent)]
    assert len(progress) == 1
    assert progress[0].round == 2
    assert progress[0].new_findings == 0  # duplicate collapsed deterministically
    assert progress[0].total_findings == 1
    assert progress[0].converged is True
    assert progress[0].cost_usd > 0  # 100 in + 50 out on a priced model

    stops = [e for e in events if isinstance(e, StatusEvent) and "iterate loop stopped" in e.message]
    assert stops and "converged" in stops[0].message
    assert not any(
        isinstance(e, AssistantTurnComplete) and "unreachable" in e.message.text for e in events
    )


@pytest.mark.asyncio
async def test_engine_injects_next_round_when_not_converged(tmp_path):
    engine = QueryEngine(
        api_client=FakeApiClient([
            _FakeResponse(
                message=ConversationMessage(
                    role="assistant",
                    content=[aggregate_call(AGGREGATE_ROUNDS_ACTIVE)],
                ),
                usage=UsageSnapshot(input_tokens=10, output_tokens=5),
            ),
            _FakeResponse(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            ),
        ]),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(
            PermissionSettings(mode=PermissionMode.FULL_AUTO)
        ),
        cwd=tmp_path,
        model="claude-sonnet-4-6",
        system_prompt="system",
        iterate_policy=IterateLoopPolicy(max_review_rounds=3),
    )

    events = [event async for event in engine.submit_message("run dry-run")]

    progress = [e for e in events if isinstance(e, ReviewProgressEvent)]
    assert len(progress) == 1
    assert progress[0].new_findings == 1
    assert progress[0].converged is False
    # The injected next-round steering message landed in history as user turn.
    injected = [m for m in engine.messages if m.role == "user" and "[iterate]" in m.text]
    assert injected and "Round 1" in injected[-1].text
    assert isinstance(events[-1], AssistantTurnComplete)


@pytest.mark.asyncio
async def test_engine_without_policy_keeps_upstream_behavior(tmp_path):
    engine = QueryEngine(
        api_client=FakeApiClient([
            _FakeResponse(
                message=ConversationMessage(
                    role="assistant",
                    content=[aggregate_call(AGGREGATE_ROUNDS_CONVERGED)],
                ),
                usage=UsageSnapshot(input_tokens=10, output_tokens=5),
            ),
            _FakeResponse(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="final")]),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            ),
        ]),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(
            PermissionSettings(mode=PermissionMode.FULL_AUTO)
        ),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        iterate_policy=False,  # explicitly disabled
    )
    events = [event async for event in engine.submit_message("go")]
    assert not any(isinstance(e, ReviewProgressEvent) for e in events)
    assert isinstance(events[-1], AssistantTurnComplete)


class TestPermissionIntegration:
    def test_allowed_commands_exact_match_permits_bash(self):
        checker = PermissionChecker(
            PermissionSettings(mode=PermissionMode.DEFAULT, allowed_commands=["pytest tests/ -q"])
        )
        decision = checker.evaluate("bash", is_read_only=False, command="pytest tests/ -q")
        assert decision.allowed and not decision.requires_confirmation
        # Prefix does NOT match.
        decision = checker.evaluate("bash", is_read_only=False, command="pytest")
        assert not decision.allowed

    def test_deny_path_rule_blocks_writes_and_reads(self):
        # Upstream contract preserved: deny rules block both directions.
        # Iterate protected_paths ride these rules (a strict superset of the
        # design minimum "block writes, allow reads").
        checker = PermissionChecker(
            PermissionSettings(
                mode=PermissionMode.DEFAULT,
                path_rules=[PathRuleConfig(pattern="*/generated/*", allow=False)],
            )
        )
        write = checker.evaluate("file_write", is_read_only=False, file_path="/x/generated/a.py")
        assert not write.allowed
        read = checker.evaluate("file_read", is_read_only=True, file_path="/x/generated/a.py")
        assert not read.allowed


class TestClaudemdInjection:
    def test_iterate_md_is_discovered_like_claude_md(self, tmp_path):
        from openharness.prompts.claudemd import discover_claude_md_files, load_claude_md_prompt

        (tmp_path / "ITERATE.md").write_text("# project knowledge\n")
        found = discover_claude_md_files(tmp_path)
        assert any(p.name == "ITERATE.md" for p in found)
        prompt = load_claude_md_prompt(tmp_path)
        assert prompt is not None and "project knowledge" in prompt


class TestSettingsAndCompact:
    def test_settings_iterate_roundtrip(self):
        settings = Settings.model_validate(
            {"iterate": {"max_review_rounds": 5, "protected_paths": ["secrets/*"]}}
        )
        assert settings.iterate.max_review_rounds == 5
        assert settings.iterate.protected_paths == ["secrets/*"]
        again = Settings.model_validate(settings.model_dump())
        assert again.iterate.max_review_rounds == 5

    def test_compact_preserves_iterate_state(self):
        from openharness.services.compact import create_iterate_review_attachment_if_needed

        attachment = create_iterate_review_attachment_if_needed(
            {
                "mode": "dry-run",
                "rounds_seen": 2,
                "total_findings": 4,
                "findings_by_round": [3, 1],
                "converged": False,
                "by_dimension": {"security": 4},
            }
        )
        assert attachment is not None
        assert attachment.kind == "iterate_review"
        assert "rounds=2" in attachment.body
        assert "security=4" in attachment.body
        assert create_iterate_review_attachment_if_needed(None) is None
        assert create_iterate_review_attachment_if_needed({}) is None


class TestRegistration:
    def test_five_iterate_tools_in_default_registry(self):
        registry = create_default_tool_registry()
        for name in (
            "iterate_config",
            "iterate_validate",
            "iterate_review",
            "iterate_decision_log",
            "iterate_context",
        ):
            assert registry.get(name) is not None, f"missing tool {name}"

    def test_iterate_slash_command_registered(self):
        from openharness.commands.registry import create_default_command_registry

        registry = create_default_command_registry()
        commands = {c.name for c in registry.list_commands()}
        assert "iterate" in commands

    def test_iterate_bundled_skill_loaded(self):
        from openharness.skills.bundled import get_bundled_skills

        names = {s.name for s in get_bundled_skills()}
        assert "iterate" in names
