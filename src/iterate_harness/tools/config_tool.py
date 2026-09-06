"""Tool for reading and updating settings."""

from __future__ import annotations

import json

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

#: Settings keys expected to hold int/float values (argv strings must be
#: coerced before assignment, otherwise a `config set max_tokens 5000`
#: writes the string "5000" into a numeric field).
_INT_KEYS = frozenset({
    "max_tokens",
    "timeout",
    "context_window_tokens",
    "auto_compact_threshold_tokens",
    "max_turns",
    "passes",
})


def _coerce_value(key: str, value: str) -> object:
    """Coerce a CLI string value into the Python type the setting expects."""
    if key in _INT_KEYS:
        try:
            return int(value)
        except ValueError:
            return value
    return value


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
            # Never echo credentials into the model context — the plain dump
            # leaks api_key (and other secrets) to the LLM and into the
            # conversation transcript.
            redacted = settings.model_dump()
            redacted["api_key"] = "<redacted>"
            return ToolResult(output=json.dumps(redacted, indent=2, ensure_ascii=False))
        if arguments.action == "set" and arguments.key and arguments.value is not None:
            if arguments.key not in _ALLOWED_CONFIG_KEYS:
                return ToolResult(
                    output=f"Config key '{arguments.key}' is not in the allowed set of mutable keys.",
                    is_error=True,
                )
            if not hasattr(settings, arguments.key):
                return ToolResult(output=f"Unknown config key: {arguments.key}", is_error=True)
            setattr(settings, arguments.key, _coerce_value(arguments.key, arguments.value))
            save_settings(settings)
            return ToolResult(output=f"Updated {arguments.key}")
        return ToolResult(output="Usage: action=show or action=set with key/value", is_error=True)
