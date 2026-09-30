"""Chat / human-in-the-loop routes (design §18.3).

Bridges the live iterate loop (:class:`~iterate_harness.web.run_manager.RunManager`)
into the WebUI REST surface:

- ``POST /chat/start`` — launch an iterate loop inside the server process.
- ``GET  /chat/status`` — current run-state snapshot for the chat panel.
- ``GET  /chat/history`` — persisted human-interaction transcript.
- ``POST /chat/message`` — send a chat message (answer a pending request, or
  nudge a running loop).
- ``POST /chat/control`` — pause / resume / stop the loop.
- ``POST /chat/reset`` — cancel any live run and return to idle (safety hatch;
  mutating, so it requires ``confirm=true`` and is audited).

Live progress / run-state transitions / chat messages are pushed over the
SSE stream via the in-process :mod:`~iterate_harness.web.hub` (see
:mod:`..events`), so the frontend stays in sync without polling.
"""

from __future__ import annotations

import asyncio

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..run_manager import RunManagerError, run_manager
from ..security import AuditLog, allowed_roots, root_is_allowed
from ..schemas import (
    ChatMessage,
    ChatRunStatus,
    ControlRequest,
    SendMessageRequest,
    StartRequest,
)

router = APIRouter(tags=["chat"])


def _resolve_project(request: Request, project_root: str) -> str:
    """Resolve the target project root: explicit query arg > app state > CWD."""
    resolved = project_root.strip() if project_root else ""
    if not resolved:
        state_root = getattr(request.app.state, "project_root", None)
        resolved = str(state_root) if state_root else ""
    if not resolved:
        resolved = str(Path.cwd())
    root = Path(resolved)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    # ``project_root`` is caller-controlled, so containment inside the root is
    # not enough on its own: without this the parameter *selects* the root, and
    # a valid token granted read/write over any directory on the machine.
    if not root_is_allowed(root):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Project root is outside the roots this WebUI serves: {root}. "
                f"Allowed: {', '.join(sorted(allowed_roots())) or '(none)'}"
            ),
        )
    return str(root.resolve())


@router.post("/chat/start", response_model=dict[str, str])
async def start_run(
    body: StartRequest,
    request: Request,
    project_root: str = Query("", description="Project root (defaults to app/CWD)"),
) -> dict[str, str]:
    """Launch a new iterate loop in the server process (review/run/resume).

    Body: ``{"mode": "review"|"run"|"resume", "changed": bool, "ref": "HEAD"}``.
    Raises ``409`` when a run is already active; ``400`` on invalid input
    (bad ref, clean worktree, no prior run for resume).
    """
    root = _resolve_project(request, project_root)
    try:
        run_id = await run_manager.start(
            root, body.mode, body.changed, body.ref, body.permission_mode
        )
    except RunManagerError as exc:
        status = 409 if "运行中" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"runId": run_id, "status": run_manager.state}


@router.get("/chat/status", response_model=ChatRunStatus)
async def chat_status() -> ChatRunStatus:
    """Live run-state snapshot for the chat panel.

    Includes the cross-process run lease probe, so a project the console is
    iterating reports its real driver instead of a misleading ``idle``.
    """
    return await run_manager.astatus()


@router.get("/chat/history", response_model=list[ChatMessage])
async def chat_history() -> list[ChatMessage]:
    """Persisted human-interaction transcript (oldest first, capped)."""
    # Off the loop: history() reads and parses the whole web-chat.jsonl, and a
    # blocking read in a coroutine stalls every concurrent stream/run.
    entries = await asyncio.to_thread(run_manager.history)
    messages: list[ChatMessage] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            messages.append(ChatMessage(**entry))
        except (TypeError, ValueError):
            continue
    return messages


@router.post("/chat/message", response_model=dict[str, Any])
async def send_message(
    body: SendMessageRequest,
) -> dict[str, Any]:
    """Send one chat message: answers a pending engine request, or nudges a
    running loop at the next round boundary (design §18.1 督促注入)."""
    try:
        return await run_manager.send_message(body.content)
    except RunManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/chat/control", response_model=dict[str, Any])
async def control_run(
    body: ControlRequest,
) -> dict[str, Any]:
    """Apply a run control command: ``pause`` / ``resume`` / ``stop``."""
    try:
        return await run_manager.control(body.action)
    except RunManagerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/chat/reset", response_model=dict[str, Any])
async def reset_run(
    request: Request,
    project_root: str = Query("", description="Project root (defaults to app/CWD)"),
    confirm: bool = False,
) -> dict[str, Any]:
    """Cancel any live run and return to idle (safety hatch, also used by tests).

    This tears down a live loop and throws away the in-memory run state, so it
    is guarded like every other mutating route: it requires ``confirm=true``
    and records an audit entry naming the run it cancelled. Without the audit
    entry, a reset that killed a run mid-round was invisible in
    ``.iterate/web-audit.jsonl`` — the only trace was a stopped run the operator
    could not explain.
    """
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="reset requires confirm=true (secondary confirmation)",
        )
    # Resolve the root before resetting: ``run_manager.reset()`` clears
    # ``project_root``/``run_id``, and the audit entry needs both the run it
    # cancelled and the project whose journal records it.
    root = _resolve_project(request, project_root)
    run_id = run_manager.run_id
    await run_manager.reset()
    AuditLog(root).record("run.reset", run_id or "(no active run)", summary={"project": root})
    return {"ok": True, "status": "idle", "cancelledRunId": run_id}


__all__ = ["router"]
