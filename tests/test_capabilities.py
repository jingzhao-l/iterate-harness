"""Capability inventory tests (``ih capabilities`` + GET /api/v1/capabilities).

The inventory exists to answer one question the console and CLI could not
answer before: *what is actually loaded, and why is a capability missing?* The
tests pin three things that must not regress:

- the shared static MCP checks (missing executable, bad URL, missing cwd),
- loader failures degrading to a problem row instead of a 500,
- CLI/route parity on the same JSON-ready model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from iterate_harness.capabilities import (
    build_capability_inventory,
    command_behavior,
    mcp_transport_preview,
    render_capability_inventory,
    schema_argument_preview,
    validate_mcp_server,
)
from iterate_harness.web.api import create_app


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_schema_argument_preview_sorts_required_and_optional() -> None:
    schema = {
        "name": "file_write",
        "input_schema": {
            "properties": {"path": {}, "content": {}, "mode": {}},
            "required": ["content", "path"],
        },
    }
    assert schema_argument_preview(schema) == {
        "required_args": ["content", "path"],
        "optional_args": ["mode"],
    }
    assert schema_argument_preview({"input_schema": None}) == {
        "required_args": [],
        "optional_args": [],
    }


def test_command_behavior_classifies_read_only_stateful_and_unknown() -> None:
    assert command_behavior("doctor")["kind"] == "read_only"
    assert command_behavior("model")["kind"] == "stateful"
    assert command_behavior("plugin-specific-command")["kind"] == "unknown"


def test_mcp_transport_preview_handles_dict_and_object_configs() -> None:
    assert mcp_transport_preview(
        {"type": "stdio", "command": "python", "args": ["-m", "server"]}
    ) == {"transport": "stdio", "target": "python -m server"}
    assert mcp_transport_preview(
        {"type": "http", "url": "https://example.com/mcp"}
    ) == {"transport": "http", "target": "https://example.com/mcp"}

    class Config:
        type = "ws"
        url = "wss://example.com/socket"

    assert mcp_transport_preview(Config()) == {
        "transport": "ws",
        "target": "wss://example.com/socket",
    }


def test_valid_stdio_command_passes_static_validation() -> None:
    result = validate_mcp_server("filesystem", {"type": "stdio", "command": sys.executable})
    assert result["status"] == "ok"
    assert result["issues"] == []
    assert result["remedy"] == ""


def test_missing_executable_is_an_error_with_an_actionable_remedy() -> None:
    result = validate_mcp_server(
        "broken", {"type": "stdio", "command": "iterate-harness-definitely-missing"}
    )
    assert result["status"] == "error"
    assert any("command not found in PATH" in issue for issue in result["issues"])
    assert "absolute executable path" in result["remedy"]


def test_missing_command_and_cwd_are_both_reported() -> None:
    result = validate_mcp_server(
        "broken",
        {"type": "stdio", "command": "", "cwd": "/nonexistent/iterate-mcp-cwd"},
    )
    assert result["status"] == "error"
    assert "missing command" in result["issues"]
    assert any("cwd does not exist" in issue for issue in result["issues"])
    assert "mcp_servers.broken.command" in result["remedy"]
    assert "mcp_servers.broken.cwd" in result["remedy"]


@pytest.mark.parametrize(
    ("transport", "url", "expected"),
    [
        ("http", "ftp://example.com/mcp", "invalid http url"),
        ("http", "https://", "invalid http url"),
        ("ws", "https://example.com/socket", "invalid ws url"),
    ],
)
def test_invalid_remote_url_is_an_error(
    transport: str, url: str, expected: str
) -> None:
    result = validate_mcp_server("remote", {"type": transport, "url": url})
    assert result["status"] == "error"
    assert any(expected in issue for issue in result["issues"])
    assert "mcp_servers.remote.url" in result["remedy"]


# ---------------------------------------------------------------------------
# Inventory assembly
# ---------------------------------------------------------------------------


def test_inventory_lists_builtin_registries_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep the test independent of whatever plugins the developer machine has.
    monkeypatch.setattr("iterate_harness.plugins.load_plugins", lambda *_args, **_kw: [])

    inventory = build_capability_inventory(tmp_path)

    assert inventory["project_root"] == str(tmp_path.resolve())
    assert inventory["tools"], "the built-in tool registry must never be empty"
    assert inventory["commands"], "the built-in command registry must never be empty"
    assert inventory["skills"], "bundled skills must be visible"
    assert inventory["plugins"] == []
    assert inventory["mcp_servers"] == []
    summary = inventory["summary"]
    assert summary["tools"] == len(inventory["tools"])
    assert summary["commands"] == len(inventory["commands"])
    assert summary["skills"] == len(inventory["skills"])
    assert summary["mcp_errors"] == 0
    assert inventory["has_problems"] is False
    # Tools carry their schema-derived argument lists, not a placeholder.
    assert any(row["required_args"] for row in inventory["tools"])
    assert all(isinstance(row["optional_args"], list) for row in inventory["tools"])


def test_bad_mcp_config_becomes_a_problem_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("iterate_harness.plugins.load_plugins", lambda *_args, **_kw: [])
    monkeypatch.setattr(
        "iterate_harness.mcp.config.load_mcp_server_configs",
        lambda *_args, **_kw: {
            "broken": {"type": "stdio", "command": "iterate-harness-definitely-missing"}
        },
    )

    inventory = build_capability_inventory(tmp_path)

    assert inventory["has_problems"] is True
    assert inventory["summary"]["mcp_servers"] == 1
    assert inventory["summary"]["mcp_errors"] == 1
    problem = next(row for row in inventory["problems"] if row["kind"] == "mcp")
    assert problem["name"] == "broken"
    assert problem["remedy"]


def test_loader_failure_keeps_the_rest_of_the_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> list[object]:
        raise ValueError("bad plugin manifest")

    monkeypatch.setattr("iterate_harness.plugins.load_plugins", boom)

    inventory = build_capability_inventory(tmp_path)

    assert inventory["plugins"] == []
    assert inventory["tools"], "a plugin failure must not hide the tool registry"
    problem = next(row for row in inventory["problems"] if row["kind"] == "loader")
    assert problem["name"] == "plugins"
    assert "ValueError: bad plugin manifest" in problem["detail"]
    assert "ih doctor" in problem["remedy"]


def test_render_is_human_readable_and_mentions_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("iterate_harness.plugins.load_plugins", lambda *_args, **_kw: [])
    monkeypatch.setattr(
        "iterate_harness.mcp.config.load_mcp_server_configs",
        lambda *_args, **_kw: {"broken": {"type": "http", "url": "ftp://bad"}},
    )

    text = render_capability_inventory(build_capability_inventory(tmp_path))

    assert "capability inventory" in text
    assert "Problems (1)" in text
    assert "[mcp] broken" in text
    assert "remedy:" in text
    assert "MCP servers (1)" in text
    assert "Tools (" in text
    assert "Slash commands (" in text


# ---------------------------------------------------------------------------
# Web route
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_route_returns_the_shared_inventory(client: TestClient, tmp_path: Path) -> None:
    response = client.get(
        "/api/v1/capabilities", params={"project_root": str(tmp_path)}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["project_root"] == str(tmp_path.resolve())
    assert payload["tools"]
    assert payload["commands"]
    assert payload["skills"]
    assert set(payload["summary"]) >= {
        "tools",
        "commands",
        "skills",
        "plugins",
        "mcp_servers",
        "mcp_errors",
        "problems",
    }
    assert payload["summary"]["tools"] == len(payload["tools"])
    assert payload["has_problems"] is False


def test_route_404s_for_missing_root(client: TestClient) -> None:
    response = client.get(
        "/api/v1/capabilities",
        params={"project_root": "/nonexistent/iterate-capabilities-probe"},
    )
    assert response.status_code == 404


def test_route_respects_allowed_root_guard(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from iterate_harness.web import security

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(security, "_ALLOWED_ROOTS", {str(allowed.resolve())})
    response = client.get(
        "/api/v1/capabilities", params={"project_root": str(outside)}
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_json_and_strict_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from iterate_harness.cli import app

    monkeypatch.setattr("iterate_harness.plugins.load_plugins", lambda *_args, **_kw: [])
    result = CliRunner().invoke(
        app, ["capabilities", "--project-root", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["tools"] == len(payload["tools"])

    monkeypatch.setattr(
        "iterate_harness.mcp.config.load_mcp_server_configs",
        lambda *_args, **_kw: {"broken": {"type": "http", "url": "ftp://bad"}},
    )
    strict = CliRunner().invoke(
        app,
        ["capabilities", "--project-root", str(tmp_path), "--json", "--strict"],
    )
    assert strict.exit_code == 1, strict.output
    strict_payload = json.loads(strict.output)
    assert strict_payload["has_problems"] is True


def test_cli_text_lists_sections(tmp_path: Path) -> None:
    from iterate_harness.cli import app

    result = CliRunner().invoke(
        app, ["capabilities", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "IterateHarness capability inventory" in result.output
    assert "MCP servers (" in result.output
    assert "Skills (" in result.output
    assert "Tools (" in result.output
