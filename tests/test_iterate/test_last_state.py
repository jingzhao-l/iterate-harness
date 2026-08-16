"""Tests for last_state resume summaries (finished + interrupted checkpoints)."""

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


def test_summarize_none_without_history(tmp_path: Path):
    assert summarize_last_run(str(tmp_path)) is None


def test_summarize_finished_run_prefers_report(tmp_path: Path):
    _write_entries(
        tmp_path,
        [
            {"timestamp": "t0", "round": 1, "type": "review_result", "data": {}},
            {
                "timestamp": "t1",
                "round": 2,
                "type": "report",
                "data": {
                    "mode": "normal",
                    "verdict": "converged",
                    "findings": [
                        {"severity": "high", "file": "a.py", "dimension": "code_review", "summary": "issue a"},
                        {"severity": "low", "file": "b.py", "dimension": "security", "summary": "issue b"},
                    ],
                },
            },
        ],
    )
    summary = summarize_last_run(str(tmp_path))
    assert summary is not None
    assert summary["verdict"] == "converged"
    assert summary["mode"] == "normal"
    assert summary["totalFindings"] == 2
    assert summary["severity"]["high"] == 1
    assert summary["rounds"] == 2
    assert "interrupted" not in summary


def test_summarize_interrupted_uses_checkpoint(tmp_path: Path):
    # A run that started but never emitted a report entry — only checkpoints.
    _write_entries(
        tmp_path,
        [
            {"timestamp": "t0", "round": 1, "type": "review_result", "data": {"findings": []}},
        ],
    )
    save_checkpoint(
        tmp_path,
        round=3,
        new_findings=1,
        total_findings=5,
        per_dimension={"code_review": 3, "security": 2},
        converged=False,
        input_tokens=9000,
        output_tokens=21000,
        cost_usd=0.82,
        mode="dry-run",
    )

    summary = summarize_last_run(str(tmp_path))
    assert summary is not None
    assert summary["verdict"] == "interrupted"
    assert summary["interrupted"] is True
    assert summary["rounds"] == 3
    assert summary["totalFindings"] == 5
    assert summary["perDimension"] == {"code_review": 3, "security": 2}
    assert summary["mode"] == "dry-run"


def test_summarize_interrupted_previews_review_result_findings(tmp_path: Path):
    _write_entries(
        tmp_path,
        [
            {
                "timestamp": "t0",
                "round": 2,
                "type": "review_result",
                "data": {
                    "findings": [
                        {"severity": "critical", "file": "c.py", "dimension": "security", "summary": "bug"},
                    ]
                },
            },
        ],
    )
    save_checkpoint(
        tmp_path,
        round=2,
        new_findings=0,
        total_findings=1,
        per_dimension={"security": 1},
        converged=False,
        input_tokens=1000,
        output_tokens=2000,
        cost_usd=0.1,
        mode="dry-run",
    )

    summary = summarize_last_run(str(tmp_path))
    assert summary is not None
    assert summary["preview"][0]["file"] == "c.py"
    assert summary["severity"]["critical"] == 1


def test_summarize_interrupted_without_review_result(tmp_path: Path):
    save_checkpoint(
        tmp_path,
        round=1,
        new_findings=2,
        total_findings=2,
        per_dimension={"security": 2},
        converged=False,
        input_tokens=100,
        output_tokens=200,
        cost_usd=0.01,
        mode="dry-run",
    )
    summary = summarize_last_run(str(tmp_path))
    assert summary is not None
    assert summary["interrupted"] is True
    assert summary["preview"] == []
