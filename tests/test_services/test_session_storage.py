"""Tests for session persistence."""

from __future__ import annotations

import json
from pathlib import Path

from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.engine.messages import ConversationMessage, TextBlock
from iterate_harness.services.session_storage import (
    export_session_markdown,
    get_project_session_dir,
    list_session_snapshots,
    load_session_snapshot,
    save_session_snapshot,
)


def test_save_and_load_session_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    path = save_session_snapshot(
        cwd=project,
        model="claude-test",
        system_prompt="system",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hello")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=2),
        tool_metadata={
            "task_focus_state": {"goal": "Fix compact carry-over"},
            "recent_verified_work": ["Focused session storage test passed"],
        },
    )

    assert path.exists()
    snapshot = load_session_snapshot(project)
    assert snapshot is not None
    assert snapshot["model"] == "claude-test"
    assert snapshot["usage"]["output_tokens"] == 2
    assert snapshot["tool_metadata"]["task_focus_state"]["goal"] == "Fix compact carry-over"
    assert snapshot["tool_metadata"]["recent_verified_work"] == ["Focused session storage test passed"]


def test_export_session_markdown(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    path = export_session_markdown(
        cwd=project,
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="hello")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
        ],
    )

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "IterateHarness Session Transcript" in content
    assert "hello" in content
    assert "world" in content


def test_load_session_snapshot_sanitizes_legacy_empty_assistant_messages(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    target_dir = get_project_session_dir(project)
    payload = {
        "session_id": "legacy123",
        "cwd": str(project),
        "model": "claude-test",
        "system_prompt": "system",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": None},
            {"role": "assistant", "content": []},
            {"role": "assistant", "content": [{"type": "text", "text": "world"}]},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "tool_metadata": {},
        "created_at": 1.0,
        "summary": "hello",
        "message_count": 4,
    }
    (target_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    snapshot = load_session_snapshot(project)
    assert snapshot is not None
    assert snapshot["message_count"] == 2
    assert [message["role"] for message in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][1]["content"][0]["text"] == "world"


def test_list_session_snapshots_tolerates_corrupt_created_at(tmp_path: Path, monkeypatch):
    """A foreign/corrupt session file with a non-numeric ``created_at`` must not
    crash listing (mixed-type sort) or timestamp rendering."""
    monkeypatch.setenv("ITERATE_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    target_dir = get_project_session_dir(project)

    base = {
        "cwd": str(project),
        "model": "claude-test",
        "system_prompt": "system",
        "messages": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "tool_metadata": {},
    }
    good = {**base, "session_id": "good", "created_at": 2.0, "summary": "good"}

    # corrupt created_at as a human-readable string (the sort breaker)
    string_ts = {**base, "session_id": "string", "created_at": "2026-09-05T00:00:00Z", "summary": "string"}

    # None (missing) created_at
    none_ts = {**base, "session_id": "none", "summary": "none"}

    for sid, payload in (("good", good), ("string", string_ts), ("none", none_ts)):
        (target_dir / f"session-{sid}.json").write_text(json.dumps(payload), encoding="utf-8")

    sessions = list_session_snapshots(project, limit=10)
    assert len(sessions) == 3
    ids = {session["session_id"] for session in sessions}
    assert ids == {"good", "string", "none"}
    # All coerced to numeric epoch floats (no mixed-type sort crash).
    for session in sessions:
        assert isinstance(session["created_at"], float)
    # Descending by created_at.
    timestamps = [session["created_at"] for session in sessions]
    assert timestamps == sorted(timestamps, reverse=True)
