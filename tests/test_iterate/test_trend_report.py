"""Tests for the cross-run trend report (``ih iterate trend`` / WebUI trend page).

The report is derived from decision-log ``report`` entries (new/fixed/regressed
per run) and joined with the trend library (open/stubborn). These tests pin the
lifecycle semantics — including the case the library alone cannot answer: a
finding that disappears and later comes back is a *regression*, not "new".
"""

from __future__ import annotations

import json
from pathlib import Path

from iterate_harness.iterate import trend_report, trend_store
from iterate_harness.iterate.decision_log import (
    DecisionLogEntry,
    append_entry,
)

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


def add_report(
    root: Path,
    *,
    index: int,
    findings: list[dict[str, object]],
    round_number: int = 1,
    verdict: str = "approved",
    mode: str = "review",
    data: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "findings": findings,
        "totalFindings": len(findings),
        "verdict": verdict,
        "mode": mode,
    }
    if data:
        payload.update(data)
    append_entry(
        root,
        DecisionLogEntry(
            timestamp=TIMESTAMPS[index % len(TIMESTAMPS)],
            round=round_number,
            type="report",
            data=payload,
        ),
    )


def test_new_fixed_regressed_lifecycle(tmp_path: Path) -> None:
    a = finding(file="a.py", line=1)
    b = finding(file="b.py", line=2)
    c = finding(file="c.py", line=3)

    add_report(tmp_path, index=0, findings=[a, b])
    add_report(tmp_path, index=1, findings=[a, c])
    add_report(tmp_path, index=2, findings=[a, b])

    report = trend_report.build_trend_report(tmp_path)
    assert report.total_runs == 3
    assert [run.index for run in report.runs] == [1, 2, 3]

    first, second, third = report.runs
    # First run: everything is new because there is no baseline.
    assert (first.new, first.fixed, first.regressed, first.unchanged) == (2, 0, 0, 0)
    # b fixed, c new, a unchanged.
    assert (second.new, second.fixed, second.regressed, second.unchanged) == (1, 1, 0, 1)
    assert [f["file"] for f in second.fixed_findings] == ["b.py"]
    # b came back after being fixed: regression, not a new finding.
    assert (third.new, third.fixed, third.regressed, third.unchanged) == (0, 1, 1, 1)
    assert [f["file"] for f in third.regressed_findings] == ["b.py"]
    assert third.regressed_findings[0]["line"] == 2
    assert third.regressed_findings[0]["severity"] == "high"
    assert report.latest is third


def test_history_limit_keeps_global_run_numbers(tmp_path: Path) -> None:
    for index in range(5):
        add_report(tmp_path, index=index, findings=[finding(file=f"{index}.py")])

    report = trend_report.build_trend_report(tmp_path, history=2)
    assert report.total_runs == 5
    assert [run.index for run in report.runs] == [4, 5]
    assert report.latest is not None and report.latest.index == 5


def test_malformed_entries_and_findings_are_skipped(tmp_path: Path) -> None:
    valid = finding()
    # Duplicate fingerprint: counted once (matches the library's semantics).
    duplicate = finding(summary="same identity, different prose")
    # Not dicts / untrackable (no file+dimension) are dropped.
    junk = [None, "nope", {"summary": "no file or dimension"}, 42]
    add_report(tmp_path, index=0, findings=[valid, duplicate, *junk])
    add_report(tmp_path, index=1, findings=[], data={"findings": junk})
    append_entry(
        tmp_path,
        DecisionLogEntry(
            timestamp=TIMESTAMPS[2],
            round=2,
            type="report",
            data={"findings": "not-a-list", "verdict": None},
        ),
    )

    report = trend_report.build_trend_report(tmp_path)
    first, second, third = report.runs
    assert first.total == 1
    assert first.reported_total == 6  # the log's raw list; the view reports both
    assert (second.total, second.fixed) == (0, 1)
    assert third.total == 0
    assert third.verdict == "unknown"


