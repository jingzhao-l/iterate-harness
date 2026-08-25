"""Regression tests for trend_store persistence.

The old ``_save_library`` wrote to a fixed ``.json.tmp`` temp name with no
lock around the read-modify-write cycle: concurrent writers clobbered each
other's temp file (lost updates / torn writes) and a failed replace leaked
the temp file. Fix: ``tempfile.mkstemp`` (process-unique name) +
``os.replace`` (atomic swap), with cleanup on failure.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from iterate_harness.iterate import trend_store


def finding(file: str = "src/a.py", line: int = 10, dimension: str = "security") -> dict:
    return {
        "file": file,
        "line": line,
        "dimension": dimension,
        "severity": "high",
        "summary": "sql injection risk",
    }


def _tmp_leftovers(project_root: Path) -> list[Path]:
    lib_dir = trend_store.library_path(project_root).parent
    if not lib_dir.exists():
        return []
    return [p for p in lib_dir.iterdir() if p.suffix == ".tmp"]


def test_save_leaves_no_tmp_remnants(tmp_path):
    trend_store.record_run(tmp_path, [finding()], run_timestamp="2026-01-01T00:00:00+00:00")
    assert _tmp_leftovers(tmp_path) == []
    assert trend_store.load_library(tmp_path) != {}


def test_save_uses_process_unique_temp_names(tmp_path, monkeypatch):
    # The old fixed ".json.tmp" name made concurrent writers collide; each
    # save must go through mkstemp with a unique name instead.
    created: list[str] = []
    real_mkstemp = __import__("tempfile").mkstemp

    def spy_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        created.append(name)
        return fd, name

    monkeypatch.setattr("iterate_harness.iterate.trend_store.tempfile.mkstemp", spy_mkstemp)
    trend_store.record_run(tmp_path, [finding()], run_timestamp="2026-01-01T00:00:00+00:00")
    trend_store.record_run(tmp_path, [finding(file="b.py", line=2)], run_timestamp="2026-01-01T00:00:00+00:00")

    assert len(created) == 2
    assert len(set(created)) == 2  # unique per call — never the fixed name


def test_replace_failure_cleans_temp_file(tmp_path, monkeypatch):
    # A failed replace must not leak the temp file (and still surfaces the
    # error to the caller, matching the previous contract).
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("iterate_harness.iterate.trend_store.os.replace", boom)
    with pytest.raises(OSError):
        trend_store.record_run(tmp_path, [finding()], run_timestamp="2026-01-01T00:00:00+00:00")
    assert _tmp_leftovers(tmp_path) == []


def test_concurrent_record_runs_keep_library_consistent(tmp_path):
    # Two writers doing read-modify-write concurrently: no temp-file race
    # crashes, no torn JSON on disk, and no leftover temp files.
    f1 = finding(file="a.py", line=1)
    f2 = finding(file="b.py", line=2)
    stamp = "2026-01-01T00:00:00+00:00"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(trend_store.record_run, tmp_path, [f1], stamp),
            pool.submit(trend_store.record_run, tmp_path, [f2], stamp),
        ]
        for future in futures:
            future.result()  # must not raise

    library = trend_store.load_library(tmp_path)
    assert len(library) >= 1
    assert all(isinstance(value, dict) for value in library.values())
    assert _tmp_leftovers(tmp_path) == []
