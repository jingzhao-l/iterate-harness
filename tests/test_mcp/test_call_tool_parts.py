"""The item boundary of one MCP tool reply must survive the client.

Fixtures copy the shape the shipped `glasspane-mcp` actually answers with, asserted in the
GlassPane tests `mcp-shell/test/update-state.test.mjs` (the daemon frame plus the
`update state:` block) and `mcp-shell/test/qoder-20261019-remedy-executability.test.mjs`
(after the 2026-10-20 batch, a third item: the `provider: … declared prerequisites: …`
attribution line). `gp_diagnose` already returns more than one content item TODAY, so the
"join everything" view is not a hypothetical — it is the defect.

The two contracts pinned here:

* `call_tool_parts` returns exactly one entry per content item (same non-text,
  `structured_content` and `(no output)` fallbacks the old loop built);
* `call_tool` stays BYTE-IDENTICAL to what it returned before the split: the parts
  joined with newlines, stripped.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from iterate_harness.mcp.client import McpClientManager, McpServerNotConnectedError

#: Daemon frame: the engine's diagnosis, JSON, item 0 of the shipped reply.
DAEMON_FRAME = json.dumps(
    {"operationId": "op_20261021_0001", "diagnosis": {"class": "T0", "report": "the GUI claim still does not hold"}}
)
#: Item 1: the update-state block the shell appends — multi-line by construction
#: (summary line, CA-bundle line, self-update line, per update-state.test.mjs).
UPDATE_STATE_BLOCK = (
    "update state: status=up-to-date current=1.6.2 latest=1.6.2"
    " updatedAt=2026-10-20T04:00:00.000Z autoUpdate=on\n"
    "ca trust bundle: this install has never recorded an export."
    " NOT the same as \"the bundle is fine\".\n"
    "updater self-update: refreshed — /Users/x/.glasspane/runtime/1.6.2/updater/cli.js"
)
#: Item 2: the provider attribution line the 2026-10-20 batch added.
PROVIDER_LINE = (
    "provider: glasspane-engine-ae version 0.1.9 method diagnose"
    " | declared prerequisites: evidencePack with at least one non-empty signal field"
)


def _manager(result: CallToolResult) -> McpClientManager:
    manager = McpClientManager({})
    session = AsyncMock()
    session.call_tool.return_value = result
    manager._sessions["glasspane-mcp"] = session
    return manager


def _text_result(texts: list[str]) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text) for text in texts])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "items",
    [
        pytest.param([DAEMON_FRAME], id="1-item"),
        pytest.param([DAEMON_FRAME, UPDATE_STATE_BLOCK], id="2-item"),
        pytest.param([DAEMON_FRAME, UPDATE_STATE_BLOCK, PROVIDER_LINE], id="3-item"),
        pytest.param(["  frame with padding  ", UPDATE_STATE_BLOCK, " trailing "], id="3-item-padded"),
    ],
)
async def test_call_tool_parts_keeps_the_item_boundary(items: list[str]) -> None:
    """One entry per content item — the shape the shell sent, item for item."""
    manager = _manager(_text_result(items))
    parts = await manager.call_tool_parts("glasspane-mcp", "gp_diagnose", {})
    assert parts == items


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "items",
    [
        pytest.param([DAEMON_FRAME], id="1-item"),
        pytest.param([DAEMON_FRAME, UPDATE_STATE_BLOCK], id="2-item"),
        pytest.param([DAEMON_FRAME, UPDATE_STATE_BLOCK, PROVIDER_LINE], id="3-item"),
        pytest.param(["  frame with padding  ", UPDATE_STATE_BLOCK, " trailing "], id="3-item-padded"),
    ],
)
async def test_call_tool_is_exactly_the_join_of_the_parts(items: list[str]) -> None:
    """The LLM-visible string did not move: `call_tool` == "\\n".join(parts).strip()."""
    manager = _manager(_text_result(items))
    joined = await manager.call_tool("glasspane-mcp", "gp_diagnose", {})
    assert joined == "\n".join(items).strip()
    parts = await manager.call_tool_parts("glasspane-mcp", "gp_diagnose", {})
    assert joined == "\n".join(parts).strip()


@pytest.mark.asyncio
async def test_non_text_items_stay_their_own_part_and_the_join_still_matches() -> None:
    """The old loop put a non-text item in as its model_dump_json; it still does, per item."""
    image = ImageContent(type="image", data="aGVsbG8=", mimeType="image/png")
    result = CallToolResult(content=[TextContent(type="text", text=DAEMON_FRAME), image])
    manager = _manager(result)
    parts = await manager.call_tool_parts("glasspane-mcp", "gp_observe", {})
    assert parts[0] == DAEMON_FRAME
    assert parts[1] == image.model_dump_json()
    assert await manager.call_tool("glasspane-mcp", "gp_observe", {}) == "\n".join(parts).strip()


@pytest.mark.asyncio
async def test_structured_content_and_no_output_fallbacks_remain_their_own_items() -> None:
    manager = _manager(CallToolResult(content=[], structured_content={"verdict": "clean"}))
    assert await manager.call_tool_parts("glasspane-mcp", "gp_probe_status", {}) == [str({"verdict": "clean"})]
    empty = _manager(CallToolResult(content=[]))
    assert await empty.call_tool_parts("glasspane-mcp", "gp_probe_status", {}) == ["(no output)"]
    assert await empty.call_tool("glasspane-mcp", "gp_probe_status", {}) == "(no output)"


@pytest.mark.asyncio
async def test_the_error_surface_did_not_move_to_only_one_of_the_two_paths() -> None:
    """Not connected, session error and timeout raise on BOTH paths, same exceptions."""
    unconnected = McpClientManager({})
    for call in (unconnected.call_tool, unconnected.call_tool_parts):
        with pytest.raises(McpServerNotConnectedError, match="not connected"):
            await call("missing", "gp_diagnose", {})

    flaky = McpClientManager({})
    session = AsyncMock()
    session.call_tool.side_effect = RuntimeError("transport closed")
    flaky._sessions["flaky"] = session
    for call in (flaky.call_tool, flaky.call_tool_parts):
        with pytest.raises(McpServerNotConnectedError, match="transport closed"):
            await call("flaky", "gp_diagnose", {})
