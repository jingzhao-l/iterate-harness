"""Lifecycle-hook web routes (design §17.3 P15).

The engine loads lifecycle hooks from settings and plugins and records every
execution; these tests pin the console's branch: the read-only inventory (root
guards, problem reporting for a misspelled event), the paged execution trail
with event/type/success filters, and the root allow-list (403 outside the
served roots, 404 for a missing root).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iterate_harness.config.settings import Settings
from iterate_harness.hooks.journal import record_hook_execution
from iterate_harness.hooks.schemas import CommandHookDefinition, PromptHookDefinition
from iterate_harness.web import security
from iterate_harness.web.api import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("ITERATE_DATA_DIR", str(data))
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path / "config"))
    return data


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    # Allow-list the root so the route accepts it.
    security.set_allowed_roots([root])
    yield root
    security.set_allowed_roots(None)


def _seed_hooks(project: Path) -> None:
    from iterate_harness.config.paths import get_config_file_path

    settings = Settings()
    settings.hooks = {
        "session_start": [CommandHookDefinition(command="printf 'booted'")],
        "pre_tool_use": [
            PromptHookDefinition(prompt="Check the tool call", matcher="bash"),
            CommandHookDefinition(command="echo gated", matcher="*edit*", block_on_failure=True),
        ],
    }
    path = get_config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")


def test_page_lists_hooks(client: TestClient, project: Path) -> None:
    _seed_hooks(project)
    response = client.get("/api/v1/hooks", params={"project_root": str(project)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total"] == 3
    assert {group["event"] for group in payload["events"]} == {"session_start", "pre_tool_use"}
    assert payload["has_problems"] is False


def test_page_reports_unknown_event(client: TestClient, project: Path) -> None:
    from iterate_harness.config.paths import get_config_file_path

    settings = Settings()
    settings.hooks = {"pre-tool-use": [CommandHookDefinition(command="echo nope")]}
    path = get_config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    payload = client.get("/api/v1/hooks", params={"project_root": str(project)}).json()
    assert payload["has_problems"] is True
    assert any(problem["kind"] == "event" for problem in payload["problems"])


def test_log_pages_newest_first(client: TestClient, project: Path) -> None:
    for index in range(3):
        record_hook_execution(
            project,
            event="pre_tool_use",
            hook_type="command",
            detail=f"hook-{index}",
            success=index % 2 == 0,
        )
    payload = client.get(
        "/api/v1/hooks/log",
        params={"project_root": str(project), "limit": 2},
    ).json()
    assert payload["total"] == 3
    assert payload["returned"] == 2
    assert payload["truncated"] is True
    assert payload["entries"][0]["detail"] == "hook-2"
    assert payload["events"] == ["pre_tool_use"]


def test_log_filters(client: TestClient, project: Path) -> None:
    record_hook_execution(project, event="pre_tool_use", hook_type="command", success=True)
    record_hook_execution(project, event="post_tool_use", hook_type="http", success=False)
    ok = client.get(
        "/api/v1/hooks/log",
        params={"project_root": str(project), "success": "true"},
    ).json()
    assert ok["total"] == 1
    assert ok["entries"][0]["hook_type"] == "command"
    by_event = client.get(
        "/api/v1/hooks/log",
        params={"project_root": str(project), "event": "post_tool_use"},
    ).json()
    assert by_event["total"] == 1
    assert by_event["entries"][0]["hook_type"] == "http"


def test_root_outside_allowlist_is_403(client: TestClient, tmp_path: Path) -> None:
    allowed = tmp_path / "repo"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    security.set_allowed_roots([allowed])
    try:
        response = client.get("/api/v1/hooks", params={"project_root": str(outside)})
        assert response.status_code == 403
    finally:
        security.set_allowed_roots(None)


def test_missing_root_is_404(client: TestClient, tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    response = client.get("/api/v1/hooks", params={"project_root": str(missing)})
    assert response.status_code == 404
