"""API route modules for the WebUI management console (design §17.4).

Each route module defines one or more route functions registered in
:mod:`..api`.
"""

from __future__ import annotations

from . import agents, chat, checkpoints, config, gate, reports, runs, schedules, status, trend, workspaces

__all__ = [
    "agents",
    "chat",
    "checkpoints",
    "config",
    "gate",
    "reports",
    "runs",
    "schedules",
    "status",
    "trend",
    "workspaces",
]