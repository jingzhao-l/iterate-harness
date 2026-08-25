"""Regression tests for checkpoint persistence.

The old ``save_checkpoint`` wrote to a fixed ``.json.tmp`` temp name, so two
concurrent writers used the same temp path and could tear each other's
partial write. Fix: ``tempfile.mkstemp`` (process-unique name) +
``os.replace`` (atomic swap) with the existing failure cleanup.
"""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from iterate_harness.iterate.checkpoint import (
    checkpoint_path,
    load_checkpoint,
    save_checkpoint,
)


def _save(tmp_path: Path, round_number: int) -> None:
    save_checkpoint(
        tmp_path,
        round=round_number,
        new_findings=round_number,
        total_findings=round_number,
        per_dimension={"security": round_number},
        converged=False,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        mode="dry-run",
    )


def test_save_checkpoint_uses_process_unique_temp_names(tmp_path, monkeypatch):
    # The old fixed "checkpoint.json.tmp" name made concurrent writers
    # collide; each save must go through mkstemp with a unique name instead.
    created: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def spy_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        created.append(name)
        return fd, name

    monkeypatch.setattr("iterate_harness.iterate.checkpoint.tempfile.mkstemp", spy_mkstemp)
    _save(tmp_path, 1)
    _save(tmp_path, 2)

    assert len(created) == 2
    assert len(set(created)) == 2  # unique per call — never the fixed name


def test_concurrent_saves_keep_checkpoint_loadable(tmp_path):
    # Concurrent writers on the same target: the checkpoint file must always
    # be a complete, parseable payload (atomic replace, unique temp files).
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_save, tmp_path, round_number)
            for round_number in range(1, 7)
        ]
        for future in futures:
            future.result()  # must not raise

    loaded = load_checkpoint(tmp_path)
    assert loaded is not None
    assert loaded["round"] in range(1, 7)
    assert isinstance(loaded["per_dimension"], dict)
    # No temp remnants after the concurrent writes.
    for f in checkpoint_path(tmp_path).parent.iterdir():
        assert f.suffix != ".tmp"


def test_replace_failure_still_returns_none_and_cleans(tmp_path, monkeypatch):
    # Contract: a failed write returns None (callers must not abort) and the
    # unique temp file must not be left behind.
    def boom(*args, **kwargs):
        raise OSError("write denied")

    monkeypatch.setattr("iterate_harness.iterate.checkpoint.os.replace", boom)
    result = save_checkpoint(
        tmp_path,
        round=1,
        new_findings=0,
        total_findings=0,
        per_dimension={},
        converged=False,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        mode="dry-run",
    )
    assert result is None
    for f in checkpoint_path(tmp_path).parent.iterdir():
        assert f.suffix != ".tmp"
