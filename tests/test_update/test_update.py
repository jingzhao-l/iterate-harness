"""Tests for iterate_harness.update (self-update support + ih update CLI)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

import iterate_harness.cli as cli
import iterate_harness.update as update_module
from iterate_harness import __version__
from iterate_harness.update import (
    INSTALL_METHOD_NPM,
    INSTALL_METHOD_PIP,
    INSTALL_METHOD_SOURCE,
    UpdateResult,
    apply_update,
    compare_versions,
    current_install_method,
    detect_install_method,
    fetch_latest_version,
    is_check_stale,
    maybe_print_update_hint,
    normalize_version,
    perform_update,
    read_check_cache,
    write_check_cache,
)

app = cli.app


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: object = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self._text = text

    def json(self) -> object:
        return self._payload

    @property
    def text(self) -> str:
        return self._text


def make_runner(commands: list[list[str]], *, returncode: int = 0, stdout: str = "") -> object:
    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    return runner


def response_for(url: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
    if "releases/latest" in url:
        return FakeResponse(200, {"tag_name": "v9.9.9"})
    return FakeResponse(200, text='__version__ = "9.9.9"')


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def test_normalize_version_extracts_semver_from_tags():
    assert normalize_version("v1.2.3") == "1.2.3"
    assert normalize_version("1.2.3") == "1.2.3"
    assert normalize_version("release-10.20.30-beta") == "10.20.30"
    assert normalize_version(None) is None
    assert normalize_version("") is None
    assert normalize_version("not-a-version") is None


def test_compare_versions_ordering():
    assert compare_versions("1.2.3", "1.2.3") == 0
    assert compare_versions("1.2.3", "1.2.4") == 1
    assert compare_versions("1.2.3", "1.3.0") == 1
    assert compare_versions("1.9.9", "2.0.0") == 1
    assert compare_versions("1.2.4", "1.2.3") == -1
    assert compare_versions("2.0.0", "1.9.9") == -1
    assert compare_versions("1.2", "1.2.0") == 0


# ---------------------------------------------------------------------------
# Install-method detection
# ---------------------------------------------------------------------------


def test_detect_install_method_returns_npm_when_runtime_dir_present(tmp_path: Path):
    (tmp_path / ".iterate-harness-npm" / "venv").mkdir(parents=True)
    assert detect_install_method(tmp_path) == INSTALL_METHOD_NPM


def test_detect_install_method_returns_npm_when_running_inside_npm_venv(tmp_path: Path):
    npm_venv = tmp_path / ".iterate-harness-npm" / "venv" / "bin" / "python"
    assert detect_install_method(tmp_path, sys_prefix=str(npm_venv)) == INSTALL_METHOD_NPM


def test_detect_install_method_returns_source_when_checkout_or_venv_present(tmp_path: Path):
    (tmp_path / ".iterate-harness-src" / ".git").mkdir(parents=True)
    assert detect_install_method(tmp_path) == INSTALL_METHOD_SOURCE

    (tmp_path / ".iterate-harness-venv").mkdir()
    assert detect_install_method(tmp_path) == INSTALL_METHOD_SOURCE


def test_detect_install_method_returns_pip_for_unknown_layout(tmp_path: Path):
    assert detect_install_method(tmp_path) == INSTALL_METHOD_PIP


def test_current_install_method_returns_known_value():
    assert current_install_method() in {INSTALL_METHOD_NPM, INSTALL_METHOD_SOURCE, INSTALL_METHOD_PIP}


# ---------------------------------------------------------------------------
# Latest-version discovery
# ---------------------------------------------------------------------------


def test_fetch_latest_version_uses_releases_feed():
    assert fetch_latest_version(get_fn=response_for) == "9.9.9"


def test_fetch_latest_version_falls_back_to_raw_file(monkeypatch):
    calls: list[str] = []

    def flaky_get(url: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
        calls.append(url)
        if "releases/latest" in url:
            return FakeResponse(404, {})
        return FakeResponse(200, text='__version__ = "8.8.8"')

    assert fetch_latest_version(get_fn=flaky_get) == "8.8.8"
    assert len(calls) == 2


def test_fetch_latest_version_returns_none_when_all_sources_fail():
    def failing_get(url: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
        return FakeResponse(500, {})

    assert fetch_latest_version(get_fn=failing_get) is None


def test_fetch_latest_version_returns_none_when_get_raises():
    def raising_get(url: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
        raise OSError("network down")

    assert fetch_latest_version(get_fn=raising_get) is None


def test_default_get_falls_back_to_curl_when_httpx_fails(monkeypatch):
    """http (broken CA store) must not break discovery: curl takes over."""
    captured: list[list[str]] = []

    class FakeCurlResult:
        returncode = 0
        stdout = '{"tag_name": "v9.9.9"}'
        stderr = ""

    monkeypatch.setattr(
        "iterate_harness.update._which_curl_in_path",
        lambda: True,
    )

    def fake_run(argv, **kwargs):
        captured.append(list(argv))
        return FakeCurlResult()

    monkeypatch.setattr("iterate_harness.update.subprocess.run", fake_run)

    import httpx

    def broken_get(url: str, *, timeout: float, headers: dict[str, str]):
        raise httpx.ConnectError("SSL: CERTIFICATE_VERIFY_FAILED", request=None)

    def patched_default_get(url, *, timeout, headers):
        try:
            return broken_get(url, timeout=timeout, headers=headers)
        except httpx.ConnectError:
            return update_module._get_via_curl(url, timeout=timeout)

    # Use update_module's fallback directly: httpx raises, curl returns the feed.
    monkeypatch.setattr("iterate_harness.update._default_get", patched_default_get)
    assert update_module.fetch_latest_version() == "9.9.9"
    assert captured and captured[0][0] == "curl"


# ---------------------------------------------------------------------------
# Update-check cache
# ---------------------------------------------------------------------------


def test_cache_round_trip(tmp_path: Path):
    cache_path = tmp_path / "update-check.json"
    assert read_check_cache(cache_path) == {}
    write_check_cache(cache_path, "2.0.0")
    cache = read_check_cache(cache_path)
    assert cache["latest"] == "2.0.0"
    assert "checked_at" in cache


def test_cache_tolerates_corrupt_content(tmp_path: Path):
    cache_path = tmp_path / "update-check.json"
    cache_path.write_text("not json {", encoding="utf-8")
    assert read_check_cache(cache_path) == {}


def test_is_check_stale(tmp_path: Path):
    fresh = {"latest": "2.0.0", "checked_at": datetime.now(timezone.utc).isoformat()}
    assert is_check_stale(fresh, ttl_seconds=3600) is False

    stale = {
        "latest": "2.0.0",
        "checked_at": (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat(),
    }
    assert is_check_stale(stale, ttl_seconds=3600) is True

    assert is_check_stale({}) is True
    assert is_check_stale({"latest": "2.0.0", "checked_at": "garbage"}) is True


# ---------------------------------------------------------------------------
# Update application
# ---------------------------------------------------------------------------


def test_apply_update_source_runs_git_pull_and_editable_install(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / ".iterate-harness-src"
    (source_dir / ".git").mkdir(parents=True)
    venv_python = tmp_path / ".iterate-harness-venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    commands: list[list[str]] = []
    runner = make_runner(commands)

    result = apply_update(
        method=INSTALL_METHOD_SOURCE,
        home=tmp_path,
        latest_version="9.9.9",
        runner=runner,
    )

    assert result.success is True
    assert result.method == INSTALL_METHOD_SOURCE
    assert ["git", "-C", str(source_dir), "pull", "--ff-only"] in commands
    assert any(argv[:3] == [str(venv_python), "-m", "pip"] for argv in commands)


def test_apply_update_source_fails_when_checkout_missing(tmp_path: Path):
    commands: list[list[str]] = []
    runner = make_runner(commands)
    result = apply_update(
        method=INSTALL_METHOD_SOURCE,
        home=tmp_path,
        latest_version="9.9.9",
        runner=runner,
    )
    assert result.success is False
    assert "not found" in result.message
    assert commands == []


def test_apply_update_npm_runs_npm_install(tmp_path: Path, monkeypatch):
    commands: list[list[str]] = []
    runner = make_runner(commands)
    monkeypatch.setenv("ITERATE_HARNESS_NPM", "/fake/npm")

    result = apply_update(
        method=INSTALL_METHOD_NPM,
        home=tmp_path,
        latest_version="9.9.9",
        runner=runner,
    )

    assert result.success is True
    assert result.method == INSTALL_METHOD_NPM
    assert commands[0] == ["/fake/npm", "install", "-g", "iterate-harness@latest"]


def test_apply_update_npm_falls_back_to_pip_without_npm(tmp_path: Path, monkeypatch):
    commands: list[list[str]] = []
    runner = make_runner(commands)
    monkeypatch.delenv("ITERATE_HARNESS_NPM", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = apply_update(
        method=INSTALL_METHOD_NPM,
        home=tmp_path,
        latest_version="9.9.9",
        runner=runner,
    )

    assert result.success is True
    assert result.method == INSTALL_METHOD_PIP
    assert any("pip" in argv and "9.9.9" in " ".join(argv) for argv in commands)


def test_apply_update_pip_installs_release_tarball(tmp_path: Path):
    commands: list[list[str]] = []
    runner = make_runner(commands)

    result = apply_update(
        method=INSTALL_METHOD_PIP,
        home=tmp_path,
        latest_version="9.9.9",
        runner=runner,
    )

    assert result.success is True
    assert result.method == INSTALL_METHOD_PIP
    tarball = commands[0][-1]
    assert "archive/refs/tags/v9.9.9.tar.gz" in tarball


def test_apply_update_surfaces_step_failure(tmp_path: Path):
    commands: list[list[str]] = []
    runner = make_runner(commands, returncode=1, stdout="boom")

    result = apply_update(
        method=INSTALL_METHOD_PIP,
        home=tmp_path,
        latest_version="9.9.9",
        runner=runner,
    )

    assert result.success is False
    assert "exit 1" in result.message
    assert "boom" in result.message


def test_perform_update_verifies_new_version(tmp_path: Path):
    commands: list[list[str]] = []

    def verify_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="iterate_harness 9.9.9\n", stderr="")

    result = perform_update(
        current="1.0.0",
        home=tmp_path,
        method=INSTALL_METHOD_PIP,
        latest="9.9.9",
        runner=verify_runner,
    )

    assert result.success is True
    assert "now running 9.9.9" in result.message


def test_perform_update_reports_failure_without_verify(tmp_path: Path):
    commands: list[list[str]] = []
    runner = make_runner(commands, returncode=1, stdout="nope")
    result = perform_update(
        current="1.0.0",
        home=tmp_path,
        method=INSTALL_METHOD_PIP,
        latest="9.9.9",
        runner=runner,
    )
    assert result.success is False


# ---------------------------------------------------------------------------
# Update hint (--version auto-notification)
# ---------------------------------------------------------------------------


def test_maybe_print_update_hint_prints_when_newer(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setattr("iterate_harness.update.get_check_cache_path", lambda: tmp_path / "update-check.json")
    maybe_print_update_hint(current="1.0.0", cache_path=tmp_path / "update-check.json", get_fn=response_for)
    output = capsys.readouterr().out
    assert "9.9.9" in output
    assert "ih update" in output


def test_maybe_print_update_hint_silent_when_up_to_date(tmp_path: Path, capsys, monkeypatch):
    cache = {"latest": __version__, "checked_at": datetime.now(timezone.utc).isoformat()}
    (tmp_path / "update-check.json").write_text(json.dumps(cache), encoding="utf-8")
    maybe_print_update_hint(current=__version__, cache_path=tmp_path / "update-check.json")
    assert capsys.readouterr().out == ""


def test_maybe_print_update_hint_never_raises_on_network_error(tmp_path: Path, capsys):
    def boom(url: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
        raise OSError("down")

    maybe_print_update_hint(current="1.0.0", cache_path=tmp_path / "update-check.json", get_fn=boom)
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# CLI: ih update
# ---------------------------------------------------------------------------


def test_update_check_reports_already_up_to_date(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("iterate_harness.update.current_install_method", lambda: INSTALL_METHOD_PIP)
    monkeypatch.setattr("iterate_harness.update.fetch_latest_version", lambda get_fn=None, timeout=10.0: __version__)

    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "Already up to date" in result.output


def test_update_check_reports_new_version(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("iterate_harness.update.current_install_method", lambda: INSTALL_METHOD_PIP)
    monkeypatch.setattr("iterate_harness.update.fetch_latest_version", lambda get_fn=None, timeout=10.0: "9.9.9")

    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "New version available" in result.output
    assert "9.9.9" in result.output


def test_update_yes_applies_update(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("iterate_harness.update.current_install_method", lambda: INSTALL_METHOD_PIP)

    def fake_fetch(get_fn=None, timeout=10.0):
        return "9.9.9"

    def fake_perform_update(**kwargs):
        return UpdateResult(method=INSTALL_METHOD_PIP, success=True, message="installed 9.9.9")

    monkeypatch.setattr("iterate_harness.update.fetch_latest_version", fake_fetch)
    monkeypatch.setattr("iterate_harness.update.perform_update", fake_perform_update)

    result = runner.invoke(app, ["update", "--yes"])
    assert result.exit_code == 0
    assert "Updated: installed 9.9.9" in result.output


def test_update_confirms_before_applying(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("iterate_harness.update.current_install_method", lambda: INSTALL_METHOD_PIP)
    monkeypatch.setattr("iterate_harness.update.fetch_latest_version", lambda get_fn=None, timeout=10.0: "9.9.9")
    monkeypatch.setattr("iterate_harness.cli._confirm_prompt", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "iterate_harness.update.perform_update",
        lambda **kwargs: UpdateResult(method=INSTALL_METHOD_PIP, success=True, message="installed 9.9.9"),
    )

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "Updated: installed 9.9.9" in result.output


def test_update_declines_without_confirmation(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("iterate_harness.update.current_install_method", lambda: INSTALL_METHOD_PIP)
    monkeypatch.setattr("iterate_harness.update.fetch_latest_version", lambda get_fn=None, timeout=10.0: "9.9.9")
    monkeypatch.setattr("iterate_harness.cli._confirm_prompt", lambda *args, **kwargs: False)

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "Update cancelled" in result.output


def test_update_fetch_failure_exits_nonzero(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("iterate_harness.update.current_install_method", lambda: INSTALL_METHOD_PIP)
    monkeypatch.setattr("iterate_harness.update.fetch_latest_version", lambda get_fn=None, timeout=10.0: None)

    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 1
    assert "Could not reach the release feed" in result.output


def test_update_reports_failed_apply(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("iterate_harness.update.current_install_method", lambda: INSTALL_METHOD_PIP)
    monkeypatch.setattr("iterate_harness.update.fetch_latest_version", lambda get_fn=None, timeout=10.0: "9.9.9")
    monkeypatch.setattr(
        "iterate_harness.update.perform_update",
        lambda **kwargs: UpdateResult(method=INSTALL_METHOD_PIP, success=False, message="git pull failed"),
    )

    result = runner.invoke(app, ["update", "--yes"])
    assert result.exit_code == 1
    assert "Update failed: git pull failed" in result.output


def test_version_flag_emits_no_update_hint_when_disabled(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("ITERATE_HARNESS_UPDATE_CHECK", "0")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
    assert "Run `ih update`" not in result.output
