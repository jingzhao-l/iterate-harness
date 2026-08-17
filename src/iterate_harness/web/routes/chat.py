"""Chat / human-in-the-loop routes (design §18.3).

Bridges the live iterate loop (:class:`~iterate_harness.web.run_manager.RunManager`)
into the WebUI REST surface:

- ``POST /chat/start`` — launch an iterate loop inside the server process.
- ``GET  /chat/status`` — current run-state snapshot for the chat panel.
- ``GET  /chat/history`` — persisted human-interaction transcript.
- ``POST /chat/message`` — send a chat message (answer a pending request, or
  nudge a running loop).
- ``POST /chat/control`` — pause / resume / stop the loop.
- ``POST /chat/reset`` — cancel any live run and return to idle (safety hatch).

Live progress / run-state transitions / chat messages are pushed over the
SSE stream via the in-process :mod:`~iterate_harness.web.hub` (see
:mod:`..events`), so the frontend stays in sync without polling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..run_manager import RunManagerError, run_manager
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
        run_id = await run_manager.start(root, body.mode, body.changed, body.ref)
    except RunManagerError as exc:
        status = 409 if "运行中" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"runId": run_id, "status": run_manager.state}


@router.get("/chat/status", response_model=ChatRunStatus)
async def chat_status() -> ChatRunStatus:
    """Live run-state snapshot for the chat panel."""
    return run_manager.status()


@router.get("/chat/history", response_model=list[ChatMessage])
async def chat_history() -> list[ChatMessage]:
    """Persisted human-interaction transcript (oldest first, capped)."""
    entries = run_manager.history()
    return [ChatMessage(**entry) for entry in entries if isinstance(entry, dict)]


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
async def reset_run() -> dict[str, Any]:
    """Cancel any live run and return to idle (safety hatch, also used by tests)."""
    await run_manager.reset()
    return {"ok": True, "status": "idle"}


__all__ = ["router"]
