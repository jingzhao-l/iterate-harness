"""Tool for reading and updating settings."""

from __future__ import annotations

from pydantic import BaseModel, Field

from iterate_harness.config.settings import load_settings, save_settings
from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult

_ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset({
    "api_key",
    "model",
    "max_tokens",
    "base_url",
    "timeout",
    "context_window_tokens",
    "auto_compact_threshold_tokens",
    "api_format",
    "provider",
    "active_profile",
    "max_turns",
    "system_prompt",
    "theme",
    "output_style",
    "vim_mode",
    "voice_mode",
    "fast_mode",
    "effort",
    "passes",
    "verbose",
})


class ConfigToolInput(BaseModel):
    """Arguments for config access."""

    action: str = Field(default="show", description="show or set")
    key: str | None = Field(default=None)
    value: str | None = Field(default=None)


class ConfigTool(BaseTool[ConfigToolInput]):
    """Read or update IterateHarness settings."""

    name = "config"
    description = "Read or update IterateHarness settings."
    input_model = ConfigToolInput

    async def execute(self, arguments: ConfigToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        settings = load_settings()
        if arguments.action == "show":
            return ToolResult(output=settings.model_dump_json(indent=2))
        if arguments.action == "set" and arguments.key and arguments.value is not None:
            if arguments.key not in _ALLOWED_CONFIG_KEYS:
                return ToolResult(
                    output=f"Config key '{arguments.key}' is not in the allowed set of mutable keys.",
                    is_error=True,
                )
            if not hasattr(settings, arguments.key):
                return ToolResult(output=f"Unknown config key: {arguments.key}", is_error=True)
            setattr(settings, arguments.key, arguments.value)
            save_settings(settings)
            return ToolResult(output=f"Updated {arguments.key}")
        return ToolResult(output="Usage: action=show or action=set with key/value", is_error=True)
