"""MCP tool adapters."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, create_model

from iterate_harness.iterate.dimension_ledger import record_observed_echo
from iterate_harness.iterate.glasspane_reverification import relay_answer
from iterate_harness.mcp.client import McpClientManager, McpServerNotConnectedError
from iterate_harness.mcp.types import McpToolInfo
from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult

log = logging.getLogger(__name__)

#: The provider's tool-name pattern is ``^[a-zA-Z0-9_-]{1,64}$``; the adapter
#: spends 7 characters on the ``mcp__`` prefix and the ``__`` separator.
_TOOL_NAME_LIMIT = 64
_TOOL_PREFIX_LEN = len("mcp__")
_SEPARATOR_LEN = len("__")
_MAX_SERVER_SEGMENT = 24
_MAX_TOOL_SEGMENT = _TOOL_NAME_LIMIT - _TOOL_PREFIX_LEN - _SEPARATOR_LEN - _MAX_SERVER_SEGMENT


class McpToolAdapter(BaseTool[BaseModel]):
    """Expose one MCP tool as a normal IterateHarness tool."""

    def __init__(self, manager: McpClientManager, tool_info: McpToolInfo) -> None:
        self._manager = manager
        self._tool_info = tool_info
        server_segment = _sanitize_tool_segment(tool_info.server_name, _MAX_SERVER_SEGMENT)
        tool_segment = _sanitize_tool_segment(tool_info.name, _MAX_TOOL_SEGMENT)
        self.name = f"mcp__{server_segment}__{tool_segment}"
        self.description = tool_info.description or f"MCP tool {tool_info.name}"
        self.input_model = _input_model_from_schema(self.name, tool_info.input_schema)

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        payload = arguments.model_dump(mode="json", exclude_none=True)
        try:
            parts = await self._manager.call_tool_parts(
                self._tool_info.server_name,
                self._tool_info.name,
                payload,
            )
        except McpServerNotConnectedError as exc:
            return ToolResult(output=str(exc), is_error=True)
        # Byte-identical to what this adapter returned before: ``McpClientManager.call_tool`` is
        # defined as exactly this join. The parts are taken instead of the join because the
        # GlassPane shell answers one call with several content items (its frame, an update-state
        # block, a provider line, and — since the dimension slot — what the call was declared
        # for), and a consumer that must classify them cannot read a string that already lost the
        # boundary. What the model sees is unchanged.
        self._witness_echo(context, payload, parts)
        return ToolResult(output="\n".join(parts).strip())

    def _witness_echo(
        self, context: ToolExecutionContext, payload: dict[str, Any], parts: list[str]
    ) -> None:
        """Park a witnessed ``dimension: …`` declaration under the operation it was fetched for.

        Cheap by construction: the scan is a prefix test over items this call already has, and
        the classifier — which does parse the frame — runs only when some item is an echo. That
        ordering is deliberate: an ordinary ``bash`` or ``read`` reply must not pay a JSON parse
        to conclude nothing.
        """
        if not any(item.startswith("dimension: ") for item in parts):
            return
        try:
            answer = relay_answer(self._tool_info.name, payload, parts)
        except Exception as error:  # noqa: BLE001 — a witness must never break a tool reply
            # Loud on purpose: the failure is in this harness's classifier, not in the engine's
            # answer, and swallowing it in debug would mean a broken witness silently stops
            # checking while every test that goes through the reply still passes.
            log.warning(
                "glasspane echo witnessing failed for %s: %s", self._tool_info.name, error, exc_info=True
            )
            return
        record_observed_echo(context.metadata, answer.operation_id, answer.echoed_dimension)


_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _input_model_from_schema(tool_name: str, schema: dict[str, object]) -> type[BaseModel]:
    raw_properties = schema.get("properties", {})
    properties = raw_properties if isinstance(raw_properties, dict) else {}

    fields: dict[str, Any] = {}
    raw_required = schema.get("required", [])
    required = set(raw_required) if isinstance(raw_required, list) else set()
    for key in properties:
        prop_value = properties[key]
        prop = prop_value if isinstance(prop_value, dict) else {}
        py_type = _JSON_TYPE_MAP.get(str(prop.get("type", "")), object)
        if key in required:
            fields[key] = (py_type, Field(default=...))
        else:
            fields[key] = (py_type | None, Field(default=None))
    return create_model(f"{tool_name.title().replace('-', '_')}Input", **fields)


def _sanitize_tool_segment(value: str, max_length: int) -> str:
    """Normalize one tool-name segment and bound its length.

    The Anthropic tool-name schema is ``^[a-zA-Z0-9_-]{1,64}$``. A long MCP
    server or tool name produced a 65+ char identifier, which the API rejects
    with a 400 for the *whole* request — so the tool could not be called at
    all, and the truncation keeps a long name usable (the prefix is kept,
    which is the part that identifies the tool).
    """
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", value)
    if not sanitized:
        return "tool"
    if not sanitized[0].isalpha():
        sanitized = f"mcp_{sanitized}"
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip("_-")
    return sanitized or "tool"


def uniquify_tool_name(name: str, is_taken: Callable[[str], bool]) -> str:
    """Return a deterministic variant of *name* that is not taken.

    Two MCP servers (``my server`` and ``my-server``) can sanitize to the same
    tool name, and a long name can truncate onto another tool's identifier.
    Collisions are disambiguated by appending ``__2``, ``__3``, ... in
    ascending order (trimming the base to keep the result within the 64-char
    API limit), so the same set of colliding tools always maps to the same
    surviving names regardless of registration timing.
    """
    counter = 2
    while True:
        suffix = f"__{counter}"
        base = name[: _TOOL_NAME_LIMIT - len(suffix)].rstrip("_-") or name
        candidate = f"{base}{suffix}"
        if not is_taken(candidate):
            return candidate
        counter += 1
