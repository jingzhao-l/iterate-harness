"""Tests for BackendRegistry: register, detect, and get_executor."""

from __future__ import annotations

import pytest

from iterate_harness.swarm.registry import BackendRegistry
from iterate_harness.swarm.types import TeammateExecutor


# ---------------------------------------------------------------------------
# Default registration
# ---------------------------------------------------------------------------


def test_registry_registers_subprocess_and_in_process():
    registry = BackendRegistry()
    available = registry.available_backends()
    assert "subprocess" in available
    assert "in_process" in available


def test_get_executor_subprocess():
    registry = BackendRegistry()
    executor = registry.get_executor("subprocess")
    assert executor is not None
    assert executor.type == "subprocess"


def test_get_executor_in_process():
    registry = BackendRegistry()
    executor = registry.get_executor("in_process")
    assert executor.type == "in_process"


def test_get_executor_unknown_raises():
    registry = BackendRegistry()
    with pytest.raises(KeyError, match="tmux"):
        registry.get_executor("tmux")


def test_get_executor_no_keyerror_for_every_detected_backend():
    """Every backend_type that detection can return must be registered, so
    get_executor() never raises KeyError on a real path (tmux/iterm2 are
    reserved and never returned)."""
    registry = BackendRegistry()
    for mode in ("auto", "in_process", "tmux", "iterm2", "some-future-mode"):
        preferred = registry.get_preferred_backend({"teammate_mode": mode})
        executor = registry.get_executor(preferred)
        assert executor is not None
        assert executor.type == preferred
    # auto-detect path
    assert registry.get_executor().type in ("subprocess", "in_process")


def test_detect_pane_backend_raises_reserved():
    """Pane backends (tmux/iterm2) are reserved/unregistered, so
    detect_pane_backend must raise rather than return an unusable type."""
    registry = BackendRegistry()
    with pytest.raises(RuntimeError, match="reserved"):
        registry.detect_pane_backend()


# ---------------------------------------------------------------------------
# detect_backend
# ---------------------------------------------------------------------------


def test_detect_backend_returns_subprocess_when_not_in_tmux(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    registry = BackendRegistry()
    detected = registry.detect_backend()
    assert detected == "subprocess"


def test_detect_backend_is_cached(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    registry = BackendRegistry()
    first = registry.detect_backend()
    second = registry.detect_backend()
    assert first == second


def test_detect_backend_reset_clears_cache(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    registry = BackendRegistry()
    _ = registry.detect_backend()
    assert registry._detected == "subprocess"
    registry.reset()
    assert registry._detected is None


# ---------------------------------------------------------------------------
# register_backend custom
# ---------------------------------------------------------------------------


def test_register_custom_backend():
    class FakeExecutor:
        type = "in_process"

        def is_available(self):
            return True

        async def spawn(self, config):
            ...

        async def send_message(self, agent_id, message):
            ...

        async def shutdown(self, agent_id, *, force=False):
            ...

    registry = BackendRegistry()
    fake = FakeExecutor()
    registry.register_backend(fake)
    assert registry.get_executor("in_process") is fake


def test_get_executor_auto_detect_returns_executor(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    registry = BackendRegistry()
    executor = registry.get_executor()  # auto-detect
    assert executor is not None
    assert isinstance(executor, TeammateExecutor)


# ---------------------------------------------------------------------------
# available_backends
# ---------------------------------------------------------------------------


def test_available_backends_sorted():
    registry = BackendRegistry()
    available = registry.available_backends()
    assert available == sorted(available)
