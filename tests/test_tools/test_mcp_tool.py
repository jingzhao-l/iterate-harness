"""Tests for MCP tool adapters — input model generation and argument serialization."""

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from iterate_harness.mcp.types import McpToolInfo
from iterate_harness.tools import create_default_tool_registry
from iterate_harness.tools.base import ToolExecutionContext, ToolRegistry
from iterate_harness.tools.bash_tool import BashTool
from iterate_harness.tools.mcp_tool import _input_model_from_schema, uniquify_tool_name


class TestInputModelFromSchema:
    """Verify _input_model_from_schema maps JSON Schema types correctly."""

    def test_required_string_rejects_none(self):
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        Model = _input_model_from_schema("search", schema)
        with pytest.raises(ValidationError):
            Model(query=None)

    def test_required_string_accepts_value(self):
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        Model = _input_model_from_schema("search", schema)
        m = Model(query="zigzag")
        assert m.query == "zigzag"

    def test_optional_string_defaults_to_none(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "wing": {"type": "string"},
            },
            "required": ["query"],
        }
        Model = _input_model_from_schema("search", schema)
        m = Model(query="test")
        assert m.wing is None

    def test_exclude_none_omits_optional_keeps_required(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "wing": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }
        Model = _input_model_from_schema("search", schema)
        m = Model(query="test")
        dumped = m.model_dump(mode="json", exclude_none=True)
        assert dumped == {"query": "test"}

    def test_all_json_types_mapped(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
                "score": {"type": "number"},
                "active": {"type": "boolean"},
                "tags": {"type": "array"},
                "meta": {"type": "object"},
            },
            "required": ["name", "count", "score", "active", "tags", "meta"],
        }
        Model = _input_model_from_schema("full", schema)
        m = Model(name="x", count=1, score=0.5, active=True, tags=["a"], meta={"k": "v"})
        dumped = m.model_dump(mode="json")
        assert dumped == {
            "name": "x", "count": 1, "score": 0.5,
            "active": True, "tags": ["a"], "meta": {"k": "v"},
        }

    def test_empty_schema_creates_valid_model(self):
        Model = _input_model_from_schema("empty", {"type": "object"})
        m = Model()
        assert m.model_dump(mode="json") == {}

    def test_model_rejects_null_for_required_integer(self):
        schema = {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "required": ["limit"],
        }
        Model = _input_model_from_schema("limited", schema)
        with pytest.raises(ValidationError):
            Model(limit=None)


class _FakeMcpManager:
    """Minimal McpClientManager stand-in exposing only what the registry needs."""

    def __init__(self, tool_infos: list[McpToolInfo]) -> None:
        self._tool_infos = tool_infos
        self.calls: list[tuple[str, str]] = []

    def list_tools(self) -> list[McpToolInfo]:
        return list(self._tool_infos)

    async def call_tool_parts(
        self, server_name: str, tool_name: str, _arguments: dict
    ) -> list[str]:
        """The view the adapter consumes. The real client defines ``call_tool`` as exactly
        this list joined, so the stand-in keeps the same relationship rather than offering
        one of the two views and letting the adapter drift from production shape."""
        self.calls.append((server_name, tool_name))
        return [f"{server_name}:{tool_name}"]

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        return "\n".join(await self.call_tool_parts(server_name, tool_name, arguments)).strip()


class TestUniquifyToolName:
    """Deterministic disambiguation of colliding MCP adapter names."""

    def test_appends_suffix_when_base_is_free(self):
        assert uniquify_tool_name("mcp__srv__search", lambda name: False) == "mcp__srv__search__2"

    def test_increments_until_a_free_name_is_found(self):
        taken = {"mcp__srv__search__2", "mcp__srv__search__3"}
        result = uniquify_tool_name("mcp__srv__search", lambda name: name in taken)
        assert result == "mcp__srv__search__4"

    def test_result_stays_within_the_64_char_api_limit(self):
        long_name = "mcp__" + "s" * 24 + "__" + "t" * 33
        assert len(long_name) == 64
        # Occupy every short-suffix candidate so uniquify must climb to __12.
        taken: set[str] = set()
        for n in range(2, 12):
            suffix = f"__{n}"
            taken.add(f"{long_name[: 64 - len(suffix)].rstrip('_-')}{suffix}")

        result = uniquify_tool_name(long_name, lambda name: name in taken)
        assert len(result) <= 64
        assert result.endswith("__12")

    def test_is_deterministic_for_the_same_collision_set(self):
        taken = {"mcp__srv__search__2"}

        def taken_check(name: str) -> bool:
            return name in taken

        first = uniquify_tool_name("mcp__srv__search", taken_check)
        second = uniquify_tool_name("mcp__srv__search", taken_check)
        assert first == second == "mcp__srv__search__3"


class TestToolRegistryRejectsDuplicates:
    """A duplicate registration must raise, never silently shadow a tool."""

    def test_duplicate_register_raises_value_error(self):
        registry = ToolRegistry()
        registry.register(BashTool())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(BashTool())

    def test_failed_duplicate_leaves_the_original_registered(self):
        registry = ToolRegistry()
        original = BashTool()
        registry.register(original)
        with pytest.raises(ValueError):
            registry.register(BashTool())
        assert registry.get("bash") is original


class TestMcpToolNameCollisions:
    """Two MCP servers whose names sanitize to the same adapter name must both
    stay reachable (deterministic __2 suffix + warning), never shadow each other."""

    @staticmethod
    def _colliding_infos() -> list[McpToolInfo]:
        # ``_sanitize_tool_segment`` maps both separators to "_", so "my server"
        # and "my.server" collide on ``mcp__my_server__search``.
        return [
            McpToolInfo(server_name="my server", name="search", description="", input_schema={}),
            McpToolInfo(server_name="my.server", name="search", description="", input_schema={}),
        ]

    def test_collision_disambiguates_instead_of_overwriting(self, caplog):
        manager = _FakeMcpManager(self._colliding_infos())
        with caplog.at_level(logging.WARNING, logger="iterate_harness.tools"):
            registry = create_default_tool_registry(manager)
        first = registry.get("mcp__my_server__search")
        second = registry.get("mcp__my_server__search__2")
        assert first is not None
        assert second is not None
        assert first is not second
        assert any("collision" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_each_colliding_tool_routes_to_its_own_server(self):
        manager = _FakeMcpManager(self._colliding_infos())
        registry = create_default_tool_registry(manager)
        first = registry.get("mcp__my_server__search")
        second = registry.get("mcp__my_server__search__2")
        assert first is not None
        assert second is not None
        context = ToolExecutionContext(cwd=Path.cwd())
        result_first = await first.execute(first.input_model(), context)
        result_second = await second.execute(second.input_model(), context)
        # Routing must follow registration order deterministically: the first
        # server keeps the bare name, the newcomer gets the __2 suffix — and
        # each adapter must call its OWN server, not the shadowed one.
        assert result_first.output == "my server:search"
        assert result_second.output == "my.server:search"

    def test_non_colliding_tools_keep_their_bare_names(self):
        infos = [
            McpToolInfo(server_name="alpha", name="search", description="", input_schema={}),
            McpToolInfo(server_name="beta", name="search", description="", input_schema={}),
        ]
        registry = create_default_tool_registry(_FakeMcpManager(infos))
        assert registry.get("mcp__alpha__search") is not None
        assert registry.get("mcp__beta__search") is not None
        assert registry.get("mcp__beta__search__2") is None
