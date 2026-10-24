"""Agent roster routes (design §17.3 P9).

The engine can run a whole team — subprocess teammates, in-process teammates
and shell tasks — and every one of them registers a record with the task
manager. Nothing could see that record from the browser before: the roster
existed only inside ``BackgroundTaskManager._tasks``, and the backend's own
``list_teammates`` / ``get_teammate_status`` / ``active_agents`` /
``shutdown_all`` had no callers at all. A hung loop therefore gave the
operator nothing to look at except the whole terminal.

``GET /agents`` is a pure read. ``POST /agents/shutdown`` is the one
mutating call — it goes through ``BackgroundTaskManager.stop_task`` and is
audited like every other mutating operation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query

from ..schemas import AgentsView, StopAgentResult, StopStatus
from ..security import AuditLog, allowed_roots, default_root, root_is_allowed

router = APIRouter(tags=["agents"])


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else (default_root() or Path.cwd())
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
    return root


@router.get("/agents", response_model=AgentsView)
async def get_agents(
    project_root: str = "",
    tail: int = Query(2000, ge=0, le=20000, description="Bytes of output tail per agent (0 = none)"),
    status: str = Query("", description="Filter by status: running / completed / failed / killed"),
    limit: int = Query(200, ge=1, le=500),
) -> AgentsView:
    """One row per worker the harness is tracking, running first.

    Read-only: the roster is a snapshot of the task manager and never touches
    a worker. ``project_root`` selects where an operation would be audited;
    the workers themselves are process-wide, not per-project.
    """
    _resolve_project(project_root)
    from ...swarm.roster import KNOWN_STATUSES, build_roster

    selected = status.strip().lower()
    if selected and selected not in KNOWN_STATUSES:
        # A typo here must be an error, not a quiet empty page: the operator
        # would otherwise read "the team is empty" and walk away.
        raise HTTPException(
            status_code=422,
            detail=f"unknown status {status!r}; expected one of "
            + "|".join(sorted(KNOWN_STATUSES)),
        )
    view = build_roster(
        tail_bytes=tail,
        max_agents=limit,
        status=selected or None,
    )
    return AgentsView(**view)


@router.post("/agents/shutdown", response_model=StopAgentResult)
async def shutdown_agent(
    agent_id: str = Query(..., min_length=1, max_length=200),
    project_root: str = "",
    confirm: bool = False,
) -> StopAgentResult:
    """Stop one worker by task id or swarm identity (``name@team``).

    Mutating and audited. Requires ``confirm=true`` — this kills a process
    group, and a mistyped id should not be a one-key-away footgun. The audit
    entry is written under the *resolved* project root, which is where the
    operator was looking when they clicked, so the journal line can be
    matched back to the browser view.
    """
    root = _resolve_project(project_root)
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="shutdown requires confirm=true (secondary confirmation)",
        )

    from ...swarm.roster import stop_agent

    result: dict[str, Any] = await stop_agent(agent_id)
    status = str(result.get("status") or "error")
    outcome: dict[str, Any] = {
        "id": result.get("id") or agent_id,
        "status_now": result.get("status_now"),
        "resolved_id": result.get("id"),
    }
    # ``not_found`` is a truthful "already gone" answer, not a server error;
    # only a real exception from the manager is worth an HTTP 500, and
    # stop_agent already converts those into a status="error" result.
    AuditLog(root).record("agent.shutdown", str(result.get("id") or agent_id), summary=outcome)

    if status == "invalid":
        raise HTTPException(status_code=422, detail=str(result.get("message") or "缺少子代理标识"))

    if status in {"stopped", "not_found", "already_terminal"}:
        stop_status: StopStatus = cast(StopStatus, status)
    else:
        stop_status = "error"
    return_code = result.get("return_code")
    return StopAgentResult(
        status=stop_status,
        message=str(result.get("message") or ""),
        id=str(result["id"]) if result.get("id") else None,
        agent_id=str(result.get("agent_id") or ""),
        status_now=str(result["status_now"]) if result.get("status_now") else None,
        return_code=int(return_code) if isinstance(return_code, int) else None,
    )


__all__ = ["router"]
