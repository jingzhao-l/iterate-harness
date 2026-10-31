"""Two-run comparison (``ih iterate diff`` / ``GET /api/v1/runs/diff``).

``trend_store.diff_runs`` existed in the engine but no CLI command or console
surface ever called it. These tests pin the join back to the decision log:
which pair is picked by default, how findings are classified, and the
regression rule (a finding fixed earlier and present again is not "new").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.iterate import trend_report, trend_store
from iterate_harness.iterate.decision_log import DecisionLogEntry, append_entry

TIMESTAMPS = [
    "2026-01-01T00:00:00+00:00",
    "2026-01-02T00:00:00+00:00",
    "2026-01-03T00:00:00+00:00",
]


def finding(file: str, severity: str = "high", line: int = 1) -> dict[str, object]:
    return {
        "file": file,
        "line": line,
        "dimension": "security",
        "severity": severity,
        "summary": f"problem in {file}",
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


def test_default_pair_is_the_last_two_runs(tmp_path: Path) -> None:
    add_report(tmp_path, 0, [finding("a.py"), finding("b.py")])
    add_report(tmp_path, 1, [finding("a.py"), finding("b.py")])
    add_report(tmp_path, 2, [finding("a.py"), finding("c.py")])

    report = trend_report.build_run_diff(tmp_path)

    assert report.from_run is not None and report.to_run is not None
    assert (report.from_run.index, report.to_run.index) == (2, 3)
    assert report.counts == {"new": 1, "fixed": 1, "regressed": 0, "unchanged": 1}
    assert [item["file"] for item in report.diff.new] == ["c.py"]
    assert [item["file"] for item in report.diff.fixed] == ["b.py"]
    assert [item["file"] for item in report.diff.unchanged] == ["a.py"]


def test_explicit_indices_compare_any_pair(tmp_path: Path) -> None:
    add_report(tmp_path, 0, [finding("a.py")])
    add_report(tmp_path, 1, [finding("a.py"), finding("b.py")])
    add_report(tmp_path, 2, [finding("b.py")])

    report = trend_report.build_run_diff(tmp_path, from_index=1, to_index=3)

    assert (report.from_run.index, report.to_run.index) == (1, 3)
    assert report.counts == {"new": 1, "fixed": 1, "regressed": 0, "unchanged": 0}
    assert [item["file"] for item in report.diff.fixed] == ["a.py"]
    assert [item["file"] for item in report.diff.new] == ["b.py"]


def test_a_fixed_finding_that_returns_is_a_regression(tmp_path: Path) -> None:
    a = finding("a.py")
    trend_store.record_run(tmp_path, [a], run_timestamp=TIMESTAMPS[0])
    trend_store.record_run(tmp_path, [], run_timestamp=TIMESTAMPS[1])  # a is fixed here
    add_report(tmp_path, 0, [a])
    add_report(tmp_path, 1, [])
    add_report(tmp_path, 2, [a])

    report = trend_report.build_run_diff(tmp_path, from_index=2, to_index=3)

    assert report.counts == {"new": 0, "fixed": 0, "regressed": 1, "unchanged": 0}
    assert [item["file"] for item in report.diff.regressed] == ["a.py"]


def test_fewer_than_two_runs_is_explained_not_guessed(tmp_path: Path) -> None:
    add_report(tmp_path, 0, [finding("a.py")])

    report = trend_report.build_run_diff(tmp_path)

    assert report.from_run is None and report.to_run is None
    assert report.counts == {"new": 0, "fixed": 0, "regressed": 0, "unchanged": 0}
    assert len(report.runs) == 1
    text = trend_report.render_run_diff(report)
    assert "need at least two finished runs" in text
    assert "ih iterate diff" in text


def test_an_out_of_range_index_names_the_valid_range(tmp_path: Path) -> None:
    add_report(tmp_path, 0, [finding("a.py")])
    add_report(tmp_path, 1, [finding("b.py")])

    with pytest.raises(ValueError) as caught:
        trend_report.build_run_diff(tmp_path, from_index=3)

    assert "between 1 and 2" in str(caught.value)
    assert "ih iterate trend" in str(caught.value)


def test_render_puts_run_metadata_before_the_diff(tmp_path: Path) -> None:
    add_report(tmp_path, 0, [finding("a.py")])
    add_report(tmp_path, 1, [finding("a.py"), finding("b.py")])

    text = trend_report.render_run_diff(trend_report.build_run_diff(tmp_path))

    assert text.startswith("iterate diff — run 1 → 2")
    assert "from: 2026-01-01T00:00:00.000Z · R1 · approved · 1 finding(s)" in text
    assert "Run diff: 1 new, 0 fixed, 0 regressed, 1 unchanged" in text


def test_cli_prints_the_comparison_and_supports_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_report(tmp_path, 0, [finding("a.py")])
    add_report(tmp_path, 1, [finding("a.py"), finding("b.py")])
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    plain = runner.invoke(cli.app, ["iterate", "diff"])
    assert plain.exit_code == 0, plain.output
    assert "iterate diff — run 1 → 2" in plain.output
    assert "1 new, 0 fixed" in plain.output

    structured = runner.invoke(cli.app, ["iterate", "diff", "--json"])
    assert structured.exit_code == 0, structured.output
    payload = json.loads(structured.output)
    assert payload["counts"] == {"new": 1, "fixed": 0, "regressed": 0, "unchanged": 1}
    assert payload["to_run"]["index"] == 2


def test_cli_rejects_an_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_report(tmp_path, 0, [finding("a.py")])
    add_report(tmp_path, 1, [finding("b.py")])
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["iterate", "diff", "--from", "9"])

    assert result.exit_code != 0
    assert "between 1 and 2" in result.output


async def test_slash_diff_reaches_the_same_comparison(tmp_path: Path) -> None:
    """`/iterate diff` is the in-TUI door to the same engine join as the CLI."""
    from iterate_harness.commands.iterate import iterate_command_handler
    from iterate_harness.commands.registry import CommandContext

    add_report(tmp_path, 0, [finding("a.py")])
    add_report(tmp_path, 1, [finding("a.py"), finding("b.py")])
    context = CommandContext(engine=None, cwd=str(tmp_path))  # type: ignore[arg-type]

    result = await iterate_command_handler("diff", context)
    assert "iterate diff — run 1 → 2" in (result.message or "")
    assert "1 new, 0 fixed" in (result.message or "")

    rejected = await iterate_command_handler("diff 9", context)
    assert "between 1 and 2" in (rejected.message or "")


def test_a_reversed_pair_is_rejected_instead_of_inverting_the_labels(tmp_path: Path) -> None:
    add_report(tmp_path, 0, [finding("a.py")])
    add_report(tmp_path, 1, [finding("b.py")])

    with pytest.raises(ValueError) as caught:
        trend_report.build_run_diff(tmp_path, from_index=2, to_index=1)

    assert "from must be earlier than to (got 2 → 1)" in str(caught.value)
