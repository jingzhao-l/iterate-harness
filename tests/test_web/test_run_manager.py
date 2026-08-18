"""Unit tests for the WebUI RunManager (design §18.3).

These tests exercise the manager's deterministic logic without spinning up a
real LLM engine: permission parsing, status snapshot mapping, send-message
routing, human-interaction channels (permission / prompt / select), error
paths, and chat-history persistence.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iterate_harness.web.run_manager import RunManager, RunManagerError


@pytest.fixture()
async def manager():
    mgr = RunManager()
    yield mgr
    await mgr.reset()


# ---------------------------------------------------------------------------
# Permission parsing
# ---------------------------------------------------------------------------


class TestParsePermission:
    @pytest.mark.parametrize(
        "content",
        ["1", "true", "yes", "y", "approve", "allow", "ok", "批准", "同意", "确认", "允许"],
    )
    def test_approve_words(self, content: str):
        assert RunManager()._parse_permission(content) is True

    @pytest.mark.parametrize(
        "content",
        ["0", "false", "no", "n", "deny", "reject", "拒绝", "不同意", "不允许", "", "whatever", "  "],
    )
    def test_deny_words_and_unknown(self, content: str):
        assert RunManager()._parse_permission(content) is False

    def test_case_insensitive(self):
        mgr = RunManager()
        assert mgr._parse_permission("YES") is True
        assert mgr._parse_permission("Approve") is True
        assert mgr._parse_permission("No") is False

    def test_whitespace_stripped(self):
        mgr = RunManager()
        assert mgr._parse_permission("  yes  ") is True

    def test_natural_language_approval(self):
        mgr = RunManager()
        assert mgr._parse_permission("我同意执行这个操作") is True
        assert mgr._parse_permission("当然可以，继续吧") is True
        assert mgr._parse_permission("yes please") is True
        assert mgr._parse_permission("please go ahead") is True

    def test_natural_language_denial(self):
        mgr = RunManager()
        assert mgr._parse_permission("不同意，先别执行") is False
        assert mgr._parse_permission("不批准") is False
        assert mgr._parse_permission("not yet, please hold") is False
        assert mgr._parse_permission("不行") is False


# ---------------------------------------------------------------------------
# Tool event stream (design §18: live tool-timeline cards)
# ---------------------------------------------------------------------------


class TestToolEvents:
    async def test_tool_events_publish_truthful_markers(
        self, manager: RunManager
    ):
        """Tool start / success publish the ▶/✔ markers the frontend parses into
        tool-timeline cards. A failing tool (is_error) must publish the ✖ marker
        so the live timeline never shows a successful check for a failed call."""
        from iterate_harness.engine.stream_events import (
            ToolExecutionCompleted,
            ToolExecutionStarted,
        )
        from iterate_harness.web.hub import hub

        queue = await hub.subscribe()
        try:
            await manager._render_event(
                ToolExecutionStarted(tool_name="iterate_review", tool_input={})
            )
            await manager._render_event(
                ToolExecutionCompleted(
                    tool_name="iterate_review",
                    output="  ok:\n  0 findings  ",
                    is_error=False,
                )
            )
            await manager._render_event(
                ToolExecutionCompleted(
                    tool_name="iterate_fix", output="boom", is_error=True
                )
            )
        finally:
            await hub.unsubscribe(queue)

        contents = []
        while not queue.empty():
            event = queue.get_nowait()
            if event.type == "chat-message" and event.data.get("kind") == "tool":
                contents.append(event.data["content"])

        assert any(c.startswith("▶ 调用工具 iterate_review") for c in contents)
        # Output is normalized (whitespace collapsed + truncated).
        assert any(c == "✔ iterate_review：ok: 0 findings" for c in contents)
        assert any(c == "✖ iterate_fix：boom" for c in contents)
        # No false success marker for the failed call.
        assert not any(c.startswith("✔ iterate_fix") for c in contents)


# ---------------------------------------------------------------------------
# Assistant text buffering (design §18: model output is flushed per turn)
# ---------------------------------------------------------------------------


class TestAssistantBuffer:
    async def _flush_and_collect_text(self, manager: RunManager) -> list[str]:
        from iterate_harness.web.hub import hub

        queue = await hub.subscribe()
        try:
            await manager._flush_assistant_buffer()
        finally:
            await hub.unsubscribe(queue)
        texts: list[str] = []
        while not queue.empty():
            event = queue.get_nowait()
            if event.type == "chat-message" and event.data.get("kind") == "text":
                texts.append(event.data["content"])
        return texts

    async def test_flush_publishes_partial_text(self, manager: RunManager):
        """Buffered assistant text that is never wrapped by an
        ``AssistantTurnComplete`` (e.g. the run errored mid-turn) must still
        reach the chat panel instead of being silently dropped."""
        manager._assistant_buffer = "partially generated output"
        texts = await self._flush_and_collect_text(manager)
        assert texts == ["partially generated output"]
        # Buffer was drained so it cannot double-publish on the next flush.
        assert manager._assistant_buffer == ""

    async def test_flush_noop_when_empty(self, manager: RunManager):
        assert await self._flush_and_collect_text(manager) == []

    async def test_turn_complete_flushes_buffer_as_text(self, manager: RunManager):
        """A completed assistant turn drains the running buffer into one
        assistant text message (regression guard for the flush path)."""
        from iterate_harness.engine.stream_events import AssistantTurnComplete
        from iterate_harness.web.hub import hub

        manager._assistant_buffer = "summary of findings"
        queue = await hub.subscribe()
        try:
            await manager._render_event(
                AssistantTurnComplete(message=None, usage=None)  # type: ignore[arg-type]
            )
        finally:
            await hub.unsubscribe(queue)
        texts: list[str] = []
        while not queue.empty():
            event = queue.get_nowait()
            if event.type == "chat-message" and event.data.get("kind") == "text":
                texts.append(event.data["content"])
        assert texts == ["summary of findings"]
        assert manager._assistant_buffer == ""


class TestStatus:
    def test_idle_default(self):
        status = RunManager().status()
        assert status.state == "idle"
        assert status.run_id == ""
        assert status.waiting_for == "none"
        assert status.options is None
        assert status.permission is None

    def test_running_with_pending_input_maps_to_paused(self):
        mgr = RunManager()
        mgr.state = "running"
        mgr.waiting_for = "user_prompt"
        mgr.question = "give me details"
        status = mgr.status()
        assert status.state == "paused"
        assert status.waiting_for == "user_prompt"
        assert status.question == "give me details"
        assert status.options is None

    def test_running_clean_stays_running(self):
        mgr = RunManager()
        mgr.state = "running"
        mgr.run_id = "abc123"
        mgr.mode = "run"
        mgr.round = 3
        status = mgr.status()
        assert status.state == "running"
        assert status.run_id == "abc123"
        assert status.round == 3

    def test_options_only_for_user_select(self):
        mgr = RunManager()
        mgr.state = "running"
        mgr.waiting_for = "user_select"
        mgr.options = [{"value": "continue", "label": "继续", "description": "keep going"}]
        status = mgr.status()
        assert status.state == "paused"
        assert status.options is not None
        assert status.options[0].value == "continue"
        assert status.options[0].label == "继续"

    def test_permission_payload(self):
        mgr = RunManager()
        mgr.state = "running"
        mgr.waiting_for = "permission"
        mgr.permission_tool = "bash"
        mgr.permission_reason = "run pytest"
        status = mgr.status()
        assert status.waiting_for == "permission"
        assert status.permission == {"tool": "bash", "reason": "run pytest"}

    def test_options_hidden_for_permission(self):
        mgr = RunManager()
        mgr.state = "running"
        mgr.waiting_for = "permission"
        mgr.options = [{"value": "x", "label": "y"}]
        status = mgr.status()
        assert status.options is None


# ---------------------------------------------------------------------------
# send_message routing
# ---------------------------------------------------------------------------


class TestSendMessage:
    async def test_empty_message_rejected(self, manager: RunManager):
        with pytest.raises(RunManagerError):
            await manager.send_message("   ")

    async def test_no_run_rejected(self, manager: RunManager):
        with pytest.raises(RunManagerError):
            await manager.send_message("hello")

    async def test_starting_state_rejected(self, manager: RunManager):
        manager.state = "starting"
        with pytest.raises(RunManagerError):
            await manager.send_message("hello")

    async def test_nudge_injects_into_running_loop(self, manager: RunManager):
        injected: list[str] = []

        class FakePolicy:
            def inject_nudge(self, content: str) -> None:
                injected.append(content)

        class FakeEngine:
            iterate_policy = FakePolicy()

        class FakeBundle:
            engine = FakeEngine()

        manager.state = "running"
        manager._bundle = FakeBundle()
        result = await manager.send_message("快点干")
        assert result == {"answered": True, "nudged": True}
        assert injected == ["快点干"]

    async def test_answer_pending_prompt(self, manager: RunManager):
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        manager.state = "paused"
        manager.waiting_for = "user_prompt"
        manager._request_registry["r1"] = future

        result = await manager.send_message("answer-here")
        assert result == {"answered": True, "waitingFor": "user_prompt"}
        assert await asyncio.wait_for(future, timeout=0.5) == "answer-here"

    async def test_answer_pending_select(self, manager: RunManager):
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        manager.state = "paused"
        manager.waiting_for = "user_select"
        manager._request_registry["r1"] = future

        result = await manager.send_message("resume")
        assert result["waitingFor"] == "user_select"
        assert await asyncio.wait_for(future, timeout=0.5) == "resume"

    async def test_answer_pending_permission_parsed(self, manager: RunManager):
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        manager.state = "paused"
        manager.waiting_for = "permission"
        manager._request_registry["r1"] = future

        await manager.send_message("approve")
        assert await asyncio.wait_for(future, timeout=0.5) is True

    async def test_stale_resolved_future_skipped(self, manager: RunManager):
        """A stop request cancelling the run leaves a *done* future in the
        registry; sending a message afterwards must not call set_result on it
        (that would raise InvalidStateError -> HTTP 500)."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        future.set_result("stop")  # already resolved by the stop path
        manager.state = "paused"
        manager.waiting_for = "user_select"
        manager._request_registry["stale"] = future

        with pytest.raises(RunManagerError):
            await manager.send_message("你好")
        # The stale entry stays untouched (still holds its result).
        assert future.result() == "stop"


