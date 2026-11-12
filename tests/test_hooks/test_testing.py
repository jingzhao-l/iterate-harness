"""``ih hooks test`` tests (design §17.3 P15).

A hook normally only proves itself by firing inside a real run. These tests pin
the "test it now" channel: the synthetic payload, matcher filtering, real
command/HTTP execution through the engine's own executor, journal visibility,
prompt/agent skipping without a model, dry-run safety, and the CLI exit codes
(0 pass, 1 failure/blocked, 1 with ``--require-match`` when nothing matched).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iterate_harness.cli import app as cli_app
from iterate_harness.config.settings import Settings
from iterate_harness.hooks.events import HookEvent
from iterate_harness.hooks.journal import journal_path, list_hook_executions
from iterate_harness.hooks.schemas import (
    CommandHookDefinition,
    HttpHookDefinition,
    PromptHookDefinition,
)
from iterate_harness.hooks.testing import (
    build_test_payload,
    render_hook_test_markdown,
    render_hook_test_table,
    run_hook_test,
)


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("ITERATE_DATA_DIR", str(data))
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path / "config"))
    return data


def _settings(**hooks: list[object]) -> Settings:
    settings = Settings()
    settings.hooks = dict(hooks)  # type: ignore[assignment]
    return settings


def test_build_test_payload_marks_synthetic_and_skips_event_override() -> None:
    payload = build_test_payload(
        HookEvent.PRE_TOOL_USE,
        tool_name="bash",
        prompt="ignored-form",
        extra={"event": "stop", "cwd": "/repo", "nested": {"a": 1}},
    )
    assert payload["event"] == "pre_tool_use"
    assert payload["test"] is True
    assert payload["tool_name"] == "bash"
    # An explicit event in --payload never overrides the selected event.
    assert payload["cwd"] == "/repo"
    assert payload["nested"] == {"a": 1}


@pytest.mark.asyncio
async def test_dry_run_reports_matches_without_executing(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    view = await run_hook_test(
        project,
        HookEvent.SESSION_START,
        dry_run=True,
        settings=_settings(session_start=[CommandHookDefinition(command="touch canary")]),
    )
    assert view["summary"]["matched"] == 1
    assert view["summary"]["would_run"] == 1
    assert view["results"][0]["status"] == "would-run"
    assert not (project / "canary").exists()
    # Dry-run must not create the journal either.
    assert not journal_path(project).exists()


@pytest.mark.asyncio
async def test_live_command_hook_runs_and_lands_in_journal(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    view = await run_hook_test(
        project,
        HookEvent.SESSION_START,
        settings=_settings(
            session_start=[CommandHookDefinition(command="printf 'hooked' ", matcher="")]
        ),
    )
    row = view["results"][0]
    assert row["status"] == "pass"
    assert row["returncode"] == 0
    assert "hooked" in row["output"]
    page, total = list_hook_executions(project)
    assert total == 1
    assert page[0]["event"] == "session_start"
    assert page[0]["success"] is True


@pytest.mark.asyncio
async def test_failing_blocking_hook_is_reported_as_blocked(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    view = await run_hook_test(
        project,
        HookEvent.PRE_TOOL_USE,
        payload=build_test_payload(HookEvent.PRE_TOOL_USE, tool_name="bash"),
        settings=_settings(
            pre_tool_use=[
                CommandHookDefinition(
                    command="exit 3", matcher="bash", block_on_failure=True
                )
            ]
        ),
    )
    row = view["results"][0]
    assert row["status"] == "blocked"
    assert row["blocked"] is True
    assert row["returncode"] == 3
    assert view["summary"]["has_failures"] is True


@pytest.mark.asyncio
async def test_matcher_filters_hooks_for_the_synthetic_payload(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    settings = _settings(
        pre_tool_use=[
            CommandHookDefinition(command="printf bash", matcher="bash"),
            CommandHookDefinition(command="printf edit", matcher="*edit*"),
        ]
    )
    view = await run_hook_test(
        project,
        HookEvent.PRE_TOOL_USE,
        payload=build_test_payload(HookEvent.PRE_TOOL_USE, tool_name="bash"),
        settings=settings,
    )
    assert view["summary"]["matched"] == 1
    assert "bash" in view["results"][0]["output"]


@pytest.mark.asyncio
async def test_prompt_hook_is_skipped_without_a_model(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    view = await run_hook_test(
        project,
        HookEvent.STOP,
        settings=_settings(stop=[PromptHookDefinition(prompt="Did the tests pass?")]),
    )
    row = view["results"][0]
    assert row["status"] == "skipped"
    assert "--with-model" in row["reason"]
    assert view["summary"]["has_failures"] is False
    assert not journal_path(project).exists()


@pytest.mark.asyncio
async def test_dead_command_is_named_before_spawning(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    view = await run_hook_test(
        project,
        HookEvent.SESSION_START,
        settings=_settings(
            session_start=[CommandHookDefinition(command="iterate-missing-binary-xyz")]
        ),
    )
    row = view["results"][0]
    assert row["issues"]
    assert "not found on PATH" in row["issues"][0]
    # The executor still tried it (and failed); the static issue explains why.
    assert row["status"] in {"fail", "blocked"}


@pytest.mark.asyncio
async def test_http_hook_is_executed_against_a_real_endpoint(tmp_path: Path) -> None:
    """A local HTTP server receives the synthetic payload (end-to-end)."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib signature
            length = int(self.headers.get("Content-Length") or 0)
            received.append(json.loads(self.rfile.read(length) or b"{}"))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args: object) -> None:  # keep the test quiet
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        project = tmp_path / "repo"
        project.mkdir()
        port = server.server_address[1]
        view = await run_hook_test(
            project,
            HookEvent.NOTIFICATION,
            payload=build_test_payload(
                HookEvent.NOTIFICATION, notification_type="permission_prompt"
            ),
            settings=_settings(
                notification=[
                    HttpHookDefinition(url=f"http://127.0.0.1:{port}/hook")
                ]
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert view["results"][0]["status"] == "pass"
    assert view["results"][0]["status_code"] == 204
    assert received[0]["event"] == "notification"
    assert received[0]["payload"]["notification_type"] == "permission_prompt"


def test_cli_test_json_exit_0_on_pass(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _write_settings(project, _settings(session_start=[CommandHookDefinition(command="true")]))
    result = CliRunner().invoke(
        cli_app, ["hooks", "test", "session_start", "--project-root", str(project), "--json"]
    )
    assert result.exit_code == 0, result.output
    view = json.loads(result.output)
    assert view["summary"]["passed"] == 1
    assert view["payload"]["test"] is True


def test_cli_test_exit_1_on_blocking_failure(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _write_settings(
        project,
        _settings(
            pre_tool_use=[
                CommandHookDefinition(command="exit 1", matcher="bash", block_on_failure=True)
            ]
        ),
    )
    result = CliRunner().invoke(
        cli_app,
        [
            "hooks",
            "test",
            "pre_tool_use",
            "--project-root",
            str(project),
            "--tool-name",
            "bash",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "blocked" in result.output


def test_cli_unknown_event_suggests_a_valid_one(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli_app,
        ["hooks", "test", "pre-tool-use", "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 1
    combined = result.output + result.stderr
    assert "unknown hook event" in combined
    assert "pre_tool_use" in combined


def test_cli_bad_payload_is_a_clear_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli_app,
        ["hooks", "test", "stop", "--project-root", str(tmp_path), "--payload", "{bad"],
    )
    assert result.exit_code == 1
    combined = result.output + result.stderr
    assert "not valid JSON" in combined


def test_cli_require_match_exits_1_when_nothing_matches(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _write_settings(project, Settings())
    result = CliRunner().invoke(
        cli_app,
        [
            "hooks",
            "test",
            "stop",
            "--project-root",
            str(project),
            "--require-match",
        ],
    )
    assert result.exit_code == 1
    assert "no hook matched" in result.output + result.stderr


def test_cli_dry_run_and_markdown_out(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _write_settings(
        project,
        _settings(stop=[CommandHookDefinition(command="touch should-not-exist")]),
    )
    out = tmp_path / "notes.md"
    result = CliRunner().invoke(
        cli_app,
        [
            "hooks",
            "test",
            "stop",
            "--project-root",
            str(project),
            "--dry-run",
            "--format",
            "markdown",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (project / "should-not-exist").exists()
    assert "# Hook test" in out.read_text(encoding="utf-8")


def test_render_shapes() -> None:
    view = {
        "event": "pre_tool_use",
        "dry_run": False,
        "model_error": "",
        "summary": {
            "matched": 1,
            "passed": 0,
            "failed": 1,
            "blocked": 0,
            "skipped": 0,
            "would_run": 0,
            "has_failures": True,
        },
        "results": [
            {
                "type": "command",
                "source": "settings",
                "matcher": "bash",
                "status": "fail",
                "duration_ms": 12,
                "detail": "exit 1",
                "reason": "exit code 1",
            }
        ],
    }
    table = render_hook_test_table(view)
    assert "Hook test" in table
    assert "exit code 1" in table
    markdown = render_hook_test_markdown(view)
    assert "| command |" in markdown


def _write_settings(project: Path, settings: Settings) -> Path:
    """Persist settings so ``load_settings`` resolves them for the CLI path."""
    from iterate_harness.config.paths import get_config_file_path

    path = get_config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(settings.model_dump_json(indent=2))
    return path
