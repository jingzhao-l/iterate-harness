"""CLI smoke tests."""

import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import iterate_harness.cli as cli
from iterate_harness.config import load_settings
from iterate_harness.config.settings import Settings
from iterate_harness.mcp.types import McpStdioServerConfig


app = cli.app


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """Redirect the unattended-automation data dir (cron registry/history) so
    `schedule` / `cron` tests never touch a developer's real state."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture()
def git_repo(tmp_path: Path, monkeypatch) -> Path:
    """A throwaway git repo with git on PATH (sandbox-safe)."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/local/bin")
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": "/tmp",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, env=env, capture_output=True)
    return repo


@pytest.fixture(autouse=True)
def _isolate_credential_backend(monkeypatch):
    """Pin credential storage to the (per-test) file backend.

    The real OS keyring is machine-wide and leaks configured state between
    tests (e.g. a developer's actual ``deepseek`` key makes ``setup`` take the
    interactive update path instead of the fresh-setup path). Every CLI test
    isolates ``ITERATE_CONFIG_DIR`` already; forcing ``_keyring_available`` to
    False routes all credential reads/writes through that isolated file.
    """
    from iterate_harness.auth import storage as storage_module

    monkeypatch.setattr(storage_module, "_keyring_available", lambda: False)


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--help"],
        env={"NO_COLOR": "1", "COLUMNS": "160"},
    )
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert result.exit_code == 0
    assert "iterate" in plain_output
    assert "multi-round review & fix harness" in plain_output
    assert "setup" in plain_output
    assert "--dry-run" in plain_output


def test_setup_flow_selects_profile_and_model(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))

    selected = []

    def fake_select(statuses, default_value=None):
        selected.append((tuple(statuses.keys()), default_value))
        return "deepseek"

    captured_keys = []

    def fake_keyflow_run(self):
        captured_keys.append(self.provider)
        return "fake-key"

    monkeypatch.setattr("iterate_harness.cli._select_setup_workflow", fake_select)
    monkeypatch.setattr("iterate_harness.cli._prompt_model_for_profile", lambda profile: "deepseek-chat")
    monkeypatch.setattr("iterate_harness.auth.flows.ApiKeyFlow.run", fake_keyflow_run)

    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0
    assert "Setup complete:" in result.output
    assert captured_keys == ["deepseek"]

    settings = load_settings()
    assert settings.active_profile == "deepseek"
    assert settings.resolve_profile()[1].last_model == "deepseek-chat"


def test_select_from_menu_uses_questionary_when_tty(monkeypatch):
    answers = []

    class _Prompt:
        def ask(self):
            return "deepseek"

    fake_questionary = types.SimpleNamespace(
        Choice=lambda title, value, checked=False: {
            "title": title,
            "value": value,
            "checked": checked,
        },
        select=lambda title, choices, default=None: answers.append((title, choices, default)) or _Prompt(),
    )

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys, "__stdin__", sys.stdin)
    monkeypatch.setattr(cli.sys, "__stdout__", sys.stdout)
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    result = cli._select_from_menu(
        "Choose a provider workflow:",
        [("deepseek", "DeepSeek"), ("claude-api", "Claude API")],
        default_value="deepseek",
    )

    assert result == "deepseek"
    assert answers