# ---------------------------------------------------------------------------
# Human-interaction channels (engine callbacks)
# ---------------------------------------------------------------------------


class TestHumanChannels:
    async def test_permission_prompt_approved_via_send_message(self, manager: RunManager):
        task = asyncio.create_task(manager._permission_prompt("bash", "run pytest"))

        # Wait until the manager is actually waiting for a decision.
        for _ in range(50):
            if manager.waiting_for == "permission":
                break
            await asyncio.sleep(0.01)
        assert manager.state == "paused"
        assert manager.permission_tool == "bash"

        await manager.send_message("yes")
        assert await asyncio.wait_for(task, timeout=1.0) is True
        assert manager.waiting_for == "none"
        assert manager.state == "running"

    async def test_permission_prompt_denied(self, manager: RunManager):
        task = asyncio.create_task(manager._permission_prompt("bash", "run rm -rf"))
        for _ in range(50):
            if manager.waiting_for == "permission":
                break
            await asyncio.sleep(0.01)
        await manager.send_message("no")
        assert await asyncio.wait_for(task, timeout=1.0) is False

    async def test_ask_user_prompt_collects_answer(self, manager: RunManager):
        task = asyncio.create_task(manager._ask_user_prompt("补充一下验收标准？"))
        for _ in range(50):
            if manager.waiting_for == "user_prompt":
                break
            await asyncio.sleep(0.01)
        assert manager.question == "补充一下验收标准？"
        await manager.send_message("用单元测试覆盖")
        assert await asyncio.wait_for(task, timeout=1.0) == "用单元测试覆盖"

    async def test_ask_user_select_collects_option(self, manager: RunManager):
        options = [
            {"value": "continue", "label": "继续", "description": "keep"},
            {"value": "stop", "label": "停止", "description": "halt"},
        ]
        task = asyncio.create_task(manager._ask_user_select("下一轮怎么走？", options))
        for _ in range(50):
            if manager.waiting_for == "user_select":
                break
            await asyncio.sleep(0.01)
        assert manager.options == options
        await manager.send_message("stop")
        assert await asyncio.wait_for(task, timeout=1.0) == "stop"


