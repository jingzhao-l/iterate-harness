"""Regression tests for the defensive layer hardening.

Covers the review fixes:

- ``FileTransactionBuffer`` reference-counts pending mutations per path:
  the first commit of a multiply-snapshotted file keeps the snapshot so a
  sibling mutation's rollback can still restore the shared original.
- Refused invariant commands name the offending metacharacters so the
  operator can fix the config entry.
"""

from __future__ import annotations

from pathlib import Path

from iterate_harness.defensive.invariants import check_invariants
from iterate_harness.defensive.transaction import FileTransactionBuffer


def test_snapshot_refcount_survives_sibling_commit(tmp_path: Path) -> None:
    target = tmp_path / "config.txt"
    target.write_text("original", encoding="utf-8")

    buffer = FileTransactionBuffer(tmp_path)
    # Two parallel mutations of the same file snapshot the same original.
    buffer.snapshot(target)
    buffer.snapshot(target)
    assert buffer.pending == [target.resolve()]

    # The first mutation commits (its edit verified good) — the snapshot must
    # stay for the sibling still in flight.
    target.write_text("edit-1", encoding="utf-8")
    buffer.commit(target)
    assert buffer.pending == [target.resolve()]

    # The second mutation fails → rollback restores the shared original.
    target.write_text("edit-2", encoding="utf-8")
    restored = buffer.rollback(target)
    assert restored == [target.resolve()]
    assert target.read_text(encoding="utf-8") == "original"
    assert buffer.pending == []


def test_single_mutation_commit_drops_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "solo.txt"
    target.write_text("original", encoding="utf-8")

    buffer = FileTransactionBuffer(tmp_path)
    buffer.snapshot(target)
    buffer.commit(target)

    assert buffer.pending == []
    # With no snapshot left, a stray rollback is a no-op.
    target.write_text("after-commit", encoding="utf-8")
    assert buffer.rollback(target) == []
    assert target.read_text(encoding="utf-8") == "after-commit"


def test_full_rollback_clears_refcounts(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    for path in (first, second):
        path.write_text("original", encoding="utf-8")

    buffer = FileTransactionBuffer(tmp_path)
    buffer.snapshot(first)
    buffer.snapshot(first)
    buffer.snapshot(second)

    restored = buffer.rollback()  # paths=None → full rollback
    assert set(restored) == {first.resolve(), second.resolve()}
    assert buffer.pending == []


def test_refused_invariant_names_offending_metachars(tmp_path: Path) -> None:
    report = check_invariants(tmp_path, commands={"mymodule": ["pytest *"]})

    assert report.passed is False
    refused = [v for v in report.violations if v.kind == "refused"]
    assert len(refused) == 1
    assert "*" in refused[0].detail
    assert "mymodule" in refused[0].label
