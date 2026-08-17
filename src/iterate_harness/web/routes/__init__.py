"""API route modules for the WebUI management console (design §17.4).

Each route module defines one or more route functions registered in
:mod:`..api`.
"""

from __future__ import annotations

from . import chat, checkpoints, config, reports, runs, status

__all__ = ["chat", "checkpoints", "config", "reports", "runs", "status"]