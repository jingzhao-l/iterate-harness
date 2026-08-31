"""Plugin manifest schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PluginManifest(BaseModel):
    """Plugin manifest stored in plugin.json or .claude-plugin/plugin.json."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    enabled_by_default: bool = True
    skills_dir: str = "skills"
    tools_dir: str = "tools"
    hooks_file: str = "hooks.json"
    mcp_file: str = "mcp.json"
    # Extended fields: optional author, commands, agents, etc.
    author: dict[str, Any] | None = None
    commands: str | list[Any] | dict[str, Any] | None = None
    agents: str | list[Any] | None = None
    skills: str | list[Any] | None = None
    hooks: str | dict[str, Any] | list[Any] | None = None
