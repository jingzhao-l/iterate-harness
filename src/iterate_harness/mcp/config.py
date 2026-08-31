"""Load MCP server config from settings and plugins."""

from __future__ import annotations

from iterate_harness.config.settings import Settings
from iterate_harness.plugins.types import LoadedPlugin


def load_mcp_server_configs(settings: Settings, plugins: list[LoadedPlugin]) -> dict[str, object]:
    """Merge settings and plugin MCP server configs."""
    servers: dict[str, object] = {}
    for name, config in settings.mcp_servers.items():
        servers[name] = config
    for plugin in plugins:
        if not plugin.enabled:
            continue
        for name, config in plugin.mcp_servers.items():
            servers.setdefault(f"{plugin.manifest.name}:{name}", config)
    return servers
