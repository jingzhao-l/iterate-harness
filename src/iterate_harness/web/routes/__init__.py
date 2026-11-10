"""API route modules for the WebUI management console (design §17.4).

Each route module defines one or more route functions registered in
:mod:`..api`.
"""

from __future__ import annotations

from . import agents, audit, capabilities, chat, checkpoints, config, doctor, gate, hooks, memory, reports, runs, schedules, status, trend, workspaces

__all__ = [
    "agents",
    "audit",
    "capabilities",
    "chat",
    "checkpoints",
    "config",
    "doctor",
    "gate",
    "hooks",
    "memory",
    "reports",
    "runs",
    "schedules",
    "status",
    "trend",
    "workspaces",
]