"""Tests for decision-log replay (openharness.iterate.replay)."""

from __future__ import annotations

import pytest

from openharness.commands.iterate import iterate_command_handler
from openharness.commands.registry import CommandContext
from openharness.iterate.decision_log import DecisionLogEntry, append_entry
from openharness.iterate.replay import build_replay_lines, render_replay


def _entry(
    entry_type: str,
    round_number: int,
    timestamp: str,
    data: dict | None = None,
) -> DecisionLogEntry:
    return DecisionLogEntry(
        timestamp=timestamp, round=round_number, type=entry_type, data=data or {}
    )


TIMELINE = [
    _entry("round_start", 1, "2026-08-15T10:00:00+00:00", {"goal": "review everything"}),
    _entry("review_result", 1, "2026-08-15T10:01:30+00:00", {"newFindings": 3}),
    _entry("atomic_fix", 1, "2026-08-15T10:02:10+00:00", {"file": "src/app.py"}),
    _entry("validation", 1, "2026-08-15T10:02:40+00:00", {"command": "pytest"}),
    _entry(
        "report",
        2,
        "2026-08-15T10:05:00+00:00",
        {"verdict": "approved", "totalFindings": 1},
    ),
]


class TestBuildReplayLines:
    def test_empty_log_is_friendly(self):
        assert "nothing to replay" in build_replay_lines([])[0]

    def test_relative_offsets_from_first_entry(self):
        lines = build_replay_lines(TIMELINE)
        assert lines[0].startswith("[+0s] r1 round_start")
        assert "goal=review everything" in lines[0]
        assert lines[1].startswith("[+90s] r1 review_result")
        assert "newFindings=3" in lines[1]
        assert lines[4].startswith("[+300s] r2 report")
        assert "verdict=approved" in lines[4]
        assert lines[-1] == "(5 entries replayed)"

    def test_unparseable_timestamp_degrades(self):
        entries = [
            _entry("round_start", 1, "not-a-timestamp"),
            _entry("report", 1, "2026-08-15T10:00:00+00:00", {"verdict": "ok"}),
        ]
        lines = build_replay_lines(entries)
        assert "[+?s]" in lines[0]

    def test_unknown_type_falls_back_to_payload_preview(self):
        entries = [_entry("mystery", 1, "2026-08-15T10:00:00+00:00", {"x": 1})]
        line = build_replay_lines(entries)[0]
        assert "mystery" in line
        assert "x" in line  # raw payload preview

    def test_empty_payload_placeholder(self):
        entries = [_entry("decision", 1, "2026-08-15T10:00:00+00:00", {})]
        assert "(no payload)" in build_replay_lines(entries)[0]

    def test_long_payloads_are_truncated(self):
        entries = [
            _entry(
                "atomic_fix",
                1,
                "2026-08-15T10:00:00+00:00",
                {"summary": "x" * 500},
            )
        ]
        line = build_replay_lines(entries)[0]
        assert len(line) < 500
        assert "…" in line


class TestRenderReplay:
    def test_joins_lines(self):
        text = render_replay(TIMELINE)
        assert text.count("\n") == len(TIMELINE)  # entries + footer minus one
        assert "(5 entries replayed)" in text


def make_context(cwd) -> CommandContext:
    return CommandContext(engine=None, cwd=str(cwd))  # type: ignore[arg-type]


class TestSlashReplay:
    @pytest.mark.asyncio
    async def test_log_replay_flag(self, tmp_path):
        for entry in TIMELINE:
            append_entry(tmp_path, entry)
        result = await iterate_command_handler("log --replay", make_context(tmp_path))
        assert "[+90s] r1 review_result" in (result.message or "")
        assert "(5 entries replayed)" in (result.message or "")


class TestCliReplay:
    def test_cli_log_replay(self, tmp_path, monkeypatch, capsys):
        from openharness.cli import iterate_log

        monkeypatch.chdir(tmp_path)
        for entry in TIMELINE:
            append_entry(tmp_path, entry)
        iterate_log(tail=20, as_json=False, trend=False, replay=True)
        out = capsys.readouterr().out
        assert "[+300s] r2 report" in out
        assert "(5 entries replayed)" in out

    def test_cli_log_replay_empty(self, tmp_path, monkeypatch, capsys):
        from openharness.cli import iterate_log

        monkeypatch.chdir(tmp_path)
        iterate_log(tail=20, as_json=False, trend=False, replay=True)
        assert "nothing to replay" in capsys.readouterr().out