# ---------------------------------------------------------------------------
# Control operations
# ---------------------------------------------------------------------------


class TestControl:
    async def test_unknown_action_rejected(self, manager: RunManager):
        with pytest.raises(RunManagerError):
            await manager.control("explode")

    async def test_pause_requires_running(self, manager: RunManager):
        manager.state = "idle"
        with pytest.raises(RunManagerError):
            await manager.control("pause")

    async def test_pause_requests_boundary_pause(self, manager: RunManager):
        class FakePolicy:
            def __init__(self) -> None:
                self.paused = False

            def request_pause(self) -> None:
                self.paused = True

        class FakeEngine:
            iterate_policy = FakePolicy()

        class FakeBundle:
            engine = FakeEngine()

        manager.state = "running"
        manager.waiting_for = "none"
        manager._bundle = FakeBundle()
        result = await manager.control("pause")
        assert result["ok"] is True
        assert manager._bundle.engine.iterate_policy.paused is True

    async def test_resume_requires_select_menu(self, manager: RunManager):
        manager.waiting_for = "none"
        with pytest.raises(RunManagerError):
            await manager.control("resume")

    async def test_resume_resolves_pending_select(self, manager: RunManager):
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        manager.waiting_for = "user_select"
        manager._request_registry["r1"] = future
        result = await manager.control("resume")
        assert result["ok"] is True
        assert await asyncio.wait_for(future, timeout=0.5) == "resume"


