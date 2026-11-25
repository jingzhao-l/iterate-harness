"""Defensive-kernel journal tests (design §17.3 P17).

The kernel and ``ih defense check`` both write here and the console reads it;
these tests pin the contract that makes that shared: a missing journal is an
empty page (never an error), a corrupt line is skipped instead of blanking the
trail, filters/pagination are exact, and the summary the page header reads
matches what was written.
"""

from __future__ import annotations

import json
from pathlib import Path

from iterate_harness.defensive import journal
from iterate_harness.defensive.journal import (
    KIND_INVARIANT,
    KIND_MANUAL_CHECK,
    KIND_ROLLBACK,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_UNMEASURED,
    defense_journal_summary,
    defense_log_csv,
    event_age_seconds,
    iter_defense_events,
    journal_export_filename,
    journal_path,
    list_defense_events,
    record_defense_event,
    render_defense_log_markdown,
    render_defense_log_table,
)


def test_missing_journal_is_an_empty_page(tmp_path: Path) -> None:
    assert list(iter_defense_events(tmp_path)) == []
    page, total = list_defense_events(tmp_path)
    assert page == []
    assert total == 0
    assert not journal_path(tmp_path).exists()
    summary = defense_journal_summary(tmp_path)
    assert summary["total"] == 0
    assert summary["last_verdict"] == ""


def test_record_then_list_newest_first(tmp_path: Path) -> None:
    first = record_defense_event(
        tmp_path,
        kind=KIND_INVARIANT,
        outcome=OUTCOME_PASS,
        tool="write_file",
        path="src/a.py",
        checks_run=2,
        duration_ms=12,
    )
    second = record_defense_event(
        tmp_path,
        kind=KIND_ROLLBACK,
        outcome=OUTCOME_FAIL,
        tool="edit_file",
        path="src/b.py",
        checks_run=1,
        rolled_back=True,
        violations=[
            {"kind": "command", "label": "core:pytest", "detail": "exit 1"}
        ],
    )
    page, total = list_defense_events(tmp_path)
    assert total == 2
    assert [entry["id"] for entry in page] == [second["id"], first["id"]]
    assert page[0]["rolled_back"] is True
    assert page[0]["violations"][0]["label"] == "core:pytest"
    assert first["timestamp"] <= second["timestamp"]


def test_filters_and_pagination(tmp_path: Path) -> None:
    for index in range(4):
        record_defense_event(
            tmp_path,
            kind=KIND_ROLLBACK if index % 2 == 0 else KIND_INVARIANT,
            outcome=OUTCOME_FAIL if index % 2 == 0 else OUTCOME_PASS,
            tool=f"tool-{index}",
        )
    page, total = list_defense_events(tmp_path, kind=KIND_ROLLBACK)
    assert total == 2
    assert [entry["tool"] for entry in page] == ["tool-2", "tool-0"]

    page, total = list_defense_events(tmp_path, outcome=OUTCOME_PASS, limit=1, offset=1)
    assert total == 2
    assert page[0]["tool"] == "tool-1"

    page, total = list_defense_events(tmp_path, tool="tool-3")
    assert total == 1
    assert page[0]["kind"] == KIND_INVARIANT


