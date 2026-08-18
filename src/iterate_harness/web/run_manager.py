"""Live iterate run manager for the WebUI chat panel (design §18).

This module bridges the headless engine runtime (:mod:`iterate_harness.ui.runtime`)
into the WebUI process so a loop can run *inside* the server, stream live
progress over SSE, and pause for human-in-the-loop decisions.

Roles:
- Own the single live run (design §18.3 single-run constraint) as a
  background asyncio task.
- Substitute the engine's three human-interaction channels
  (``permission_prompt`` / ``ask_user_prompt`` / ``ask_user_select``) with
  Web versions that broadcast the question over the hub and await the
  answer posted through :meth:`RunManager.send_message`.
- Persist a human-interaction-only chat transcript to
  ``.iterate/web-chat.jsonl`` (design §18.3) and broadcast ``chat-message`` /
  ``run-state`` / ``progress-update`` hub events for the SSE stream.

The engine itself is reused unchanged (plus a tiny nudge-injection hook on
:class:`~iterate_harness.iterate.loop_policy.IterateLoopPolicy`); no loop
logic is reimplemented here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .hub import hub
from .schemas import ChatRunStatus, RunState, WaitingKind

log = logging.getLogger(__name__)

#: How long a permission request waits before it is auto-denied (seconds).
_PERMISSION_TIMEOUT = 300.0

#: Max chat history entries returned by the REST endpoint.
_HISTORY_LIMIT = 500

#: Upper bound on the streaming assistant-text buffer per turn (chars).
#: Guards against unbounded memory growth if a turn never emits
#: ``AssistantTurnComplete``.
_ASSISTANT_BUFFER_LIMIT = 200_000

#: Exact single-token answers treated as approval of a permission request.
#: The input is normalized (stripped + lowercased) before this exact check.
_APPROVE_WORDS = frozenset(
    {
        "1", "true", "yes", "y", "approve", "allow", "ok",
        "批准", "同意", "确认", "允许", "可以", "好",
    }
)

#: Exact single-token answers treated as denial of a permission request.
_DENY_WORDS = frozenset(
    {
        "0", "false", "no", "n", "deny", "reject", "refuse",
        "拒绝", "不同意", "不允许", "不要", "否",
    }
)

#: Substrings that signal denial inside a longer free-text answer. Checked
#: before approval markers so a negated phrase ("不同意") always wins.
_DENY_MARKERS = frozenset(
    {
        "deny", "denied", "reject", "rejected", "refuse", "refused", "false",
        "never", "block", "blocked", "no", "not",
        "拒绝", "不同意", "不允许", "不要", "否", "不行",
    }
)

#: Substrings that signal approval inside a longer free-text answer.
_APPROVE_MARKERS = frozenset(
    {
        "approve", "allowed", "accepted", "confirm", "granted", "yes",
        "please", "go ahead", "continue",
        "好的", "同意", "批准", "确认", "允许", "可以", "好", "行", "对",
    }
)

#: Chinese negation prefixes that flip an approval-looking free-text answer.
_NEGATION_PREFIXES = ("不", "别", "没", "无", "非", "未")


class RunManagerError(Exception):
    """Rejected run operation with a human-readable message (maps to 4xx)."""


class RunManager:
    """Owns the single live iterate loop inside the WebUI process."""

    def __init__(self) -> None:
        self.state: RunState = "idle"
        self.run_id: str = ""
        self.mode: str = ""
        self.project_root: str = ""
        self.round: int = 0
        self.new_findings: int = 0
        self.total_findings: int = 0
        self.cost_usd: float = 0.0
        self.converged: bool = False
        self.waiting_for: WaitingKind = "none"
        self.question: str | None = None
        self.options: list[dict[str, Any]] | None = None
        self.permission_tool: str | None = None
        self.permission_reason: str | None = None
        self.error: str | None = None
        self.last_message: str = ""
        self._chat_dir: Path | None = None
        self._bundle: Any = None
        self._task: asyncio.Task[Any] | None = None
        self._request_registry: dict[str, asyncio.Future[Any]] = {}
        self._assistant_buffer: str = ""
        self._stopping: bool = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API (used by routes)
    # ------------------------------------------------------------------

    async def start(self, project_root: str, mode: str, changed: bool, ref: str) -> str:
        """Validate state and launch a new iterate loop in the background.

        Returns the new ``run_id``. Raises :class:`RunManagerError` when a
        run is already active or the kickoff cannot be built.
        """
        async with self._lock:
            if self.state in ("starting", "running", "paused"):
                raise RunManagerError("已有运行中的 iterate 循环，请先停止或等待结束")
            self._reset(project_root)
            run_id = uuid4().hex[:12]
            self.run_id = run_id
            self.mode = mode
            self.state = "starting"
        await self._publish_chat("system", f"收到启动指令：iterate {mode}", kind="status")
        self._task = asyncio.create_task(
            self._run_loop(project_root, mode, changed, ref, run_id)
        )
        return run_id

    async def send_message(self, content: str) -> dict[str, Any]:
        """Route one user chat input.

        - Paused waiting for input → answer the pending engine request.
        - Running → queue the input as a nudge injected at the next round
          boundary (design §18.1 "督促注入").
        """
        content = content.strip()
        if not content:
            raise RunManagerError("消息内容不能为空")
        async with self._lock:
            # Skip futures that are already resolved (e.g. a stop request
            # cancelled the run task before we got here): setting a result on
            # a done/cancelled future would raise InvalidStateError -> 500.
            pending = [f for f in self._request_registry.values() if not f.done()]
            waiting = self.waiting_for
        if pending:
            future = pending[0]
            if waiting == "permission":
                future.set_result(self._parse_permission(content))
                kind = "decision"
            elif waiting == "user_select":
                future.set_result(content)
                kind = "decision"
            else:
                future.set_result(content)
                kind = "answer"
            await self._publish_chat("user", content, kind=kind)
            return {"answered": True, "waitingFor": waiting}
        if self.state == "running":
            await self._nudge(content)
            return {"answered": True, "nudged": True}
        if self.state == "starting":
            raise RunManagerError("运行正在启动，请稍候再发送消息")
        raise RunManagerError("当前没有运行中的循环")

    async def control(self, action: str) -> dict[str, Any]:
        """Apply a run control command (pause / resume / stop)."""
        if action == "pause":
            return await self._pause()
        if action == "resume":
            return await self._resume()
        if action == "stop":
            return await self._stop()
        raise RunManagerError(f"未知控制动作：{action}")

    def status(self) -> ChatRunStatus:
        """Build the current run status snapshot (for the REST endpoint)."""
        waiting_for = self.waiting_for
        state: RunState = "paused" if (self.state == "running" and waiting_for != "none") else self.state
        permission: dict[str, Any] | None = None
        if waiting_for == "permission":
            permission = {
                "tool": self.permission_tool,
                "reason": self.permission_reason,
            }
        return ChatRunStatus(
            state=state,
            run_id=self.run_id,
            mode=self.mode,
            project_root=self.project_root,
            round=self.round,
            new_findings=self.new_findings,
            total_findings=self.total_findings,
            cost_usd=self.cost_usd,
            converged=self.converged,
            waiting_for=waiting_for,
            question=self.question,
            options=self.options if waiting_for == "user_select" else None,
            permission=permission,
            error=self.error,
            message=self.last_message,
        )

    def history(self) -> list[dict[str, Any]]:
        """Return the persisted human-interaction transcript (oldest first)."""
        path = self._chat_path
        if path is None or not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries[-_HISTORY_LIMIT:]

    async def reset(self) -> None:
        """Cancel any live run and return to idle (used by tests)."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Expected: the cancellation propagated into the run task.
                pass
            except Exception as exc:  # noqa: BLE001 - best-effort shutdown
                log.warning("reset: run task raised during cancellation: %s", exc)
        self._reset("")
        self.state = "idle"

    # ------------------------------------------------------------------
    # Background run loop
    # ------------------------------------------------------------------

    async def _run_loop(
        self, project_root: str, mode: str, changed: bool, ref: str, run_id: str
    ) -> None:
        bundle: Any = None
        # Capture the task this coroutine runs under so the finally block only
        # clears references that still point at *this* run. A fresh run started
        # immediately after this one ends must not have its handle clobbered.
        task_handle = asyncio.current_task()
        try:
            kickoff, rounds = self._build_kickoff(project_root, mode, changed, ref)
            try:
                from iterate_harness.iterate.onboard_cmd import (
                    ensure_onboarding_fingerprints,
                    warn_if_drifted,
                )

                ensure_onboarding_fingerprints(Path(project_root))
                warn_if_drifted(Path(project_root))
            except Exception as exc:  # noqa: BLE001 - preflight is best-effort
                log.warning("iterate onboarding preflight failed: %s", exc)

            from iterate_harness.ui.runtime import (
                build_runtime,
                close_runtime,
                start_runtime,
            )

            bundle = await build_runtime(
                prompt=kickoff,
                cwd=project_root,
                permission_prompt=self._permission_prompt,
                ask_user_prompt=self._ask_user_prompt,
                ask_user_select=self._ask_user_select,
                permission_mode="full_auto",
            )
            self._bundle = bundle
            await start_runtime(bundle)
            await self._set_state("running", message=f"iterate {mode} 运行中（rounds ≤ {rounds}）")
            await self._publish_chat(
                "system", f"启动 iterate {mode} 循环，rounds ≤ {rounds}", kind="status"
            )
            async for event in bundle.engine.submit_message(kickoff):
                await self._render_event(event)
            await self._publish_chat("system", "iterate 循环已结束", kind="status")
            await self._set_state("stopped", message="iterate 循环已结束")
        except asyncio.CancelledError:
            await self._publish_chat("system", "运行已被用户停止", kind="error")
            await self._set_state("stopped", message="运行已被用户停止")
            raise
        except SystemExit as exc:
            message = f"引擎无法启动：{exc}"
            log.error("iterate web run aborted on engine startup: %s", exc)
            self.error = message
            await self._publish_chat("system", message, kind="error")
            await self._set_state("stopped", message=message)
        except Exception as exc:  # noqa: BLE001 - report any engine failure
            log.exception("iterate web run failed (run_id=%s)", run_id)
            self.error = str(exc)
            await self._publish_chat("system", f"运行失败：{exc}", kind="error")
            await self._set_state("stopped", message="运行失败")
        finally:
            # Flush any assistant text that was still buffered when the loop
            # ended — a turn interrupted by an error, an early stop, or a
            # system-exit would otherwise drop the model's partial output with
            # no trace (no-op when the last turn completed and was already
            # flushed).
            await self._flush_assistant_buffer()
            if bundle is not None:
                try:
                    await close_runtime(bundle)
                except Exception as exc:  # noqa: BLE001 - best-effort close
                    log.warning("iterate runtime close failed: %s", exc)
            async with self._lock:
                # Only clear references that still belong to *this* run; a new
                # run may already own the manager by the time we get here.
                if self._bundle is bundle:
                    self._bundle = None
                if self._task is task_handle:
                    self._task = None
                self._stopping = False

    async def _render_event(self, event: Any) -> None:
        """Translate engine stream events into chat/progress hub events."""
        from iterate_harness.engine.stream_events import (
            AssistantTextDelta,
            AssistantTurnComplete,
            CompactProgressEvent,
            ErrorEvent,
            ReviewProgressEvent,
            StatusEvent,
            ToolExecutionCompleted,
            ToolExecutionStarted,
        )

        if isinstance(event, ReviewProgressEvent):
            async with self._lock:
                self.round = event.round
                self.new_findings = event.new_findings
                self.total_findings = event.total_findings
                self.cost_usd = event.cost_usd
                self.converged = event.converged
            summary = (
                f"iterate {event.mode} 第 {event.round} 轮：+{event.new_findings} "
                f"个 findings（累计 {event.total_findings}），成本 ${event.cost_usd:.4f}"
                + (" — 已收敛" if event.converged else "")
            )
            await self._publish_chat("system", summary, kind="progress")
            await hub.publish(
                "progress-update",
                {
                    "round": event.round,
                    "newFindings": event.new_findings,
                    "totalFindings": event.total_findings,
                    "costUsd": event.cost_usd,
                    "converged": event.converged,
                    "mode": event.mode,
                },
            )
        elif isinstance(event, AssistantTextDelta):
            remaining = _ASSISTANT_BUFFER_LIMIT - len(self._assistant_buffer)
            if remaining > 0:
                self._assistant_buffer += event.text[:remaining]
        elif isinstance(event, AssistantTurnComplete):
            await self._flush_assistant_buffer()
        elif isinstance(event, StatusEvent):
            await self._publish_chat("system", event.message, kind="status")
        elif isinstance(event, ErrorEvent):
            await self._publish_chat("system", f"错误：{event.message}", kind="error")
        elif isinstance(event, CompactProgressEvent):
            if event.message:
                await self._publish_chat("system", event.message, kind="status")
        elif isinstance(event, ToolExecutionStarted):
            await self._publish_tool(f"▶ 调用工具 {event.tool_name}")
        elif isinstance(event, ToolExecutionCompleted):
            preview = event.output if isinstance(event.output, str) else str(event.output or "")
            preview = " ".join(preview.strip().split())[:120]
            if event.is_error:
                await self._publish_tool(f"✖ {event.tool_name}：{preview}")
            else:
                await self._publish_tool(f"✔ {event.tool_name}：{preview}")

    # ------------------------------------------------------------------
    # Engine interaction channels (human-in-the-loop)
    # ------------------------------------------------------------------

    async def _permission_prompt(self, tool_name: str, reason: str) -> bool:
        request_id = uuid4().hex
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._request_registry[request_id] = future
            self.waiting_for = "permission"
            self.permission_tool = tool_name
            self.permission_reason = reason
            self.state = "paused"
        await self._publish_chat(
            "assistant", f"请求权限：{tool_name} — {reason}", kind="permission"
        )
        await hub.publish(
            "run-state",
            {
                "state": "paused",
                "waitingFor": "permission",
                "tool": tool_name,
                "reason": reason,
            },
        )
        try:
            return await asyncio.wait_for(future, timeout=_PERMISSION_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("permission request %s timed out; denying", request_id)
            return False
        finally:
            async with self._lock:
                self._request_registry.pop(request_id, None)
                self.waiting_for = "none"
                self.permission_tool = None
                self.permission_reason = None
                if not self._stopping:
                    self.state = "running"
            await hub.publish("run-state", {"state": "running", "waitingFor": "none"})

    async def _ask_user_prompt(self, question: str) -> str:
        request_id = uuid4().hex
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._request_registry[request_id] = future
            self.waiting_for = "user_prompt"
            self.question = question
            self.state = "paused"
        await self._publish_chat("assistant", question, kind="question")
        await hub.publish(
            "run-state", {"state": "paused", "waitingFor": "user_prompt", "question": question}
        )
        try:
            return await future
        finally:
            async with self._lock:
                self._request_registry.pop(request_id, None)
                self.waiting_for = "none"
                self.question = None
                if not self._stopping:
                    self.state = "running"
            await hub.publish("run-state", {"state": "running", "waitingFor": "none"})

    async def _ask_user_select(self, title: str, options: list[dict[str, Any]]) -> str:
        # A stop request while running resolves here: the engine reaches the
        # pause menu at the round boundary and we short-circuit it with the
        # engine's own clean "stop" action (worktree rollback + final report).
        async with self._lock:
            if self._stopping:
                self._stopping = False
                return "stop"
            request_id = uuid4().hex
            future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            self._request_registry[request_id] = future
            self.waiting_for = "user_select"
            self.question = title
            self.options = list(options)
            self.state = "paused"
        await self._publish_chat("assistant", title, kind="select")
        await hub.publish(
            "run-state",
            {
                "state": "paused",
                "waitingFor": "user_select",
                "question": title,
                "options": options,
            },
        )
        try:
            return await future
        finally:
            async with self._lock:
                self._request_registry.pop(request_id, None)
                self.waiting_for = "none"
                self.question = None
                self.options = None
                if not self._stopping:
                    self.state = "running"
            await hub.publish("run-state", {"state": "running", "waitingFor": "none"})

    # ------------------------------------------------------------------
    # Control operations
    # ------------------------------------------------------------------

    async def _pause(self) -> dict[str, Any]:
        async with self._lock:
            if self.state != "running" or self.waiting_for != "none":
                raise RunManagerError("当前状态无法暂停（仅在运行中可暂停）")
            policy = self._policy()
            if policy is None:
                raise RunManagerError("运行尚未就绪，无法暂停")
            pause_requested = getattr(policy, "request_pause", None)
            if not callable(pause_requested):
                raise RunManagerError("当前运行不支持暂停")
            pause_requested()
            self.last_message = "已请求暂停，将在下一轮边界生效"
        await self._publish_chat("system", "已请求暂停，将在下一轮边界生效", kind="status")
        return {"ok": True, "message": "已请求暂停，将在下一轮边界生效"}

    async def _resume(self) -> dict[str, Any]:
        async with self._lock:
            if self.waiting_for != "user_select":
                raise RunManagerError("当前没有可恢复的暂停菜单（请直接回答问题或选择）")
            pending = list(self._request_registry.values())
        if not pending:
            raise RunManagerError("暂停请求已失效")
        pending[0].set_result("resume")
        await self._publish_chat("user", "resume", kind="decision")
        return {"ok": True, "message": "已继续运行"}

    async def _stop(self) -> dict[str, Any]:
        async with self._lock:
            pending = list(self._request_registry.values())
            waiting = self.waiting_for
            self._stopping = True
            self.last_message = "正在停止…"
        if pending:
            if waiting == "user_select":
                pending[0].set_result("stop")
                return {"ok": True, "message": "正在停止…"}
            # A question/permission is pending with no clean answer channel:
            # abort the run task (engine state is checkpointed for resume).
            task = self._task
            if task is not None and not task.done():
                task.cancel()
            return {"ok": True, "message": "正在停止…"}
        policy = self._policy()
        if policy is not None:
            pause_requested = getattr(policy, "request_pause", None)
            if callable(pause_requested):
                pause_requested()
                return {"ok": True, "message": "已请求停止，将在下一轮边界生效"}
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        return {"ok": True, "message": "正在停止…"}

    async def _nudge(self, content: str) -> None:
        policy = self._policy()
        if policy is None:
            raise RunManagerError("运行尚未就绪，无法注入督促")
        inject_nudge = getattr(policy, "inject_nudge", None)
        if not callable(inject_nudge):
            raise RunManagerError("当前运行不支持督促注入")
        inject_nudge(content)
        await self._publish_chat("user", content, kind="nudge")
        await self._publish_chat("system", "已收到督促，将在下一轮边界注入循环", kind="status")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _chat_path(self) -> Path | None:
        if self._chat_dir is None:
            return None
        return self._chat_dir / "web-chat.jsonl"

    def _policy(self) -> Any:
        bundle = self._bundle
        if bundle is None:
            return None
        return getattr(bundle.engine, "iterate_policy", None)

    def _parse_permission(self, content: str) -> bool:
        """Parse permission approval from user input.

        Strategy:
        1) Exact match: single token matches one of the predefined single-word
           yes/no lists → return immediately.
        2) Free text: check for denial markers first (negations always win),
           then check for approval markers, defaulting to False if neither is
           clearly present.
        3) Chinese negation prefixes (不/别/没…) flip a yes into no.
        """
        normalized = content.strip().lower()
        # 1. Exact single-word match (fast + handles the quick-button case)
        if normalized in _APPROVE_WORDS:
            return True
        if normalized in _DENY_WORDS:
            return False

        # 2. Check for denial markers first — a single explicit negation wins
        # over any approval mention.
        has_deny = any(marker in normalized for marker in _DENY_MARKERS)
        if has_deny:
            return False

        # 3. Check for Chinese negation prefixes at word start.
        # Cases like "不同意" → even though it contains "同意", the "不"
        # at the start flips it to denial.
        words = normalized.split()
        for word in words:
            for neg_prefix in _NEGATION_PREFIXES:
                if word.startswith(neg_prefix):
                    # If the remainder looks like approval, this is a negated
                    # approval → still deny.
                    return False

        # 4. Check for any approval marker.
        has_approve = any(marker in normalized for marker in _APPROVE_MARKERS)
        if has_approve:
            return True

        # 5. Default: no clear signal → safer to deny.
        return False

    def _build_kickoff(
        self, project_root: str, mode: str, changed: bool, ref: str
    ) -> tuple[str, int]:
        """Assemble the canonical kickoff prompt (mirrors the CLI path)."""
        from iterate_harness.config.settings import load_settings
        from iterate_harness.iterate.prompts import (
            dry_run_kickoff,
            normal_kickoff,
            resume_kickoff,
        )
        from iterate_harness.iterate.settings import effective_review_rounds, project_config

        effective = project_config(project_root)
        kernel = load_settings().iterate
        rounds = effective_review_rounds(kernel, effective)
        goal = effective.config.goal
        if mode == "review":
            changed_files = self._changed_files(project_root, changed, ref)
            return dry_run_kickoff(goal, rounds, changed_files, cwd=project_root), rounds
        if mode == "run":
            changed_files = self._changed_files(project_root, changed, ref)
            return normal_kickoff(goal, rounds, changed_files, cwd=project_root), rounds
        # resume
        from iterate_harness.iterate.last_state import summarize_last_run

        summary = summarize_last_run(project_root)
        if not summary:
            raise RunManagerError("没有可恢复的上一次运行记录")
        return resume_kickoff(goal, rounds, summary), rounds

    @staticmethod
    def _changed_files(project_root: str, changed: bool, ref: str) -> list[str] | None:
        if not changed:
            return None
        from iterate_harness.iterate import git_scope

        try:
            files = git_scope.collect_changed_files(project_root, ref)
        except ValueError as exc:
            raise RunManagerError(f"无效的 --ref：{exc}") from exc
        if not files:
            raise RunManagerError(f"相对 {ref} 没有变更文件（工作区干净）")
        return files

    async def _flush_assistant_buffer(self) -> None:
        """Publish any buffered assistant text as a chat message (no-op if empty)."""
        text = self._assistant_buffer.strip()
        self._assistant_buffer = ""
        if text:
            await self._publish_chat("assistant", text, kind="text")

    async def _set_state(self, state: RunState, *, message: str = "") -> None:
        async with self._lock:
            self.state = state
            if message:
                self.last_message = message
        await hub.publish("run-state", {"state": state, "message": message})

    async def _publish_chat(
        self, role: str, content: str, kind: str = "text"
    ) -> dict[str, Any]:
        entry = {
            "id": uuid4().hex,
            "role": role,
            "kind": kind,
            "content": content,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        path = self._chat_path
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError as exc:
                log.warning("web chat history write failed: %s", exc)
        await hub.publish("chat-message", entry)
        return entry

    async def _publish_tool(self, content: str) -> None:
        # Live tool activity is broadcast but never persisted (ephemeral).
        await hub.publish(
            "chat-message",
            {
                "id": uuid4().hex,
                "role": "system",
                "kind": "tool",
                "content": content,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    def _reset(self, project_root: str) -> None:
        self.run_id = ""
        self.mode = ""
        self.project_root = project_root
        self.round = 0
        self.new_findings = 0
        self.total_findings = 0
        self.cost_usd = 0.0
        self.converged = False
        self.waiting_for = "none"
        self.question = None
        self.options = None
        self.permission_tool = None
        self.permission_reason = None
        self.error = None
        self.last_message = ""
        self._chat_dir = Path(project_root) / ".iterate" if project_root else None
        self._bundle = None
        self._request_registry = {}
        self._assistant_buffer = ""
        self._stopping = False


#: Module-level singleton shared by the route layer (design §18.3).
run_manager = RunManager()


__all__ = ["RunManager", "RunManagerError", "run_manager"]
