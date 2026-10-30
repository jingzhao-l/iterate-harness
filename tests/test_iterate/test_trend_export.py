"""Markdown / CSV trend exports (the console's "hand this to someone" form).

The page could already *show* the trend; the export is the new capability:
a paste-ready Markdown digest for a PR or weekly report, and a one-row-per-run
CSV for a spreadsheet. These tests pin the shapes so a column rename or a
missing empty-state sentence fails here rather than in someone's report.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from iterate_harness.iterate import trend_report, trend_store
from iterate_harness.iterate.decision_log import DecisionLogEntry, append_entry
from iterate_harness.iterate.trend_report import TrendReport, TrendRun

TIMESTAMPS = [
    "2026-01-01T00:00:00+00:00",
    "2026-01-02T00:00:00+00:00",
    "2026-01-03T00:00:00+00:00",
]


def finding(
    file: str = "src/a.py",
    line: int = 10,
    dimension: str = "security",
    severity: str = "high",
    summary: str = "sql injection risk",
) -> dict[str, object]:
    return {
        "file": file,
        "line": line,
        "dimension": dimension,
        "severity": severity,
        "summary": summary,
    }


def add_report(root: Path, index: int, findings: list[dict[str, object]]) -> None:
    append_entry(
        root,
        DecisionLogEntry(
            timestamp=TIMESTAMPS[index],
            round=1,
            type="report",
            data={
                "findings": findings,
                "totalFindings": len(findings),
                "verdict": "approved",
                "mode": "review",
            },
        ),
    )


def test_markdown_digest_has_a_run_table_and_the_summary_line(tmp_path: Path) -> None:
    add_report(tmp_path, 0, [finding("a.py"), finding("b.py")])
    add_report(tmp_path, 1, [finding("a.py")])
    report = trend_report.build_trend_report(tmp_path)

    text = trend_report.render_trend_markdown(report)

    assert text.startswith(f"# 迭代趋势 · {tmp_path}")
    assert "| # | 时间 | 轮次 | 结论 | 总数 | 新增 | 修复 | 回退 |" in text
    assert "| 1 | 2026-01-01T00:00:00.000Z | R1 | approved | 2 | 2 | 0 | 0 |" in text
    assert "| 2 | 2026-01-02T00:00:00.000Z | R1 | approved | 1 | 0 | 1 | 0 |" in text
    assert "- 运行：共 2 次，窗口内 2 次" in text
    assert text.endswith("\n")


def test_markdown_lists_stubborn_findings_with_location_and_age(tmp_path: Path) -> None:
    record = finding(file="src/stubborn.py", line=7, dimension="correctness")
    for index in range(3):
        trend_store.record_run(tmp_path, [record], run_timestamp=TIMESTAMPS[index])
        add_report(tmp_path, index, [record])
    report = trend_report.build_trend_report(tmp_path)

    text = trend_report.render_trend_markdown(report)

    assert "## 顽固 finding（连续 3+ 次未修复）" in text
    assert "`src/stubborn.py:7` · correctness · sql injection risk（已 3 次" in text
    assert "## 未解决 finding（最严重的在前）" in text


def test_markdown_says_how_to_produce_data_when_there_is_none(tmp_path: Path) -> None:
    text = trend_report.render_trend_markdown(trend_report.build_trend_report(tmp_path))

    assert "还没有已完成的运行" in text
    assert "ih iterate run" in text
    assert "## 逐次运行" not in text


def test_csv_has_a_stable_header_and_one_row_per_run(tmp_path: Path) -> None:
    add_report(tmp_path, 0, [finding("a.py")])
    add_report(tmp_path, 1, [finding("a.py"), finding("b.py")])
    report = trend_report.build_trend_report(tmp_path)

    rows = list(csv.reader(io.StringIO(trend_report.trend_csv(report))))

    assert tuple(rows[0]) == trend_report.TREND_CSV_COLUMNS
    assert len(rows) == 3
    assert rows[1][0] == "1" and rows[1][1] == "2026-01-01T00:00:00.000Z"
    assert rows[2][0] == "2" and rows[2][7] == "1"  # one new finding in run 2
    assert all(len(row) == len(rows[0]) for row in rows)


def test_csv_quotes_free_text_fields() -> None:
    report = TrendReport(
        project_root="/tmp/demo",
        generated_at="2026-01-01T00:00:00+00:00",
        total_runs=1,
        tracked=1,
        open=1,
        fixed=0,
        stubborn=0,
        runs=[
            TrendRun(
                index=1,
                timestamp="2026-01-01T00:00:00+00:00",
                round=2,
                verdict="approved, with notes",
                mode="review",
                total=1,
                reported_total=1,
                new=1,
                fixed=0,
                regressed=0,
                unchanged=0,
            )
        ],
    )

    rows = list(csv.reader(io.StringIO(trend_report.trend_csv(report))))

    assert rows[1][3] == "approved, with notes"
    assert rows[1][2] == "2"
