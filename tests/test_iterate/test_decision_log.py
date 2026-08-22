"""Tests for iterate_harness.iterate.decision_log (port of the decision-log tool core)."""

from __future__ import annotations

import json

from iterate_harness.iterate.decision_log import (
    LOG_DIR,
    LOG_FILE,
    append_entry,
    log_path,
    make_entry,
    read_entries,
)
from iterate_harness.iterate.types import DecisionLogEntry


def entry(**kwargs: object) -> DecisionLogEntry:
    base: dict[str, object] = {
        "timestamp": "2026-08-14T00:00:00+00:00",
        "round": 1,
        "type": "round_start",
        "data": {},
    }
    base.update(kwargs)
    return DecisionLogEntry(**base)  # type: ignore[arg-type]


class TestAppendAndRead:
    def test_creates_log_under_iterate_dir_and_appends(self, tmp_path):
        count1, path = append_entry(tmp_path, entry())
        assert path == tmp_path / LOG_DIR / LOG_FILE
        assert count1 == 1
        count2, _ = append_entry(tmp_path, entry(round=2, type="validation"))
        assert count2 == 2

    def test_log_path_creates_directory(self, tmp_path):
        target = tmp_path / LOG_DIR
        assert not target.exists()
        path = log_path(tmp_path)
        assert path.parent is target or path.parent == target
        assert target.exists()

    def test_read_returns_entries_in_order_with_fields(self, tmp_path):
        append_entry(tmp_path, entry(data={"phase": "start"}))
        append_entry(tmp_path, entry(round=2, type="atomic_fix", data={"file": "a.py"}))
        entries = read_entries(tmp_path)
        assert len(entries) == 2
        assert entries[0].type == "round_start"
        assert entries[0].data == {"phase": "start"}
        assert entries[1].round == 2
        assert entries[1].data == {"file": "a.py"}

    def test_read_returns_empty_when_log_missing(self, tmp_path):
        assert read_entries(tmp_path) == []

    def test_each_line_is_valid_jsonl(self, tmp_path):
        append_entry(tmp_path, entry(data={"k": "v"}))
        raw = (tmp_path / LOG_DIR / LOG_FILE).read_text(encoding="utf-8")
        lines = [line for line in raw.split("\n") if line.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["data"] == {"k": "v"}
        assert parsed["type"] == "round_start"


class TestCorruptionHandling:
    def test_malformed_lines_are_skipped_not_fatal(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text(
            "not-json\n"
            + json.dumps({"timestamp": "t", "round": 1, "type": "decision", "data": {}})
            + "\n\n",
            encoding="utf-8",
        )
        entries = read_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0].type == "decision"

    def test_non_dict_lines_are_skipped(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text('["a","list"]\n', encoding="utf-8")
        assert read_entries(tmp_path) == []

    def test_non_numeric_round_is_skipped_not_fatal(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text(
            json.dumps({"timestamp": "t", "round": "abc", "type": "decision", "data": {}})
            + "\n"
            + json.dumps({"timestamp": "t", "round": 2, "type": "validation", "data": {}})
            + "\n",
            encoding="utf-8",
        )
        entries = read_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0].round == 2

    def test_non_mapping_data_is_skipped_not_fatal(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text(
            json.dumps({"timestamp": "t", "round": 1, "type": "decision", "data": ["a", "b"]})
            + "\n"
            + json.dumps({"timestamp": "t", "round": 1, "type": "report", "data": {}})
            + "\n",
            encoding="utf-8",
        )
        entries = read_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0].type == "report"

    def test_corrupt_entry_does_not_break_later_appends(self, tmp_path):
        log_dir = tmp_path / LOG_DIR
        log_dir.mkdir()
        (log_dir / LOG_FILE).write_text(
            json.dumps({"timestamp": "t", "round": "bad", "type": "decision", "data": {}}) + "\n",
            encoding="utf-8",
        )
        count, _ = append_entry(tmp_path, entry(round=2, type="validation"))
        assert count == 1  # only the valid entry survives the corrupt one
        assert read_entries(tmp_path)[0].round == 2


class TestMakeEntry:
    def test_builds_timestamped_utc_entry(self):
        made = make_entry(entry_type="revert", round_number=3, data={"reason": "test fail"})
        assert made.type == "revert"
        assert made.round == 3
        assert made.data == {"reason": "test fail"}
        assert "T" in made.timestamp  # ISO-8601
        assert made.timestamp.endswith("+00:00")  # UTC

    def test_entry_types_cover_skill_vocabulary(self):
        from iterate_harness.iterate.decision_log import VALID_ENTRY_TYPES

        assert VALID_ENTRY_TYPES == {
            "round_start", "review_result", "atomic_fix", "architectural_fix",
            "revert", "validation", "decision", "report",
        }

    def test_findings_from_report_handles_all_legacy_shapes(self):
        from iterate_harness.iterate.decision_log import findings_from_report

        canonical = {"findings": [{"dimension": "security", "file": "a.py", "summary": "x"}]}
        assert len(findings_from_report(canonical)) == 1

        trimmed = {"topFindings": [{"dimension": "correctness", "file": "b.py", "summary": "y"}]}
        assert len(findings_from_report(trimmed)) == 1

        notable = {"notableFindings": [{"dimension": "architecture", "file": "c.py", "summary": "z"}]}
        assert len(findings_from_report(notable)) == 1

        nested = {"summary": {"findings": [{"dimension": "performance", "file": "d.py", "summary": "w"}]}}
        assert len(findings_from_report(nested)) == 1

        # Precedence: the fully-qualified ``findings`` wins over a trimmer slice.
        mixed = {"findings": [{"dimension": "a", "file": "n", "summary": "1"}], "topFindings": [{"dimension": "b", "file": "m", "summary": "2"}]}
        assert findings_from_report(mixed)[0]["dimension"] == "a"

        # Non-dict payloads and shapes without any carrier yield [].
        assert findings_from_report(None) == []
        assert findings_from_report({"verdict": "converged"}) == []
        assert findings_from_report({"topFindings": ["not-a-dict"]}) == []
