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


# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------


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

    def test_reset_recomputes_chat_dir(self, tmp_path):
        mgr = RunManager()
        mgr._reset(str(tmp_path))
        assert mgr._chat_dir == tmp_path / ".iterate"
        assert mgr.state == "idle"
