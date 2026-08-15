"""Kernel integration tests for the iterate wiring (8 fixed-point diffs).

Covers: engine control block (ReviewProgressEvent + convergence stop +
next-round injection), permission allowlist / read-write path rules,
claudemd ITERATE.md injection, Settings.iterate round-trip, compact
attachment preservation, tool/command/bundled-skill registration.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from iterate_harness.api.client import ApiMessageCompleteEvent, ApiTextDeltaEvent
from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.config.settings import PathRuleConfig, PermissionSettings, Settings
from iterate_harness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from iterate_harness.engine.query_engine import QueryEngine
from iterate_harness.engine.stream_events import (
    AssistantTurnComplete,
    ReviewProgressEvent,
    StatusEvent,
)
from iterate_harness.iterate.loop_policy import IterateLoopPolicy
from iterate_harness.permissions.checker import PermissionChecker
from iterate_harness.permissions.modes import PermissionMode
from iterate_harness.tools import create_default_tool_registry


@dataclass
class _FakeResponse:
    message: ConversationMessage
    usage: UsageSnapshot


class FakeApiClient:
    def __init__(self, responses: list[_FakeResponse], on_stream=None) -> None:
        self._responses = list(responses)
        self._on_stream = on_stream

    async def stream_message(self, request):
        del request
        if self._on_stream is not None:
            self._on_stream()
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
        write = checker.evaluate("write_file", is_read_only=False, file_path="/x/generated/a.py")
        assert not write.allowed
        read = checker.evaluate("file_read", is_read_only=True, file_path="/x/generated/a.py")
        assert not read.allowed


class TestIteratePermissionWiring:
    """build_permission_checker auto-assembles iterate settings into the layer."""

    def test_default_protected_paths_deny_reads_and_writes(self):
        from iterate_harness.permissions.checker import build_permission_checker

        settings = Settings()  # default iterate.protected_paths (.env, *.key, ...)
        checker = build_permission_checker(settings)
        for path in ("/proj/.env", "/.env", "/proj/sub/server.key", "/proj/credentials.json"):
            write = checker.evaluate("write_file", is_read_only=False, file_path=path)
            assert not write.allowed and not write.requires_confirmation, path
            assert "deny rule" in write.reason, path
            read = checker.evaluate("file_read", is_read_only=True, file_path=path)
            assert not read.allowed, path

    def test_directory_scoped_protected_path_normalizes(self):
        from iterate_harness.permissions.checker import build_permission_checker

        settings = Settings.model_validate({"iterate": {"protected_paths": ["secrets/*"]}})
        checker = build_permission_checker(settings)
        decision = checker.evaluate("write_file", is_read_only=False, file_path="/proj/secrets/token.txt")
        assert not decision.allowed
        # Directory root itself (grep/glob style) is blocked too.
        decision = checker.evaluate("grep", is_read_only=True, file_path="/proj/secrets")
        assert not decision.allowed

    def test_disabled_iterate_passes_permission_settings_through(self):
        from iterate_harness.permissions.checker import build_permission_checker

        settings = Settings.model_validate({"iterate": {"enabled": False}})
        checker = build_permission_checker(settings)
        # No iterate deny wiring: default mode falls back to confirmation flow.
        decision = checker.evaluate("write_file", is_read_only=False, file_path="/proj/.env")
        assert not decision.allowed
        assert decision.requires_confirmation
        assert "deny rule" not in decision.reason

    def test_user_path_rules_are_never_duplicated_or_mutated(self):
        from iterate_harness.permissions.checker import build_permission_checker

        settings = Settings.model_validate({"iterate": {"protected_paths": [".env", "*.pem"]}})
        settings.permission.path_rules.append(PathRuleConfig(pattern="*/.env", allow=False))
        original_rules = list(settings.permission.path_rules)
        checker = build_permission_checker(settings)
        env_rules = [r for r in checker._path_rules if r.pattern == "*/.env"]
        assert len(env_rules) == 1
        assert any(r.pattern == "*/*.pem" and not r.allow for r in checker._path_rules)
        # Source settings untouched (factory works on a deep copy).
        assert settings.permission.path_rules == original_rules

    def test_forbidden_fix_patterns_block_matching_write_payloads(self):
        from iterate_harness.permissions.checker import build_permission_checker

        settings = Settings.model_validate(
            {"iterate": {"forbidden_fix_patterns": [r"AKIA[A-Z0-9]{16}"]}}
        )
        checker = build_permission_checker(settings)
        blocked = checker.evaluate(
            "write_file", is_read_only=False, file_path="/proj/app.py",
            content='key = "AKIAIOSFODNN7EXAMPLE"',
        )
        assert not blocked.allowed and not blocked.requires_confirmation
        assert "forbidden fix pattern" in blocked.reason
        clean = checker.evaluate(
            "write_file", is_read_only=False, file_path="/proj/app.py",
            content='key = "helloworld"',
        )
        assert clean.requires_confirmation  # default-mode flow, not hard-denied

    def test_forbidden_boundary_precedes_tool_allowlist(self):
        from iterate_harness.permissions.checker import build_permission_checker

        settings = Settings.model_validate(
            {
                "iterate": {"forbidden_fix_patterns": [r"sk-[a-z]+"]},
                "permission": {"allowed_tools": ["write_file"]},
            }
        )
        checker = build_permission_checker(settings)
        decision = checker.evaluate(
            "write_file", is_read_only=False, content="token = sk-abc123"
        )
        assert not decision.allowed
        assert "forbidden fix pattern" in decision.reason

    def test_invalid_forbidden_regex_is_skipped_valid_still_enforced(self):
        from iterate_harness.permissions.checker import build_permission_checker

        settings = Settings.model_validate(
            {"iterate": {"forbidden_fix_patterns": ["[unclosed", r"BEGIN PRIVATE KEY"]}}
        )
        checker = build_permission_checker(settings)
        decision = checker.evaluate(
            "edit_file", is_read_only=False, content="-----BEGIN PRIVATE KEY-----"
        )
        assert not decision.allowed
        assert "BEGIN PRIVATE KEY" in decision.reason

    def test_engine_extracts_write_payload_for_mutating_tools_only(self):
        from iterate_harness.engine.query import _extract_permission_content

        assert _extract_permission_content({"content": "body"}, object()) == "body"
        assert _extract_permission_content({"new_string": "edit"}, object()) == "edit"
        assert _extract_permission_content({"diff": "+x"}, object()) == "+x"
        assert _extract_permission_content({"patch": "-y"}, object()) == "-y"
        assert _extract_permission_content({"file_path": "/a"}, object()) is None

        class _Parsed:
            content = "from-model"

        assert _extract_permission_content({}, _Parsed()) == "from-model"


class TestClaudemdInjection:
    def test_iterate_md_is_discovered_like_claude_md(self, tmp_path):
        from iterate_harness.prompts.claudemd import discover_claude_md_files, load_claude_md_prompt

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
        from iterate_harness.services.compact import create_iterate_review_attachment_if_needed

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


class TestFixApproval:
    """Per-fix diff approval (Settings.iterate.require_fix_approval)."""

    def _context(self, tmp_path, *, policy, mode="normal", checker=None, prompt=None):
        from iterate_harness.engine.query import QueryContext
        from iterate_harness.tools.base import ToolRegistry
        from iterate_harness.tools.file_edit_tool import FileEditTool
        from iterate_harness.tools.file_write_tool import FileWriteTool

        registry = ToolRegistry()
        registry.register(FileEditTool())
        registry.register(FileWriteTool())
        metadata = {"iterate_state": {"mode": mode}} if mode else None
        return QueryContext(
            api_client=None,  # type: ignore[arg-type]
            tool_registry=registry,
            permission_checker=checker
            or PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
            cwd=tmp_path,
            model="test-model",
            system_prompt="",
            max_tokens=64,
            permission_prompt=prompt,
            tool_metadata=metadata,
            iterate_policy=policy,
        )

    @pytest.mark.asyncio
    async def test_normal_mode_file_edit_prompts_with_diff_preview(self, tmp_path):
        from iterate_harness.engine.query import _execute_tool_call

        target = tmp_path / "app.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")
        prompts: list[tuple[str, str]] = []

        async def deny(tool_name: str, reason: str) -> bool:
            prompts.append((tool_name, reason))
            return False

        context = self._context(
            tmp_path,
            policy=IterateLoopPolicy(require_fix_approval=True),
            prompt=deny,
        )
        result = await _execute_tool_call(
            context,
            "edit_file",
            "t1",
            {"path": str(target), "old_str": "beta", "new_str": "gamma"},
        )
        assert result.is_error
        assert prompts and prompts[0][0] == "edit_file"
        reason = prompts[0][1]
        assert "Iterate fix approval" in reason and "app.py" in reason
        assert "-beta" in reason and "+gamma" in reason
        assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"  # denied: unchanged

    @pytest.mark.asyncio
    async def test_approved_edit_executes(self, tmp_path):
        from iterate_harness.engine.query import _execute_tool_call

        target = tmp_path / "app.py"
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        async def approve(tool_name: str, reason: str) -> bool:
            return True

        context = self._context(
            tmp_path,
            policy=IterateLoopPolicy(require_fix_approval=True),
            prompt=approve,
        )
        result = await _execute_tool_call(
            context,
            "edit_file",
            "t1",
            {"path": str(target), "old_str": "beta", "new_str": "gamma"},
        )
        assert not result.is_error
        assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"

    @pytest.mark.asyncio
    async def test_gate_off_policy_or_dry_run_does_not_prompt(self, tmp_path):
        from iterate_harness.engine.query import _execute_tool_call

        target = tmp_path / "app.py"
        target.write_text("alpha\n", encoding="utf-8")

        async def fail_prompt(tool_name: str, reason: str) -> bool:
            raise AssertionError("permission prompt must not be called")

        for policy, mode in (
            (IterateLoopPolicy(require_fix_approval=False), "normal"),
            (IterateLoopPolicy(require_fix_approval=True), "dry-run"),
            (None, "normal"),
        ):
            context = self._context(tmp_path, policy=policy, mode=mode, prompt=fail_prompt)
            result = await _execute_tool_call(
                context,
                "write_file",
                "t1",
                {"path": str(target), "content": "alpha\n"},
            )
            assert not result.is_error, (policy, mode)

    @pytest.mark.asyncio
    async def test_hard_deny_not_overridden_into_confirmation(self, tmp_path):
        from iterate_harness.engine.query import _execute_tool_call
        from iterate_harness.permissions.checker import build_permission_checker

        settings = Settings.model_validate(
            {"iterate": {"protected_paths": ["secrets/*"], "require_fix_approval": True}}
        )
        checker = build_permission_checker(settings)
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        target = secrets_dir / "token.txt"

        async def fail_prompt(tool_name: str, reason: str) -> bool:
            raise AssertionError("hard deny must not become a confirmation")

        context = self._context(
            tmp_path,
            policy=IterateLoopPolicy(require_fix_approval=True),
            checker=checker,
            prompt=fail_prompt,
        )
        result = await _execute_tool_call(
            context,
            "write_file",
            "t1",
            {"path": str(target), "content": "secret"},
        )
        assert result.is_error
        assert "deny rule" in result.content
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_forbidden_pattern_boundary_covers_file_edit_new_str(self, tmp_path):
        from iterate_harness.engine.query import _execute_tool_call
        from iterate_harness.permissions.checker import build_permission_checker

        settings = Settings.model_validate(
            {"iterate": {"forbidden_fix_patterns": [r"AKIA[A-Z0-9]{16}"]}}
        )
        checker = build_permission_checker(settings)
        target = tmp_path / "creds.py"
        target.write_text("key = ''\n", encoding="utf-8")

        async def fail_prompt(tool_name: str, reason: str) -> bool:
            raise AssertionError("forbidden content must hard-deny without prompting")

        context = self._context(
            tmp_path,
            policy=IterateLoopPolicy(require_fix_approval=True),
            checker=checker,
            prompt=fail_prompt,
        )
        result = await _execute_tool_call(
            context,
            "edit_file",
            "t1",
            {"path": str(target), "old_str": "''", "new_str": "AKIAIOSFODNN7EXAMPLE"},
        )
        assert result.is_error
        assert "forbidden fix pattern" in result.content
        assert target.read_text(encoding="utf-8") == "key = ''\n"

    def test_fix_approval_reason_diff_variants(self, tmp_path):
        from iterate_harness.engine.query import _fix_approval_reason

        # file_write to a new file → "+ lines" preview + total marker.
        reason = _fix_approval_reason(
            "write_file", str(tmp_path / "new.py"), {"content": "a\nb\n"}, object()
        )
        assert "new file, 2 lines total" in reason and "+a" in reason

        # file_write over an existing file → unified diff against current.
        existing = tmp_path / "existing.py"
        existing.write_text("one\ntwo\n", encoding="utf-8")
        reason = _fix_approval_reason(
            "write_file", str(existing), {"content": "one\nTWO\n"}, object()
        )
        assert "-two" in reason and "+TWO" in reason and "(proposed)" in reason

        # Oversized diff → clipped with an ellipsis marker.
        big_old = "\n".join(f"line{i}" for i in range(60))
        big_new = "\n".join(f"line{i}!" for i in range(60))
        reason = _fix_approval_reason(
            "edit_file", None, {"old_str": big_old, "new_str": big_new}, object()
        )
        assert "more diff lines" in reason


class TestEscIntervention:
    """Esc intervention: pause at the round boundary → menu → action."""

    def _engine(self, tmp_path, responses, *, ask, policy, on_stream=None):
        return QueryEngine(
            api_client=FakeApiClient(responses, on_stream=on_stream),
            tool_registry=create_default_tool_registry(),
            permission_checker=PermissionChecker(
                PermissionSettings(mode=PermissionMode.FULL_AUTO)
            ),
            cwd=tmp_path,
            model="claude-sonnet-4-6",
            system_prompt="system",
            iterate_policy=policy,
            ask_user_prompt=ask,
        )

    @staticmethod
    def _pause_during_first_stream(policy):
        """Simulate Esc pressed mid-turn: pause set during the first stream."""
        calls = {"n": 0}

        def on_stream():
            calls["n"] += 1
            if calls["n"] == 1:
                policy.request_pause()

        return on_stream

    def _injected(self, engine):
        return [m.text for m in engine.messages if m.role == "user" and "[iterate]" in m.text]

    @pytest.mark.asyncio
    async def test_stop_answer_halts_loop(self, tmp_path):
        policy = IterateLoopPolicy(max_review_rounds=3)
        engine = self._engine(
            tmp_path,
            [
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[aggregate_call(AGGREGATE_ROUNDS_ACTIVE)]),
                    usage=UsageSnapshot(input_tokens=10, output_tokens=5),
                ),
                _FakeResponse(  # would run if the loop (wrongly) continued
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="unreachable")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ],
            ask=_a("x"),
            policy=policy,
            on_stream=self._pause_during_first_stream(policy),
        )
        events = [event async for event in engine.submit_message("run dry-run")]
        stops = [e for e in events if isinstance(e, StatusEvent) and "pause menu" in e.message]
        assert stops
        assert not any(
            isinstance(e, AssistantTurnComplete) and "unreachable" in e.message.text for e in events
        )

    @pytest.mark.asyncio
    async def test_skip_answer_injects_skip_instruction(self, tmp_path):
        policy = IterateLoopPolicy(max_review_rounds=3)
        engine = self._engine(
            tmp_path,
            [
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[aggregate_call(AGGREGATE_ROUNDS_ACTIVE)]),
                    usage=UsageSnapshot(input_tokens=10, output_tokens=5),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ],
            ask=_a("s"),
            policy=policy,
            on_stream=self._pause_during_first_stream(policy),
        )
        [event async for event in engine.submit_message("run dry-run")]
        injected = self._injected(engine)
        assert injected and "SKIP the current top finding" in injected[-1]

    @pytest.mark.asyncio
    async def test_narrow_answer_injects_narrow_instruction(self, tmp_path):
        policy = IterateLoopPolicy(max_review_rounds=3)
        engine = self._engine(
            tmp_path,
            [
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[aggregate_call(AGGREGATE_ROUNDS_ACTIVE)]),
                    usage=UsageSnapshot(input_tokens=10, output_tokens=5),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ],
            ask=_a("n security"),
            policy=policy,
            on_stream=self._pause_during_first_stream(policy),
        )
        [event async for event in engine.submit_message("run dry-run")]
        injected = self._injected(engine)
        assert injected and "NARROW" in injected[-1] and "security" in injected[-1]

    @pytest.mark.asyncio
    async def test_resume_answer_keeps_original_next_round(self, tmp_path):
        policy = IterateLoopPolicy(max_review_rounds=3)
        engine = self._engine(
            tmp_path,
            [
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[aggregate_call(AGGREGATE_ROUNDS_ACTIVE)]),
                    usage=UsageSnapshot(input_tokens=10, output_tokens=5),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ],
            ask=_a(""),
            policy=policy,
            on_stream=self._pause_during_first_stream(policy),
        )
        [event async for event in engine.submit_message("run dry-run")]
        injected = self._injected(engine)
        assert injected and "Round 1" in injected[-1] and "NARROW" not in injected[-1]

    @pytest.mark.asyncio
    async def test_headless_pause_defaults_to_stop(self, tmp_path):
        policy = IterateLoopPolicy(max_review_rounds=3)
        engine = self._engine(
            tmp_path,
            [
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[aggregate_call(AGGREGATE_ROUNDS_ACTIVE)]),
                    usage=UsageSnapshot(input_tokens=10, output_tokens=5),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="unreachable")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ],
            ask=None,
            policy=policy,
            on_stream=self._pause_during_first_stream(policy),
        )
        events = [event async for event in engine.submit_message("run dry-run")]
        stops = [e for e in events if isinstance(e, StatusEvent) and "pause menu" in e.message]
        assert stops

    @pytest.mark.asyncio
    async def test_intervention_decision_logged(self, tmp_path):
        from iterate_harness.iterate.decision_log import read_entries

        policy = IterateLoopPolicy(max_review_rounds=3)
        engine = self._engine(
            tmp_path,
            [
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[aggregate_call(AGGREGATE_ROUNDS_ACTIVE)]),
                    usage=UsageSnapshot(input_tokens=10, output_tokens=5),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ],
            ask=_a("x"),
            policy=policy,
            on_stream=self._pause_during_first_stream(policy),
        )
        [event async for event in engine.submit_message("run dry-run")]
        interventions = [
            e for e in read_entries(tmp_path) if e.type == "decision"
            and isinstance(e.data, dict) and e.data.get("kind") == "intervention"
        ]
        assert interventions and interventions[-1].data.get("action") == "stop"


def _a(answer: str):
    async def ask(question: str) -> str:
        return answer

    return ask


class TestRegistration:
    def test_six_iterate_tools_in_default_registry(self):
        registry = create_default_tool_registry()
        for name in (
            "iterate_config",
            "iterate_validate",
            "iterate_review",
            "iterate_decision_log",
            "iterate_context",
            "iterate_triage",
        ):
            assert registry.get(name) is not None, f"missing tool {name}"

    def test_iterate_slash_command_registered(self):
        from iterate_harness.commands.registry import create_default_command_registry

        registry = create_default_command_registry()
        commands = {c.name for c in registry.list_commands()}
        assert "iterate" in commands

    def test_iterate_bundled_skill_loaded(self):
        from iterate_harness.skills.bundled import get_bundled_skills

        names = {s.name for s in get_bundled_skills()}
        assert "iterate" in names
