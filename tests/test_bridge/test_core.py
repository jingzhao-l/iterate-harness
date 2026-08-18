"""Tests for bridge helpers."""

from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from iterate_harness.bridge import WorkSecret, build_sdk_url, decode_work_secret, encode_work_secret, spawn_session
from iterate_harness.bridge.manager import BridgeSessionManager
from iterate_harness.bridge.work_secret import _is_loopback_base


def test_work_secret_roundtrip():
    secret = WorkSecret(version=1, session_ingress_token="tok", api_base_url="https://api.example.com")
    encoded = encode_work_secret(secret)
    decoded = decode_work_secret(encoded)
    assert decoded == secret


def test_build_sdk_url():
    assert build_sdk_url("https://api.example.com", "abc").startswith("wss://")
    assert build_sdk_url("http://localhost:3000", "abc").startswith("ws://")


# ---------------------------------------------------------------------------
# Loopback detection (regression: substring matching over-triggered WS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Loopback hosts must select the plaintext ws:// + v2 protocol.
        ("http://localhost:3000", True),
        ("http://127.0.0.1:8080", True),
        ("http://[::1]:3000", True),
        ("localhost:3000", True),  # scheme-less host:port form
        # Enterprise domains that merely *contain* ``localhost`` / ``127.0.0.1``
        # as a substring must NOT be treated as loopback (this is the bug fixed:
        # the old code used ``"localhost" in url`` which false-positived here).
        ("https://mylocalhost-corplink.example", False),
        ("https://api127.0.0.1.example.com", False),
        ("https://api.example.com", False),
        ("", False),
    ],
)
def test_is_loopback_base(url: str, expected: bool):
    assert _is_loopback_base(url) is expected


def test_build_sdk_url_uses_wss_for_false_positive_domain():
    """A domain containing 'localhost' as a substring must use wss (not ws)."""
    url = build_sdk_url("https://mylocalhost-corplink.example", "abc")
    assert url.startswith("wss://")
    assert "/v1/" in url


def test_build_sdk_url_ipv6_loopback_uses_ws():
    assert build_sdk_url("http://[::1]:3000", "abc").startswith("ws://")


# ---------------------------------------------------------------------------
# BridgeSessionManager resource cleanup (regression: dicts leaked forever)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_cleans_up_after_session_completes(tmp_path: Path, monkeypatch):
    """When a session finishes, its entries must be dropped from every manager
    dict so they do not accumulate over many runs."""
    monkeypatch.setattr("iterate_harness.bridge.manager.get_data_dir", lambda: tmp_path)
    mgr = BridgeSessionManager()
    await mgr.spawn(session_id="done", command="echo manager-cleanup", cwd=tmp_path)
    # The copy task runs in the background; poll until it has finished and
    # dropped the session (an already-finished process exits almost instantly).
    for _ in range(100):
        if session_id := next(iter(mgr._copy_tasks), None):
            await mgr._copy_tasks[session_id]
        else:
            break
    assert mgr._sessions == {}
    assert mgr._copy_tasks == {}
    assert mgr._commands == {}
    assert mgr._output_paths == {}


@pytest.mark.asyncio
async def test_manager_stop_cancels_copy_task_and_cleans(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("iterate_harness.bridge.manager.get_data_dir", lambda: tmp_path)
    mgr = BridgeSessionManager()
    await mgr.spawn(session_id="stop-me", command="sleep 30", cwd=tmp_path)
    assert set(mgr._sessions) == {"stop-me"}
    assert "stop-me" in mgr._copy_tasks
    await mgr.stop("stop-me")
    assert mgr._sessions == {}
    assert mgr._copy_tasks == {}
    assert mgr._commands == {}
    assert mgr._output_paths == {}


@pytest.mark.asyncio
async def test_manager_stop_unknown_session_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("iterate_harness.bridge.manager.get_data_dir", lambda: tmp_path)
    mgr = BridgeSessionManager()

    with pytest.raises(ValueError, match="Unknown bridge session"):
        await mgr.stop("ghost")


@pytest.mark.asyncio
async def test_spawn_session_and_kill(tmp_path: Path):
    handle = await spawn_session(session_id="s1", command="sleep 30", cwd=tmp_path)
    assert handle.process.returncode is None
    await handle.kill()
    assert handle.process.returncode is not None


@pytest.mark.asyncio
async def test_spawn_session_merges_stderr_into_stdout(monkeypatch, tmp_path: Path):
    seen_kwargs = {}

    class FakeProcess:
        returncode = 0

        def terminate(self):
            pass

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def fake_create_shell_subprocess(*args, **kwargs):
        seen_kwargs.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(
        "iterate_harness.bridge.session_runner.create_shell_subprocess",
        fake_create_shell_subprocess,
    )

    await spawn_session(session_id="s1", command="printf err >&2", cwd=tmp_path)

    assert seen_kwargs["stdout"] is asyncio.subprocess.PIPE
    assert seen_kwargs["stderr"] is asyncio.subprocess.STDOUT
