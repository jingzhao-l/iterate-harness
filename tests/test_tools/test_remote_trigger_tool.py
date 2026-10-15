"""Regression tests: remote_trigger must bound its inputs and collected output."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import iterate_harness.tools.remote_trigger_tool as remote_trigger_module
from iterate_harness.tools.base import ToolExecutionContext
from iterate_harness.tools.remote_trigger_tool import RemoteTriggerTool, RemoteTriggerToolInput


@pytest.fixture()
def _stub_cron_job(monkeypatch):
    def _install(command: str, *, cwd: str | None = None) -> None:
        job: dict[str, object] = {"command": command}
        if cwd is not None:
            job["cwd"] = cwd
        monkeypatch.setattr(remote_trigger_module, "get_cron_job", lambda name: job if name == "job" else None)

    return _install


@pytest.mark.asyncio
async def test_remote_trigger_caps_huge_job_output(_stub_cron_job, tmp_path: Path):
    """A runaway job must not stream megabytes back into the model context."""
    _stub_cron_job(f'"{sys.executable}" -c "print(\'x\' * 200000)"')

    result = await RemoteTriggerTool().execute(
        RemoteTriggerToolInput(name="job", timeout_seconds=30),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert result.metadata["output_truncated"] is True
    assert "...[job output truncated]" in result.output
    assert len(result.output) < 2 * remote_trigger_module._TRIGGER_OUTPUT_MAX_BYTES


@pytest.mark.asyncio
async def test_remote_trigger_small_output_is_not_truncated(_stub_cron_job, tmp_path: Path):
    _stub_cron_job(f'"{sys.executable}" -c "print(\'small output\')"')

    result = await RemoteTriggerTool().execute(
        RemoteTriggerToolInput(name="job", timeout_seconds=30),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert result.metadata["output_truncated"] is False
    assert "small output" in result.output
    assert "...[job output truncated]" not in result.output


@pytest.mark.asyncio
async def test_remote_trigger_timeout_returns_error(_stub_cron_job, tmp_path: Path):
    _stub_cron_job(f'"{sys.executable}" -c "import time; time.sleep(30)"')

    result = await RemoteTriggerTool().execute(
        RemoteTriggerToolInput(name="job", timeout_seconds=1),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "timed out after 1 seconds" in result.output


def test_remote_trigger_rejects_oversized_job_name():
    with pytest.raises(ValidationError):
        RemoteTriggerToolInput(name="x" * 4096)


def test_remote_trigger_rejects_empty_job_name():
    with pytest.raises(ValidationError):
        RemoteTriggerToolInput(name="")