def test_corrupt_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    record_defense_event(tmp_path, kind=KIND_INVARIANT, outcome=OUTCOME_PASS)
    path = journal_path(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write("[1, 2, 3]\n")
        handle.write("\n")
    record_defense_event(tmp_path, kind=KIND_ROLLBACK, outcome=OUTCOME_FAIL)

    events = list(iter_defense_events(tmp_path))
    assert [event["kind"] for event in events] == [KIND_INVARIANT, KIND_ROLLBACK]
    assert defense_journal_summary(tmp_path)["total"] == 2


def test_summary_counts_kinds_and_last_verdict(tmp_path: Path) -> None:
    record_defense_event(tmp_path, kind=KIND_INVARIANT, outcome=OUTCOME_PASS)
    record_defense_event(tmp_path, kind=KIND_ROLLBACK, outcome=OUTCOME_FAIL)
    record_defense_event(tmp_path, kind=KIND_ROLLBACK, outcome=OUTCOME_UNMEASURED)
    record_defense_event(
        tmp_path, kind=KIND_MANUAL_CHECK, outcome=OUTCOME_PASS, tool="ih defense check"
    )

    summary = defense_journal_summary(tmp_path)
    assert summary["total"] == 4
    assert summary["rollbacks"] == 2
    assert summary["manual_checks"] == 1
    assert summary["invariant_runs"] == 4
    assert summary["last_verdict"] == OUTCOME_PASS
    assert summary["by_kind"] == {
        KIND_INVARIANT: 1,
        KIND_MANUAL_CHECK: 1,
        KIND_ROLLBACK: 2,
    }


def test_unmeasured_is_not_a_pass(tmp_path: Path) -> None:
    record_defense_event(tmp_path, kind=KIND_ROLLBACK, outcome=OUTCOME_UNMEASURED)
    page, _ = list_defense_events(tmp_path)
    assert "unmeasured" in render_defense_log_table(page, 1, 50, 0)
    assert "unmeasured" in render_defense_log_markdown(page, 1, 50, 0)


def test_record_bounds_huge_payloads(tmp_path: Path) -> None:
    record = record_defense_event(
        tmp_path,
        kind=KIND_ROLLBACK,
        outcome=OUTCOME_FAIL,
        reason="x" * (journal._MAX_FIELD_CHARS + 50),
        violations=[{"kind": "command", "label": "l", "detail": "d"}] * 50,
    )
    assert "truncated" in record["reason"]
    assert len(record["violations"]) == journal.MAX_VIOLATIONS


def test_journal_rotates_keeping_newest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(journal, "MAX_JOURNAL_BYTES", 400)
    ids: list[str] = []
    for index in range(12):
        record = record_defense_event(
            tmp_path,
            kind=KIND_INVARIANT,
            outcome=OUTCOME_PASS,
            tool=f"tool-{index}",
            reason="line " + "x" * 40,
        )
        ids.append(record["id"])
    path = journal_path(tmp_path)
    assert path.stat().st_size <= journal.MAX_JOURNAL_BYTES
    remaining = [entry["id"] for entry in iter_defense_events(tmp_path)]
    assert remaining == ids[-len(remaining) :]
    assert ids[-1] in remaining
    assert ids[0] not in remaining


def test_renderers_escape_markdown_and_csv(tmp_path: Path) -> None:
    record_defense_event(
        tmp_path,
        kind=KIND_ROLLBACK,
        outcome=OUTCOME_FAIL,
        tool="write_file",
        path="a|b.py",
        reason="line one\nline two",
        rolled_back=True,
        violations=[{"kind": "ensure", "label": "a", "detail": "b"}],
    )
    page, total = list_defense_events(tmp_path)

    markdown = render_defense_log_markdown(page, total, 50, 0)
    assert "a\\|b.py" in markdown
    assert "line one line two" in markdown

    csv_text = defense_log_csv(page, total, 50, 0)
    assert "timestamp,id,kind,outcome" in csv_text
    assert "write_file" in csv_text
    assert "ensure:a (b)" in csv_text

    table = render_defense_log_table(page, total, 50, 0)
    assert "Showing 1 of 1 matching events" in table
    assert "rolled back (fail)" in table


def test_export_filename_and_event_age() -> None:
    assert journal_export_filename("md").startswith("iterate-defense-")
    assert journal_export_filename("csv").endswith(".csv")
    age = event_age_seconds({"timestamp": "2000-01-01T00:00:00+00:00"})
    assert age is not None and age > 0
    assert event_age_seconds({"timestamp": "not a timestamp"}) is None


def test_journal_line_shape_is_stable(tmp_path: Path) -> None:
    record_defense_event(tmp_path, kind=KIND_INVARIANT, outcome=OUTCOME_PASS)
    line = journal_path(tmp_path).read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert set(parsed) == {
        "id",
        "timestamp",
        "kind",
        "outcome",
        "tool",
        "path",
        "trigger",
        "reason",
        "checks_run",
        "duration_ms",
        "rolled_back",
        "violations",
    }
