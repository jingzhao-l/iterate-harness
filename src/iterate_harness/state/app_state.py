"""Minimal application state model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


@dataclass
class AppState:
    """Shared mutable UI/session state."""

    model: str
    permission_mode: str
    theme: str
    cwd: str = "."
    # Dual-mode architecture (design §20.2): ``task_mode`` decides *what the
    # agent does* (``code`` = general programming agent + defensive kernel,
    # ``iterate`` = 1.x review loop) and is orthogonal to ``permission_mode``
    # (how permissive the permission layer is). Switched via Tab in the TUI;
    # affects only the next round, never interrupts in-flight work.
    task_mode: str = "iterate"
    provider: str = "unknown"
    auth_status: str = "missing"
    base_url: str = ""
    vim_enabled: bool = False
    voice_enabled: bool = False
    voice_available: bool = False
    voice_reason: str = ""
    fast_mode: bool = False
    effort: str = "medium"
    passes: int = 1
    mcp_connected: int = 0
    mcp_failed: int = 0
    bridge_sessions: int = 0
    output_style: str = "default"
    keybindings: dict[str, str] = field(default_factory=dict)


class AppStateUpdates(TypedDict, total=False):
    """Subset of :class:`AppState` fields accepted by ``AppStateStore.set``."""

    model: str
    permission_mode: str
    theme: str
    cwd: str
    task_mode: str
    provider: str
    auth_status: str
    base_url: str
    vim_enabled: bool
    voice_enabled: bool
    voice_available: bool
    voice_reason: str
    fast_mode: bool
    effort: str
    passes: int
    mcp_connected: int
    mcp_failed: int
    bridge_sessions: int
    output_style: str
    keybindings: dict[str, str]
