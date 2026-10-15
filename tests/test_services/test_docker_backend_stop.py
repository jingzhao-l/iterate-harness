"""Regression tests for ``DockerSandboxSession.stop`` (sandbox/docker_backend.py).

The sandbox package has its own test tree, which is out of scope for this
change set, so the stop() contract is pinned here: a stop that never
confirmed the container was gone used to clear ``_running`` anyway, so the
caller believed the container was gone while it kept running — and a retry
was impossible because ``_running`` was already False.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.config import Settings
from iterate_harness.sandbox.docker_backend import DockerSandboxSession


class _FakeDockerProcess:
    """Answers ``communicate()`` according to the scripted subcommand mode."""

    def __init__(self, mode: str) -> None:
        self._mode = mode
        self.returncode: int | None = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._mode == "timeout":
            raise asyncio.TimeoutError()
        if self._mode == "error":
            raise OSError("docker daemon is unreachable")
        if self._mode == "true":
            return b"true\n", b""
        return b"false\n", b""


class _FakeDockerCLI:
    """Scriptable docker CLI stand-in that records every argv it is given."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.behaviour: dict[str, str] = {"stop": "ok", "kill": "ok", "inspect": "false"}

    async def __call__(self, *argv: object, **_kwargs: Any) -> _FakeDockerProcess:
        command = [str(part) for part in argv]
        self.calls.append(command)
        return _FakeDockerProcess(self.behaviour.get(command[1], "ok"))

    @property
    def subcommands(self) -> list[str]:
        return [call[1] for call in self.calls]


@pytest.fixture
def docker_cli(monkeypatch: pytest.MonkeyPatch) -> _FakeDockerCLI:
    cli = _FakeDockerCLI()
    monkeypatch.setattr(
        "iterate_harness.sandbox.docker_backend.shutil.which", lambda _name: "/usr/bin/docker"
    )
    monkeypatch.setattr(
        "iterate_harness.sandbox.docker_backend.asyncio.create_subprocess_exec", cli
    )
    return cli


def _running_session() -> DockerSandboxSession:
    session = DockerSandboxSession(
        settings=Settings(), session_id="stop-test", cwd=Path("/tmp")
    )
    session._running = True
    return session


async def test_stop_reports_success_once_the_container_is_gone(docker_cli: _FakeDockerCLI):
    session = _running_session()

    assert await session.stop() is True

    assert session.is_running is False
    # The container state was checked rather than assumed.
    assert docker_cli.subcommands == ["stop", "inspect"]


async def test_stop_keeps_the_session_active_when_the_container_survives(
    docker_cli: _FakeDockerCLI,
):
    docker_cli.behaviour["inspect"] = "true"
    session = _running_session()

    assert await session.stop() is False

    # Still marked running so a later stop/retry is possible.
    assert session.is_running is True


async def test_stop_force_kills_after_a_hung_docker_stop(docker_cli: _FakeDockerCLI):
    docker_cli.behaviour["stop"] = "timeout"
    session = _running_session()

    assert await session.stop() is True

    assert "kill" in docker_cli.subcommands
    assert session.is_running is False


async def test_stop_stays_active_when_nothing_can_be_confirmed(docker_cli: _FakeDockerCLI):
    """Unreachable daemon: neither stop, nor kill, nor inspect succeed — the
    conservative answer is "still running", not "definitely gone"."""
    docker_cli.behaviour.update({"stop": "timeout", "kill": "error", "inspect": "error"})
    session = _running_session()

    assert await session.stop() is False

    assert session.is_running is True


async def test_stop_without_a_running_container_touches_nothing(docker_cli: _FakeDockerCLI):
    session = DockerSandboxSession(
        settings=Settings(), session_id="stop-test", cwd=Path("/tmp")
    )

    assert await session.stop() is True

    assert docker_cli.calls == []
