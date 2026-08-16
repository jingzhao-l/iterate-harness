"""Tests for the iterate checkpoint module (failure recovery)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from iterate_harness.iterate.checkpoint import (
    checkpoint_path,
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
)


def test_save_checkpoint_writes_atomic(tmp_path: Path):
    ckpt = save_checkpoint(
        tmp_path,
        round=3,
        new_findings=2,
        total_findings=7,
        per_dimension={"code_review": 4, "security": 3},
        converged=False,
        input_tokens=5000,
        output_tokens=12000,
        cost_usd=0.45,
        mode="dry-run",
    )
    assert ckpt.exists()
    assert ckpt.suffix == ".json"
    assert ckpt.parent.name == ".iterate"

    loaded = json.loads(ckpt.read_text(encoding="utf-8"))
    assert loaded["round"] == 3
    assert loaded["total_findings"] == 7
    assert loaded["per_dimension"]["code_review"] == 4
    assert loaded["converged"] is False
    assert loaded["cost_usd"] == 0.45
    assert loaded["mode"] == "dry-run"


def test_save_checkpoint_overwrites_previous(tmp_path: Path):
    save_checkpoint(tmp_path, round=1, new_findings=1, total_findings=2, per_dimension={}, converged=False, input_tokens=0, output_tokens=0, cost_usd=0.0, mode="dry-run")
    save_checkpoint(tmp_path, round=2, new_findings=1, total_findings=3, per_dimension={}, converged=False, input_tokens=0, output_tokens=0, cost_usd=0.0, mode="dry-run")

    loaded = load_checkpoint(tmp_path)
    assert loaded is not None
    assert loaded["round"] == 2
    assert loaded["total_findings"] == 3


def test_save_checkpoint_tmp_file_cleaned_on_success(tmp_path: Path):
    """No .json.tmp remnants after a successful save."""
    ckpt = save_checkpoint(tmp_path, round=1, new_findings=0, total_findings=0, per_dimension={}, converged=False, input_tokens=0, output_tokens=0, cost_usd=0.0, mode="dry-run")
    for f in ckpt.parent.iterdir():
        assert f.suffix != ".tmp"


def test_load_checkpoint_missing(tmp_path: Path):
    assert load_checkpoint(tmp_path) is None


def test_load_checkpoint_malformed(tmp_path: Path):
    path = checkpoint_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json\n", encoding="utf-8")
    assert load_checkpoint(tmp_path) is None


def test_load_checkpoint_empty(tmp_path: Path):
    path = checkpoint_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    assert load_checkpoint(tmp_path) is None


def test_clear_checkpoint_removes_file(tmp_path: Path):
    save_checkpoint(tmp_path, round=1, new_findings=0, total_findings=0, per_dimension={}, converged=False, input_tokens=0, output_tokens=0, cost_usd=0.0, mode="dry-run")
    assert checkpoint_path(tmp_path).exists()
    clear_checkpoint(tmp_path)
    assert not checkpoint_path(tmp_path).exists()


def test_clear_checkpoint_does_not_error_when_missing(tmp_path: Path):
    clear_checkpoint(tmp_path)  # should not raise


def test_save_checkpoint_survives_unwritable_dir(tmp_path: Path):
    """Checkpoint must never raise when the dir is not writable."""
    readonly = tmp_path / "readonly"
    readonly.mkdir(parents=True)
    os.chmod(readonly, 0o555)
    try:
        save_checkpoint(readonly, round=1, new_findings=0, total_findings=0, per_dimension={}, converged=False, input_tokens=0, output_tokens=0, cost_usd=0.0, mode="dry-run")
        # Should not raise
    finally:
        os.chmod(readonly, 0o755)