def test_summary_joins_library_and_reports_stubborn(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    record = finding(file="stubborn.py", line=7)
    for index in range(3):
        trend_store.record_run(tmp_path, [record], run_timestamp=TIMESTAMPS[index])
        add_report(tmp_path, index=index, findings=[record])

    report = trend_report.build_trend_report(tmp_path)
    assert report.tracked == 1
    assert report.open == 1
    assert report.stubborn == 1
    assert report.open_by_dimension == {"security": 1}
    assert report.stubborn_findings[0]["file"] == "stubborn.py"
    assert report.stubborn_findings[0]["runs"] == 3


def test_gate_helpers_only_look_at_the_latest_run(tmp_path: Path) -> None:
    low = finding(file="low.py", severity="low")
    high = finding(file="high.py", severity="high")
    add_report(tmp_path, index=0, findings=[high])
    report = trend_report.build_trend_report(tmp_path)
    assert trend_report.regression_count(report) == 0
    assert trend_report.new_findings_at_or_above(report, "low") == 1
    assert trend_report.new_findings_at_or_above(report, "high") == 1
    assert trend_report.new_findings_at_or_above(report, "critical") == 0
    assert trend_report.new_findings_at_or_above(report, "bogus") == 0

    add_report(tmp_path, index=1, findings=[low])
    report = trend_report.build_trend_report(tmp_path)
    # The high finding was fixed; the low one is new. The gate sees only the
    # latest run, so a previously fixed critical cannot fail a later clean run.
    assert trend_report.new_findings_at_or_above(report, "high") == 0
    assert trend_report.new_findings_at_or_above(report, "low") == 1


def test_render_empty_state_names_the_next_action(tmp_path: Path) -> None:
    rendered = trend_report.render_trend_report(trend_report.build_trend_report(tmp_path))
    assert "No trend data yet" in rendered
    assert "ih iterate trend" in rendered


def test_render_and_json_payload(tmp_path: Path) -> None:
    add_report(tmp_path, index=0, findings=[finding(), finding(file="b.py", severity="medium")])
    add_report(tmp_path, index=1, findings=[finding()])
    report = trend_report.build_trend_report(tmp_path)

    rendered = trend_report.render_trend_report(report)
    assert "iterate trend — 2 finished run(s)" in rendered
    assert "regressed" in rendered
    assert "fixed findings" in rendered

    payload = report.to_dict()
    # Must survive JSON serialisation for --json and the HTTP route.
    parsed = json.loads(json.dumps(payload))
    assert parsed["has_data"] is True
    assert parsed["total_runs"] == 2
    assert parsed["latest"]["index"] == 2
    assert parsed["latest"]["fixed"] == 1
    assert parsed["runs"][0]["regressed"] == 0


def test_finding_lists_are_bounded_but_counts_are_exact(tmp_path: Path) -> None:
    many = [finding(file=f"f{i}.py", line=i) for i in range(trend_report.MAX_FINDINGS_PER_RUN + 10)]
    add_report(tmp_path, index=0, findings=many)

    report = trend_report.build_trend_report(tmp_path)
    assert report.latest is not None
    assert report.latest.new == len(many)
    payload = report.to_dict()
    assert len(payload["latest"]["new_findings"]) == trend_report.MAX_FINDINGS_PER_RUN


def test_top_bounds_open_findings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    findings = [finding(file=f"x{i}.py", severity="critical") for i in range(6)]
    trend_store.record_run(tmp_path, findings, run_timestamp=TIMESTAMPS[0])

    report = trend_report.build_trend_report(tmp_path, top=2)
    assert report.open == 6
    assert len(report.open_findings) == 2


def test_non_integer_total_findings_falls_back_to_the_recovered_count(
    tmp_path: Path,
) -> None:
    """A malformed ``totalFindings`` must never render as a word in a table.

    ``True`` satisfies ``isinstance(x, int)`` in Python, so an unguarded check
    would have put the word "True" in the ``reported`` column of a run row.
    """
    add_report(
        tmp_path,
        index=0,
        findings=[finding(file="a.py"), finding(file="b.py")],
        data={"totalFindings": True},
    )
    add_report(
        tmp_path,
        index=1,
        findings=[finding(file="a.py")],
        data={"totalFindings": "two"},
    )

    report = trend_report.build_trend_report(tmp_path)
    assert [run.reported_total for run in report.runs] == [2, 1]
    rendered = trend_report.render_trend_report(report)
    assert "True" not in rendered and "two" not in rendered


def test_boolean_line_is_not_a_line_number(tmp_path: Path) -> None:
    """``line: true`` is a caller bug, not line 1 — keep the two views in step.

    The trend library fingerprints such a finding as file-level
    (``finding_fingerprint`` refuses the bool), so the report's row must drop
    the line too; otherwise the same finding reads as ``file`` in the library
    and ``file:1`` in the trend report.
    """
    weird = finding(file="weird.py")
    weird["line"] = True
    add_report(tmp_path, index=0, findings=[weird])

    report = trend_report.build_trend_report(tmp_path)
    assert report.latest is not None
    assert report.latest.new_findings[0]["line"] is None
    assert (
        trend_store.finding_fingerprint(weird)
        == report.latest.new_findings[0]["fingerprint"]
    )
    assert "weird.py:True" not in trend_report.render_trend_report(report)
