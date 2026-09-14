"""Tests for bounded tool-artifact offloading (design: disk hygiene).

``engine/query._offload_tool_output_if_needed`` offloads oversized tool
outputs to ``~/.iterate-harness/data/tool_artifacts``. These files are never
modified after writing, so without a retention bound a long-lived session
could accumulate unbounded disk usage. These tests pin the prune behavior:
pruning happens on write, keeps only the most recent ``tool_artifact_max_files``
files, and degrades gracefully (never raises into the query loop).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iterate_harness.engine import query
from iterate_harness.services.tool_outputs import tool_artifact_max_files


def _write_artifact(dir_path: Path, name: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / name
    path.write_text("x" * 10, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _bounded_max_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITERATE_TOOL_ARTIFACT_MAX_FILES", "3")


def test_offload_writes_and_prunes_to_bound(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ITERATE_TOOL_OUTPUT_INLINE_CHARS", "5")
    artifact_dir = tmp_path / "tool_artifacts"
    monkeypatch.setattr(query, "get_data_dir", lambda: tmp_path)

    big = "x" * 10_000
    inline, path = query._offload_tool_output_if_needed(
        tool_name="bash", tool_use_id="use-1", output=big
    )
    assert path is not None
    assert path.parent == artifact_dir
    assert path.exists()
    assert "Full output saved to" in inline

    for i in range(2, 6):
        query._offload_tool_output_if_needed(
            tool_name="bash", tool_use_id=f"use-{i}", output=big
        )

    left = [p.name for p in artifact_dir.iterdir() if p.suffix == ".txt"]
    assert len(left) == tool_artifact_max_files()


def test_small_output_not_offloaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ITERATE_TOOL_OUTPUT_INLINE_CHARS", "1000")
    monkeypatch.setattr(query, "get_data_dir", lambda: tmp_path)

    inline, path = query._offload_tool_output_if_needed(
        tool_name="bash", tool_use_id="use-1", output="small"
    )
    assert path is None
    assert inline == "small"


def test_prune_keeps_newest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact_dir = tmp_path / "tool_artifacts"
    for name in (
        "20260101-000000-bash-old.txt",
        "20260102-000000-bash-mid.txt",
        "20260103-000000-bash-new.txt",
        "20260104-000000-bash-newest.txt",
    ):
        _write_artifact(artifact_dir, name)

    query._prune_tool_artifacts(artifact_dir)

    left = {p.name for p in artifact_dir.iterdir() if p.suffix == ".txt"}
    assert left == {
        "20260102-000000-bash-mid.txt",
        "20260103-000000-bash-new.txt",
        "20260104-000000-bash-newest.txt",
    }


def test_prune_tolerates_non_tool_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact_dir = tmp_path / "tool_artifacts"
    _write_artifact(artifact_dir, "readme.md")
    _write_artifact(artifact_dir, "20260101-000000-bash-a.txt")
    _write_artifact(artifact_dir, "20260102-000000-bash-b.txt")
    _write_artifact(artifact_dir, "20260103-000000-bash-c.txt")

    query._prune_tool_artifacts(artifact_dir)

    left = {p.name for p in artifact_dir.iterdir() if p.is_file()}
    assert "readme.md" in left


def test_prune_missing_dir_is_noop() -> None:
    missing = Path("/nonexistent/iterate/nope")
    # Must not raise even when the directory does not exist.
    query._prune_tool_artifacts(missing)
    assert not missing.exists()


def test_tool_artifact_max_files_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITERATE_TOOL_ARTIFACT_MAX_FILES", "7")
    assert tool_artifact_max_files() == 7
    monkeypatch.setenv("ITERATE_TOOL_ARTIFACT_MAX_FILES", "bogus")
    assert tool_artifact_max_files() == 200