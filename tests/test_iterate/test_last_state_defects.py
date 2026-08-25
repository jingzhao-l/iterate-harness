"""Regression tests for last_state._summarize_checkpoint.

The old implementation interleaved the severity-count loop with the preview
truncation: once 3 preview findings were collected it stopped counting, so
severity distributions beyond the preview limit were lost (e.g. 3 low
findings followed by 2 critical ones reported critical=0).
"""

from __future__ import annotations

import json
from pathlib import Path

from iterate_harness.iterate.checkpoint import save_checkpoint
from iterate_harness.iterate.last_state import summarize_last_run


def _write_entries(project_root: Path, entries: list[dict]) -> None:
    log_dir = project_root / ".iterate"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "decision-log.jsonl").open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _save_ckpt(tmp_path: Path, total: int) -> None:
    save_checkpoint(
        tmp_path,
        round=1,
        new_findings=total,
        total_findings=total,
        per_dimension={"security": total},
        converged=False,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        mode="dry-run",
    )


def test_severity_counts_cover_findings_beyond_preview_limit(tmp_path: Path):
    # 5 findings in one review_result: 3 low then 2 critical. The old code
    # stopped counting after the 3rd previewed finding → critical reported 0.
    low = {"severity": "low", "file": "a.py", "dimension": "style", "summary": "style issue"}
    critical = {"severity": "critical", "file": "b.py", "dimension": "security", "summary": "real bug"}
    _write_entries(
        tmp_path,
        [
            {
                "timestamp": "t0",
                "round": 1,
                "type": "review_result",
                "data": {"findings": [low, low, low, critical, critical]},
            },
        ],
    )
    _save_ckpt(tmp_path, total=5)

    summary = summarize_last_run(str(tmp_path))
    assert summary is not None
    assert summary["severity"]["low"] == 3
    assert summary["severity"]["critical"] == 2
    # Preview stays capped at MAX_PREVIEW_FINDINGS.
    assert len(summary["preview"]) == 3


def test_counts_cover_all_entries_while_preview_stays_latest(tmp_path: Path):
    # Two review_result entries: 4 critical then 2 low (more recent). Counts
    # must cover all 6; preview must show the most recent 3.
    critical = {"severity": "critical", "file": "b.py", "dimension": "security", "summary": "bug"}
    low = {"severity": "low", "file": "a.py", "dimension": "style", "summary": "style"}
    _write_entries(
        tmp_path,
        [
            {"timestamp": "t0", "round": 1, "type": "review_result", "data": {"findings": [critical, critical, critical, critical]}},
            {"timestamp": "t1", "round": 2, "type": "review_result", "data": {"findings": [low, low]}},
        ],
    )
    _save_ckpt(tmp_path, total=6)

    summary = summarize_last_run(str(tmp_path))
    assert summary is not None
    assert summary["severity"]["critical"] == 4
    assert summary["severity"]["low"] == 2
    assert [p["severity"] for p in summary["preview"]] == ["low", "low", "critical"]


def test_no_findings_yields_zero_counts_and_empty_preview(tmp_path: Path):
    _write_entries(
        tmp_path,
        [
            {"timestamp": "t0", "round": 1, "type": "review_result", "data": {"findings": []}},
        ],
    )
    _save_ckpt(tmp_path, total=0)

    summary = summarize_last_run(str(tmp_path))
    assert summary is not None
    assert summary["severity"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}
    assert summary["preview"] == []
