"""Unit tests for the SSE event generator helpers (design §17.4).

Focuses on the decision-log tail cursor logic in
:func:`iterate_harness.web.events._decision_log_tail`, including the
truncation/rotation case that used to leave the cursor stuck past the new
EOF so post-rotation lines were never streamed.
"""

from __future__ import annotations

import json

from iterate_harness.web.events import _decision_log_tail


def _write_lines(path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


class TestDecisionLogTail:
    def test_missing_file_returns_empty(self, tmp_path):
        entries, cursor = _decision_log_tail(tmp_path / "nope.jsonl", 0)
        assert entries == []
        assert cursor == 0

    def test_initial_tail_reads_all_lines(self, tmp_path):
        path = tmp_path / "decision-log.jsonl"
        _write_lines(path, [json.dumps({"i": 1}), json.dumps({"i": 2})])
        entries, cursor = _decision_log_tail(path, 0)
        assert [e["i"] for e in entries] == [1, 2]
        assert cursor == path.stat().st_size

    def test_incremental_tail_reads_only_new_lines(self, tmp_path):
        path = tmp_path / "decision-log.jsonl"
        _write_lines(path, [json.dumps({"i": 1})])
        _, cursor = _decision_log_tail(path, 0)

        _write_lines(path, [json.dumps({"i": 1}), json.dumps({"i": 2})])
        entries, new_cursor = _decision_log_tail(path, cursor)
        assert [e["i"] for e in entries] == [2]
        assert new_cursor == path.stat().st_size

    def test_no_new_data_keeps_cursor(self, tmp_path):
        path = tmp_path / "decision-log.jsonl"
        _write_lines(path, [json.dumps({"i": 1})])
        _, cursor = _decision_log_tail(path, 0)
        entries, new_cursor = _decision_log_tail(path, cursor)
        assert entries == []
        assert new_cursor == cursor

    def test_truncated_log_resets_cursor(self, tmp_path):
        """Regression: after a truncation/rotation the new file is *shorter*
        than the old cursor. The tail must re-sync from 0 instead of staying
        stuck past the new EOF (which would permanently miss new lines)."""
        path = tmp_path / "decision-log.jsonl"
        _write_lines(
            path,
            [json.dumps({"i": n}) for n in range(50)],
        )
        _, cursor = _decision_log_tail(path, 0)
        assert cursor > 100  # cursor sits well past a small replacement

        # Rotate: a fresh, shorter journal replaces the old one.
        _write_lines(path, [json.dumps({"i": "new-1"})])

        # Truncated file is smaller than the old cursor, so the whole
        # replacement file is read immediately (cursor re-anchors at 0). Old
        # logic kept the stale cursor and streamed [] forever.
        entries, new_cursor = _decision_log_tail(path, cursor)
        assert [e["i"] for e in entries] == ["new-1"]
        assert new_cursor == path.stat().st_size  # cursor re-anchored at EOF

        # Appending after the reset is picked up normally.
        path.write_text(
            path.read_text(encoding="utf-8") + json.dumps({"i": "new-2"}) + "\n",
            encoding="utf-8",
        )
        entries, _ = _decision_log_tail(path, new_cursor)
        assert [e["i"] for e in entries] == ["new-2"]

    def test_skips_corrupt_lines(self, tmp_path):
        path = tmp_path / "decision-log.jsonl"
        _write_lines(path, ["{bad json}", json.dumps({"i": 1})])
        entries, _ = _decision_log_tail(path, 0)
        assert [e["i"] for e in entries] == [1]
