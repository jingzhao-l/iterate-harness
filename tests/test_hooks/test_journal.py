"""Hook-execution journal tests (design §17.3 P15).

The engine records every hook it runs into ``.iterate/hook-journal.jsonl``;
these tests pin the journal contract the CLI and console share: append-only
records, defensive reads (a corrupt line never blanks the view), newest-first
paging with matching totals, event/type/success filters, lazy rotation under a
size cap, and the three render shapes (table / markdown / csv).
"""

from __future__ import annotations

import json
from pathlib import Path

from iterate_harness.hooks.journal import (
    distinct_events,
    distinct_hook_types,
    hook_execution_time_ms,
    hook_log_csv,
    iter_hook_executions,
    journal_path,
    journal_summary,
    list_hook_executions,
    record_hook_execution,
    render_hook_log_markdown,
    render_hook_log_table,
)


def test_record_and_read_roundtrip(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    record_hook_execution(
        project,
        event="session_start",
        hook_type="command",
        detail="printf 'booted'",
        success=True,
        duration_ms=12,
    )
    entries = list(iter_hook_executions(project))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "session_start"
    assert entry["hook_type"] == "command"
    assert entry["success"] is True
    assert entry["duration_ms"] == 12
    assert "timestamp" in entry


def test_record_is_best_effort_on_bad_path(tmp_path: Path) -> None:
    # A project root that cannot be created must not raise.
    record = record_hook_execution(
        tmp_path / "no" / "such" / "repo" / "with" / "a" / "file" / "inside",
        event="stop",
        hook_type="http",
        success=False,
        reason="boom",
    )
    assert record["event"] == "stop"


def test_corrupt_line_is_skipped(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    path = journal_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("{not json}\n")
        handle.write(json.dumps({"event": "stop", "hook_type": "command", "success": True}) + "\n")
        handle.write("\n")
    entries = list(iter_hook_executions(project))
    assert len(entries) == 1
    assert entries[0]["event"] == "stop"


def test_newest_first_paging_and_total(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    for index in range(5):
        record_hook_execution(
            project,
            event="pre_tool_use",
            hook_type="command",
            detail=f"hook-{index}",
            success=index % 2 == 0,
        )
    page, total = list_hook_executions(project, limit=2, offset=0)
    assert total == 5
    assert len(page) == 2
    # Newest first: the last recorded (hook-4) is first.
    assert page[0]["detail"] == "hook-4"
    assert page[1]["detail"] == "hook-3"
    page2, _ = list_hook_executions(project, limit=2, offset=2)
    assert [entry["detail"] for entry in page2] == ["hook-2", "hook-1"]


def test_filters_by_event_type_and_success(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    record_hook_execution(project, event="pre_tool_use", hook_type="command", success=True)
    record_hook_execution(project, event="post_tool_use", hook_type="http", success=False)
    record_hook_execution(project, event="pre_tool_use", hook_type="prompt", success=False)

    by_event, total = list_hook_executions(project, event="pre_tool_use")
    assert total == 2
    assert {entry["hook_type"] for entry in by_event} == {"command", "prompt"}

    by_type, total = list_hook_executions(project, hook_type="http")
    assert total == 1
    assert by_type[0]["event"] == "post_tool_use"

    ok, total = list_hook_executions(project, success=True)
    assert total == 1
    assert ok[0]["hook_type"] == "command"

    failed, total = list_hook_executions(project, success=False)
    assert total == 2

    assert distinct_events(project) == ["post_tool_use", "pre_tool_use"]
    assert distinct_hook_types(project) == ["command", "http", "prompt"]


def test_journal_summary(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    record_hook_execution(project, event="pre_tool_use", hook_type="command", success=True)
    record_hook_execution(project, event="pre_tool_use", hook_type="http", success=False, blocked=True)
    summary = journal_summary(project)
    assert summary["total"] == 2
    assert summary["successes"] == 1
    assert summary["failures"] == 1
    assert summary["blocked"] == 1
    assert summary["by_event"] == {"pre_tool_use": 2}


def test_rotation_trims_to_newest(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    # Force a tiny cap so the assertion exercises real rotation rather than
    # the default 5 MiB ceiling (200 × 1 KB records never approached it).
    monkeypatch.setattr("iterate_harness.hooks.journal.MAX_JOURNAL_BYTES", 4_000)
    for index in range(30):
        record_hook_execution(
            project,
            event="pre_tool_use",
            hook_type="command",
            detail="x" * 400 + str(index),
            success=True,
        )
    path = journal_path(project)
    assert path.stat().st_size <= 4_000
    remaining = [entry["detail"] for entry in iter_hook_executions(project)]
    # Rotation keeps the newest lines and never fabricates entries.
    assert remaining
    assert remaining[-1].endswith("29")
    assert all("x" * 400 in detail for detail in remaining)
    # The rewrite is atomic: no temp file survives a successful rotation.
    assert not list((project / ".iterate").glob("hook-journal.jsonl.tmp-*"))


def test_rotation_happens_after_the_first_append(tmp_path: Path, monkeypatch) -> None:
    """A pre-existing journal must still be trimmed (regression).

    The first implementation only rotated when the append *created* the file,
    so the cap was enforced once and never again: a long-lived project grew
    ``hook-journal.jsonl`` without bound.
    """
    project = tmp_path / "repo"
    project.mkdir()
    record_hook_execution(
        project,
        event="session_start",
        hook_type="command",
        detail="first",
        success=True,
    )
    path = journal_path(project)
    assert path.exists()
    monkeypatch.setattr("iterate_harness.hooks.journal.MAX_JOURNAL_BYTES", 600)
    for index in range(10):
        record_hook_execution(
            project,
            event="stop",
            hook_type="command",
            detail="y" * 300 + str(index),
            success=True,
        )
    assert path.stat().st_size <= 600
    drafts = [entry["detail"] for entry in iter_hook_executions(project)]
    assert drafts[-1].endswith("9")
    assert "first" not in drafts


def test_render_shapes(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    record_hook_execution(
        project,
        event="pre_tool_use",
        hook_type="command",
        detail="printf 'gated'",
        success=True,
        duration_ms=5,
    )
    record_hook_execution(
        project,
        event="stop",
        hook_type="http",
        detail="https://example.com/hook",
        success=False,
        blocked=True,
        reason="denied",
    )
    page, total = list_hook_executions(project)
    table = render_hook_log_table(page, total, limit=50, offset=0)
    assert "pre_tool_use" in table
    assert "blocked" in table
    assert "2" in table
    markdown = render_hook_log_markdown(page, total, limit=50, offset=0)
    assert "| event" in markdown or "| time" in markdown
    csv_text = hook_log_csv(page, total, limit=50, offset=0)
    assert "hook_type" in csv_text
    assert "command" in csv_text


def test_empty_journal_is_not_an_error(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    page, total = list_hook_executions(project)
    assert page == []
    assert total == 0
    assert journal_summary(project)["total"] == 0
    assert distinct_events(project) == []


def test_execution_time_ms_is_nonnegative():
    import time

    start = time.monotonic()
    assert hook_execution_time_ms(start) >= 0