# ---------------------------------------------------------------------------
# Chat history persistence
# ---------------------------------------------------------------------------


class TestHistory:
    async def test_history_empty_without_chat_dir(self, manager: RunManager):
        assert manager.history() == []

    async def test_history_persists_and_reads(self, manager: RunManager, tmp_path):
        manager._chat_dir = tmp_path / ".iterate"
        await manager._publish_chat("user", "继续", kind="decision")
        await manager._publish_chat("assistant", "好的", kind="text")

        entries = manager.history()
        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[0]["content"] == "继续"
        assert entries[1]["role"] == "assistant"

        # File on disk is append-only JSONL.
        lines = (tmp_path / ".iterate" / "web-chat.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        for line in lines:
            assert json.loads(line)["id"]

    async def test_history_skips_corrupt_lines(self, manager: RunManager, tmp_path):
        chat_dir = tmp_path / ".iterate"
        chat_dir.mkdir(parents=True, exist_ok=True)
        (chat_dir / "web-chat.jsonl").write_text(
            "{bad json}\n" + json.dumps({"role": "user", "content": "ok", "id": "1", "timestamp": "t", "kind": "text"}) + "\n",
            encoding="utf-8",
        )
        manager._chat_dir = chat_dir
        entries = manager.history()
        assert len(entries) == 1
        assert entries[0]["content"] == "ok"


# ---------------------------------------------------------------------------
# Lifecycle / reset
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_start_rejects_when_active(self, manager: RunManager, tmp_path):
        manager.state = "running"
        with pytest.raises(RunManagerError):
            await manager.start(str(tmp_path), "run", False, "HEAD")

    async def test_reset_returns_to_idle(self, manager: RunManager):
        manager.state = "running"
        manager.run_id = "xyz"
        manager.waiting_for = "permission"
        await manager.reset()
        assert manager.state == "idle"
        assert manager.run_id == ""

    async def test_reset_logs_exception_raised_by_run_task(self, manager: RunManager, caplog):
        """If cancelling the live run task surfaces a non-CancelledError, reset()
        must log the failure instead of silently swallowing it."""

        class FailingTask:
            def done(self):
                return False

            def cancel(self):
                pass

            def __await__(self):
                async def inner() -> None:
                    raise RuntimeError("boom during cancellation")

                return inner().__await__()

        manager._task = FailingTask()  # type: ignore[assignment]
        with caplog.at_level("WARNING"):
            await manager.reset()
        assert any(
            "reset: run task raised during cancellation" in record.message
            and "boom during cancellation" in record.message
            for record in caplog.records
        )
        assert manager.state == "idle"

    def test_reset_recomputes_chat_dir(self, tmp_path):
        mgr = RunManager()
        mgr._reset(str(tmp_path))
        assert mgr._chat_dir == tmp_path / ".iterate"
        assert mgr.state == "idle"