def test_setup_flow_existing_api_key_profile_can_update_secret(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from iterate_harness.auth.manager import AuthManager
    from iterate_harness.auth.storage import load_credential

    manager = AuthManager()
    manager.store_profile_credential("openai-compatible", "api_key", "old-key")

    selections = iter(["openai-compatible", "openai-compatible"])
    monkeypatch.setattr("iterate_harness.cli._select_setup_workflow", lambda *args, **kwargs: next(selections))
    monkeypatch.setattr("iterate_harness.cli._select_from_menu", lambda *args, **kwargs: next(selections))
    monkeypatch.setattr("iterate_harness.cli._confirm_prompt", lambda *args, **kwargs: True)
    monkeypatch.setattr("iterate_harness.auth.flows.ApiKeyFlow.run", lambda self: "new-key")
    monkeypatch.setattr("iterate_harness.cli._prompt_model_for_profile", lambda profile: "gpt-4.1")

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert "Setup complete:" in result.output
    assert load_credential("openai", "api_key") == "new-key"


def test_setup_flow_creates_kimi_profile_with_profile_scoped_key(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
    # Prevent env var leakage from overriding the configured api_key
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    selections = iter(["claude-api", "kimi-anthropic"])
    prompts = iter(
        [
            "https://api.moonshot.cn/anthropic",
            "kimi-k2.5",
        ]
    )

    monkeypatch.setattr("iterate_harness.cli._select_setup_workflow", lambda *args, **kwargs: next(selections))
    monkeypatch.setattr("iterate_harness.cli._select_from_menu", lambda *args, **kwargs: next(selections))
    monkeypatch.setattr("iterate_harness.cli._text_prompt", lambda *args, **kwargs: next(prompts))
    monkeypatch.setattr("iterate_harness.auth.flows.ApiKeyFlow.run", lambda self: "sk-kimi-test")

    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0
    assert "Setup complete:" in result.output
    assert "- profile: kimi-anthropic" in result.output

    settings = load_settings()
    assert settings.active_profile == "kimi-anthropic"
    profile = settings.resolve_profile()[1]
    assert profile.base_url == "https://api.moonshot.cn/anthropic"
    assert profile.credential_slot == "kimi-anthropic"
    assert profile.allowed_models == ["kimi-k2.5"]

    from iterate_harness.auth.storage import load_credential

    assert load_credential("profile:kimi-anthropic", "api_key") == "sk-kimi-test"


def test_provider_add_can_store_profile_api_key(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))

    from iterate_harness.auth.storage import load_credential

    result = runner.invoke(
        app,
        [
            "provider",
            "add",
            "custom-openai",
            "--label",
            "Custom OpenAI",
            "--provider",
            "openai",
            "--api-format",
            "openai",
            "--auth-source",
            "openai_api_key",
            "--model",
            "gpt-4.1",
            "--credential-slot",
            "custom-openai",
            "--api-key",
            "new-key",
        ],
    )

    assert result.exit_code == 0
    assert "API key set" in result.output
    assert load_credential("profile:custom-openai", "api_key") == "new-key"


def test_provider_edit_can_replace_profile_api_key(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from iterate_harness.auth.manager import AuthManager
    from iterate_harness.auth.storage import load_credential

    manager = AuthManager()
    manager.store_profile_credential("openai-compatible", "api_key", "old-key")

    result = runner.invoke(app, ["provider", "edit", "openai-compatible", "--api-key", "new-key"])

    assert result.exit_code == 0
    assert "API key replaced" in result.output
    assert load_credential("openai", "api_key") == "new-key"


def test_login_provider_surfaces_store_failure(tmp_path: Path, monkeypatch, capsys):
    """A failed AuthManager store must not end in a silent 'saved' success."""
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("iterate_harness.auth.flows.ApiKeyFlow.run", lambda self: "sk-xyz")

    def boom(self, *args, **kwargs):
        raise OSError("keyring unavailable")

    monkeypatch.setattr("iterate_harness.auth.manager.AuthManager.store_credential", boom)

    with pytest.raises(typer.Exit) as exc_info:
        cli._login_provider("openai")
    assert exc_info.value.exit_code == 1

    captured = capsys.readouterr()
    assert "Failed to save" in captured.err
    assert "API key saved" not in captured.out


def test_dangerously_skip_permissions_passes_full_auto_to_run_repl(monkeypatch):
    runner = CliRunner()
    captured = {}

    async def fake_run_repl(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("iterate_harness.ui.app.run_repl", fake_run_repl)

    result = runner.invoke(app, ["--dangerously-skip-permissions"])

    assert result.exit_code == 0
    assert captured["permission_mode"] == "full_auto"


def test_task_worker_flag_routes_to_run_task_worker(monkeypatch):
    runner = CliRunner()
    captured = {}

    async def fake_run_task_worker(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("iterate_harness.ui.app.run_task_worker", fake_run_task_worker)

    result = runner.invoke(app, ["--task-worker", "--model", "kimi-k2.5"])

    assert result.exit_code == 0
    assert captured["model"] == "kimi-k2.5"


def test_dry_run_uses_preview_builder_and_skips_repl(monkeypatch):
    runner = CliRunner()
    captured = {}

    def fake_build_dry_run_preview(**kwargs):
        captured.update(kwargs)
        return {
            "cwd": kwargs["cwd"],
            "prompt_preview": kwargs["prompt"],
            "settings": {
                "active_profile": "claude-api",
                "profile_label": "Claude API",
                "provider": "anthropic",
                "api_format": "anthropic",
                "model": "claude-sonnet-4-6",
                "base_url": "",
                "permission_mode": "default",
                "max_turns": 200,
                "effort": "medium",
                "passes": 1,
            },
            "validation": {
                "auth_status": "configured",
                "api_client": {"status": "ok"},
                "system_prompt_chars": 123,
                "mcp_validation": "skipped",
            },
            "entrypoint": {"kind": "model_prompt", "detail": "preview only"},
            "plugins": [],
            "skills": [],
            "commands": [],
            "tools": [],
            "mcp_servers": [],
            "system_prompt_preview": "preview",
        }

    async def fake_run_repl(**kwargs):  # pragma: no cover - should never be called
        raise AssertionError(f"run_repl should not be called during dry-run: {kwargs}")

    monkeypatch.setattr("iterate_harness.cli._build_dry_run_preview", fake_build_dry_run_preview)
    monkeypatch.setattr("iterate_harness.ui.app.run_repl", fake_run_repl)

    result = runner.invoke(app, ["--dry-run", "--print", "ship it", "--model", "gpt-5.4"])

    assert result.exit_code == 0
    assert captured["prompt"] == "ship it"
    assert captured["model"] == "gpt-5.4"
    assert "IterateHarness Dry Run" in result.output
    assert "ship it" in result.output


def test_dry_run_json_output(monkeypatch):
    runner = CliRunner()

    def fake_build_dry_run_preview(**kwargs):
        return {
            "mode": "dry-run",
            "cwd": kwargs["cwd"],
            "prompt": kwargs["prompt"],
            "prompt_preview": kwargs["prompt"],
            "settings": {
                "active_profile": "claude-api",
                "profile_label": "Claude API",
                "provider": "anthropic",
                "api_format": "anthropic",
                "model": "claude-sonnet-4-6",
                "base_url": "",
                "permission_mode": "default",
                "max_turns": 200,
                "effort": "medium",
                "passes": 1,
            },
            "validation": {
                "auth_status": "configured",
                "api_client": {"status": "ok"},
                "system_prompt_chars": 123,
                "mcp_validation": "skipped",
            },
            "entrypoint": {"kind": "interactive_session", "detail": "wait"},
            "plugins": [],
            "skills": [],
            "commands": [],
            "tools": [],
            "mcp_servers": [],
            "system_prompt_preview": "preview",
        }

    monkeypatch.setattr("iterate_harness.cli._build_dry_run_preview", fake_build_dry_run_preview)

    result = runner.invoke(app, ["--dry-run", "--output-format", "json", "--print", "preview this"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["mode"] == "dry-run"
    assert payload["prompt"] == "preview this"


def test_dry_run_rejects_continue_resume(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("iterate_harness.cli._build_dry_run_preview", lambda **kwargs: {"mode": "dry-run"})

    result = runner.invoke(app, ["--dry-run", "--continue"])

    assert result.exit_code == 1
    assert "--dry-run does not support --continue/--resume yet" in result.output


def test_build_dry_run_preview_classifies_slash_command_and_flags_bad_mcp(monkeypatch, tmp_path: Path):
    settings = Settings(
        api_key="sk-test",
        mcp_servers={
            "broken": McpStdioServerConfig(command="definitely-not-a-real-command-iterate_harness"),
        },
    )

    class _FakeSkillRegistry:
        def list_skills(self):
            return []

    monkeypatch.setattr("iterate_harness.config.load_settings", lambda: settings)
    monkeypatch.setattr(
        "iterate_harness.api.provider.detect_provider",
        lambda settings: types.SimpleNamespace(name="anthropic"),
    )
    monkeypatch.setattr("iterate_harness.api.provider.auth_status", lambda settings: "configured")
    monkeypatch.setattr("iterate_harness.plugins.load_plugins", lambda settings, cwd: [])
    monkeypatch.setattr("iterate_harness.skills.load_skill_registry", lambda cwd, settings=None: _FakeSkillRegistry())
    monkeypatch.setattr("iterate_harness.prompts.context.build_runtime_system_prompt", lambda *args, **kwargs: "preview prompt")
    monkeypatch.setattr("iterate_harness.ui.runtime._resolve_api_client_from_settings", lambda settings: object())

    preview = cli._build_dry_run_preview(
        prompt="/config show",
        cwd=str(tmp_path),
        model=None,
        max_turns=None,
        base_url=None,
        system_prompt=None,
        append_system_prompt=None,
        api_key=None,
        api_format=None,
        permission_mode=None,
    )

    assert preview["entrypoint"]["kind"] == "slash_command"
    assert preview["entrypoint"]["command"] == "config"
    assert preview["entrypoint"]["remote_invocable"] is False
    assert preview["entrypoint"]["remote_admin_opt_in"] is True
    assert preview["entrypoint"]["behavior"] == "stateful"
    assert preview["validation"]["mcp_errors"] == 1
    assert preview["mcp_servers"][0]["status"] == "error"
    assert "command not found in PATH" in preview["mcp_servers"][0]["issues"][0]


def test_build_dry_run_preview_sets_blocked_when_model_prompt_lacks_auth(monkeypatch, tmp_path: Path):
    settings = Settings(api_key="")

    class _FakeSkillRegistry:
        def list_skills(self):
            return []

    monkeypatch.setattr("iterate_harness.config.load_settings", lambda: settings)
    monkeypatch.setattr(
        "iterate_harness.api.provider.detect_provider",
        lambda settings: types.SimpleNamespace(name="anthropic"),
    )
    monkeypatch.setattr("iterate_harness.api.provider.auth_status", lambda settings: "missing")
    monkeypatch.setattr("iterate_harness.plugins.load_plugins", lambda settings, cwd: [])
    monkeypatch.setattr("iterate_harness.skills.load_skill_registry", lambda cwd, settings=None: _FakeSkillRegistry())
    monkeypatch.setattr("iterate_harness.prompts.context.build_runtime_system_prompt", lambda *args, **kwargs: "preview prompt")

    def fake_resolve_api_client(settings):
        raise SystemExit(1)

    monkeypatch.setattr("iterate_harness.ui.runtime._resolve_api_client_from_settings", fake_resolve_api_client)

    preview = cli._build_dry_run_preview(
        prompt="fix the failing tests",
        cwd=str(tmp_path),
        model=None,
        max_turns=None,
        base_url=None,
        system_prompt=None,
        append_system_prompt=None,
        api_key=None,
        api_format=None,
        permission_mode=None,
    )

    assert preview["entrypoint"]["kind"] == "model_prompt"
    assert preview["readiness"]["level"] == "blocked"
    assert any("runtime client" in reason.lower() for reason in preview["readiness"]["reasons"])
    assert any("authentication" in action.lower() or "profile" in action.lower() for action in preview["readiness"]["next_actions"])


def test_build_dry_run_preview_recommends_matching_skills_and_tools(monkeypatch, tmp_path: Path):
    settings = Settings(api_key="sk-test")

    class _FakeSkillRegistry:
        def list_skills(self):
            return [
                types.SimpleNamespace(
                    name="review",
                    description="Review code for bugs and regressions.",
                    content="Use this when reviewing bug fixes and regressions.",
                    source="bundled",
                ),
                types.SimpleNamespace(
                    name="plan",
                    description="Plan implementation work before coding.",
                    content="Use this to design an implementation plan.",
                    source="bundled",
                ),
            ]

    class _FakeToolRegistry:
        def to_api_schema(self):
            return [
                {
                    "name": "grep",
                    "description": "Search code for bug patterns and failing lines.",
                    "input_schema": {"properties": {"pattern": {}, "root": {}}, "required": ["pattern"]},
                },
                {
                    "name": "read_file",
                    "description": "Read files from disk.",
                    "input_schema": {"properties": {"path": {}, "offset": {}}, "required": ["path"]},
                },
            ]

    monkeypatch.setattr("iterate_harness.config.load_settings", lambda: settings)
    monkeypatch.setattr(
        "iterate_harness.api.provider.detect_provider",
        lambda settings: types.SimpleNamespace(name="anthropic"),
    )
    monkeypatch.setattr("iterate_harness.api.provider.auth_status", lambda settings: "configured")
    monkeypatch.setattr("iterate_harness.plugins.load_plugins", lambda settings, cwd: [])
    monkeypatch.setattr("iterate_harness.skills.load_skill_registry", lambda cwd, settings=None: _FakeSkillRegistry())
    monkeypatch.setattr("iterate_harness.tools.create_default_tool_registry", lambda: _FakeToolRegistry())
    monkeypatch.setattr("iterate_harness.prompts.context.build_runtime_system_prompt", lambda *args, **kwargs: "preview prompt")
    monkeypatch.setattr("iterate_harness.ui.runtime._resolve_api_client_from_settings", lambda settings: object())

    preview = cli._build_dry_run_preview(
        prompt="review this bug fix and grep for failing tests",
        cwd=str(tmp_path),
        model=None,
        max_turns=None,
        base_url=None,
        system_prompt=None,
        append_system_prompt=None,
        api_key=None,
        api_format=None,
        permission_mode=None,
    )

    recommended_skills = [entry["name"] for entry in preview["recommendations"]["skills"]]
    recommended_tools = [entry["name"] for entry in preview["recommendations"]["tools"]]

    assert preview["readiness"]["level"] == "ready"
    assert any("you can run this prompt directly" in action.lower() for action in preview["readiness"]["next_actions"])
    assert "review" in recommended_skills
    assert "grep" in recommended_tools


# ---- unattended automation commands (schedule/hook/cron) ----


class TestIterateScheduleCommand:
    def test_schedule_add_status_remove_lifecycle(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        add = runner.invoke(
            app, ["iterate", "schedule", "add", "--cron", "0 9 * * 1-5", "--ref", "origin/main"]
        )
        assert add.exit_code == 0, add.output
        assert "0 9 * * 1-5" in add.output
        assert "origin/main" in add.output

        status = runner.invoke(app, ["iterate", "schedule", "status"])
        assert status.exit_code == 0, status.output
        assert "Schedule:   0 9 * * 1-5" in status.output
        assert "Last run:   never" in status.output

        remove = runner.invoke(app, ["iterate", "schedule", "remove"])
        assert remove.exit_code == 0, remove.output
        assert "removed" in remove.output

        empty = runner.invoke(app, ["iterate", "schedule", "status"])
        assert empty.exit_code == 0, empty.output
        assert "No scheduled quick-review job" in empty.output

    def test_schedule_add_requires_cron(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app, ["iterate", "schedule", "add"])
        assert result.exit_code != 0
        assert "--cron is required" in result.output

    def test_schedule_invalid_action_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app, ["iterate", "schedule", "pause"])
        assert result.exit_code != 0
        assert "action must be add|remove|status" in result.output

    def test_schedule_invalid_cron_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app, ["iterate", "schedule", "add", "--cron", "not-cron"])
        assert result.exit_code != 0
        assert "cron" in result.output


class TestIterateCronCommand:
    def test_cron_status_empty(self):
        result = CliRunner().invoke(app, ["iterate", "cron", "status"])
        assert result.exit_code == 0, result.output
        assert "Running:" in result.output

    def test_cron_status_json(self):
        result = CliRunner().invoke(app, ["iterate", "cron", "status", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "running" in payload
        assert "total_jobs" in payload

    def test_cron_history_empty(self):
        result = CliRunner().invoke(app, ["iterate", "cron", "history"])
        assert result.exit_code == 0, result.output
        assert "No cron job executions recorded yet." in result.output

    def test_cron_invalid_action_rejected(self):
        result = CliRunner().invoke(app, ["iterate", "cron", "explode"])
        assert result.exit_code != 0
        assert "action must be start|stop|status|history" in result.output


class TestIterateHookCommand:
    def test_hook_status_outside_git_repo_degrades(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app, ["iterate", "hook", "status"])
        assert result.exit_code == 0, result.output
        assert "not installed" in result.output

    def test_hook_install_and_uninstall_via_cli(self, tmp_path, monkeypatch, git_repo):
        monkeypatch.chdir(git_repo)
        runner = CliRunner()

        installed = runner.invoke(app, ["iterate", "hook", "install", "--fail-on", "high"])
        assert installed.exit_code == 0, installed.output
        assert "pre-commit" in installed.output

        status = runner.invoke(app, ["iterate", "hook", "status"])
        assert status.exit_code == 0, status.output
        assert "installed" in status.output

        removed = runner.invoke(app, ["iterate", "hook", "uninstall"])
        assert removed.exit_code == 0, removed.output
        assert "removed" in removed.output

        gone = runner.invoke(app, ["iterate", "hook", "status"])
        assert gone.exit_code == 0, gone.output
        assert "not installed" in gone.output

    def test_hook_invalid_action_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app, ["iterate", "hook", "execute"])
        assert result.exit_code != 0
        assert "action must be install|uninstall|status" in result.output


