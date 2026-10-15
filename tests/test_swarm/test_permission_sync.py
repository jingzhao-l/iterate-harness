"""Tests for swarm permission sync protocol: create/send/poll/handle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iterate_harness.swarm.permission_sync import (
    PermissionResolution,
    SwarmPermissionResponse,
    _is_read_only,
    _pending_request_path,
    _resolved_request_path,
    cleanup_old_resolutions,
    create_permission_request,
    delete_resolved_permission,
    handle_permission_request,
    poll_permission_response,
    read_resolved_permission,
    resolve_permission,
    send_permission_request,
    send_permission_response,
    send_permission_response_via_mailbox,
    validate_request_id,
    write_permission_request,
)


# ---------------------------------------------------------------------------
# _is_read_only heuristic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    ["read_file", "glob", "grep", "web_fetch", "web_search", "task_get", "task_list", "cron_list"],
)
def test_is_read_only_true_for_safe_tools(tool_name):
    assert _is_read_only(tool_name) is True


@pytest.mark.parametrize("tool_name", ["bash", "edit_file", "write_file", "task_create"])
def test_is_read_only_false_for_write_tools(tool_name):
    assert _is_read_only(tool_name) is False


# ---------------------------------------------------------------------------
# create_permission_request
# ---------------------------------------------------------------------------


def test_create_permission_request_has_unique_id():
    r1 = create_permission_request("Bash", "tu-1", {"command": "ls"})
    r2 = create_permission_request("Bash", "tu-2", {"command": "ls"})
    assert r1.id != r2.id


def test_create_permission_request_fields():
    req = create_permission_request(
        "Edit",
        "tu-xyz",
        {"file_path": "/tmp/f.py"},
        description="edit a file",
        permission_suggestions=[{"type": "allow"}],
    )
    assert req.tool_name == "Edit"
    assert req.tool_use_id == "tu-xyz"
    assert req.description == "edit a file"
    assert req.permission_suggestions == [{"type": "allow"}]


def test_create_permission_request_default_suggestions():
    req = create_permission_request("Bash", "tu-1", {})
    assert req.permission_suggestions == []


# ---------------------------------------------------------------------------
# SwarmPermissionResponse
# ---------------------------------------------------------------------------


def test_swarm_permission_response_defaults():
    resp = SwarmPermissionResponse(request_id="r1", allowed=True)
    assert resp.feedback is None
    assert resp.updated_rules == []


# ---------------------------------------------------------------------------
# send_permission_request writes to leader mailbox
# ---------------------------------------------------------------------------


async def test_send_permission_request_writes_to_leader(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    req = create_permission_request("Bash", "tu-1", {"command": "echo hi"})
    await send_permission_request(req, "myteam", "worker1", "leader")

    from iterate_harness.swarm.mailbox import TeammateMailbox
    mailbox = TeammateMailbox("myteam", "leader")
    messages = await mailbox.read_all(unread_only=False)
    assert len(messages) == 1
    assert messages[0].type == "permission_request"
    assert messages[0].payload["tool_name"] == "Bash"
    assert messages[0].payload["worker_id"] == "worker1"


# ---------------------------------------------------------------------------
# send_permission_response writes to worker mailbox
# ---------------------------------------------------------------------------


async def test_send_permission_response_writes_to_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    resp = SwarmPermissionResponse(request_id="r1", allowed=True, feedback=None)
    await send_permission_response(resp, "myteam", "worker1", "leader")

    from iterate_harness.swarm.mailbox import TeammateMailbox
    mailbox = TeammateMailbox("myteam", "worker1")
    messages = await mailbox.read_all(unread_only=False)
    assert len(messages) == 1
    assert messages[0].type == "permission_response"
    assert messages[0].payload["allowed"] is True


# ---------------------------------------------------------------------------
# handle_permission_request
# ---------------------------------------------------------------------------


async def test_handle_read_only_tool_auto_approved():
    req = create_permission_request("read_file", "tu-1", {"file_path": "/tmp/f.py"})
    checker = MagicMock()
    resp = await handle_permission_request(req, checker)
    assert resp.allowed is True
    checker.evaluate.assert_not_called()


async def test_handle_write_tool_delegates_to_checker():
    req = create_permission_request("Bash", "tu-2", {"command": "rm -rf /"})

    decision = MagicMock()
    decision.allowed = False
    decision.reason = "dangerous command"
    checker = MagicMock()
    checker.evaluate.return_value = decision

    resp = await handle_permission_request(req, checker)
    assert resp.allowed is False
    assert resp.feedback == "dangerous command"
    checker.evaluate.assert_called_once_with(
        "Bash", is_read_only=False, file_path=None, command="rm -rf /"
    )


async def test_handle_write_tool_allowed_by_checker():
    req = create_permission_request("Edit", "tu-3", {"file_path": "/src/main.py"})

    decision = MagicMock()
    decision.allowed = True
    decision.reason = None
    checker = MagicMock()
    checker.evaluate.return_value = decision

    resp = await handle_permission_request(req, checker)
    assert resp.allowed is True
    assert resp.feedback is None


# ---------------------------------------------------------------------------
# poll_permission_response timeout
# ---------------------------------------------------------------------------


async def test_poll_permission_response_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = await poll_permission_response("myteam", "worker1", "nonexistent-id", timeout=0.1)
    assert result is None


async def test_poll_permission_response_finds_matching_message(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Pre-write a response to the worker mailbox
    resp = SwarmPermissionResponse(request_id="req-abc", allowed=True)
    await send_permission_response(resp, "myteam", "worker1", "leader")

    result = await poll_permission_response("myteam", "worker1", "req-abc", timeout=2.0)
    assert result is not None
    assert result.allowed is True
    assert result.request_id == "req-abc"


async def test_poll_permission_response_decodes_text_envelope_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_AGENT_NAME", "leader")

    # Regression: send_permission_response_via_mailbox writes a TS-style text
    # envelope (JSON inside the "text" field). poll_permission_response used to
    # look for request_id only in the top-level payload, so an approval sent
    # through this flow was never matched and the worker timed out.
    resolution = PermissionResolution(
        decision="approved",
        resolved_by="leader",
        feedback=None,
        updated_input={"command": "echo hi"},
        permission_updates=[{"tool": "echo*", "allow": True}],
    )
    assert await send_permission_response_via_mailbox(
        "worker1", resolution, "req-text", team_name="myteam"
    )

    result = await poll_permission_response("myteam", "worker1", "req-text", timeout=2.0)
    assert result is not None
    assert result.request_id == "req-text"
    assert result.allowed is True
    assert result.feedback is None
    assert result.updated_rules == [{"tool": "echo*", "allow": True}]


async def test_poll_permission_response_decodes_text_envelope_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_AGENT_NAME", "leader")

    resolution = PermissionResolution(
        decision="rejected",
        resolved_by="leader",
        feedback="cannot write to secrets",
    )
    assert await send_permission_response_via_mailbox(
        "worker1", resolution, "req-rej", team_name="myteam"
    )

    result = await poll_permission_response("myteam", "worker1", "req-rej", timeout=2.0)
    assert result is not None
    assert result.allowed is False
    assert result.feedback == "cannot write to secrets"
    # The matched message is marked read so a later poll does not re-deliver it.
    assert await poll_permission_response("myteam", "worker1", "req-rej", timeout=0.1) is None


# ---------------------------------------------------------------------------
# request_id validation (path traversal guard)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_id",
    [
        "../../etc/passwd",
        "..",
        ".",
        "a/b",
        "a\\b",
        "/abs/path",
        "perm-1\n",
        "perm-1\x00",
        "",
        "x" * 129,  # over the 128-char cap
    ],
)
def test_validate_request_id_rejects_unsafe_ids(request_id):
    """Request IDs become ``permissions/{pending,resolved}/<id>.json``; an ID
    with ``../`` (or a separator / NUL / oversized) must be rejected so no
    caller can read, move, or delete files outside the permissions dir."""
    with pytest.raises(ValueError):
        validate_request_id(request_id)


def test_validate_request_id_accepts_generated_ids():
    assert validate_request_id("perm-1712345678901-abc1234")
    assert validate_request_id("sandbox-1712345678901-xyz9876")
    assert validate_request_id(create_permission_request("Bash", "tu-1", {}).id)


def test_pending_and_resolved_paths_reject_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(ValueError):
        _pending_request_path("myteam", "../escape")
    with pytest.raises(ValueError):
        _resolved_request_path("myteam", "../escape")
    assert not (tmp_path / "escape").exists()


async def test_resolve_permission_rejects_traversal_request_id(tmp_path, monkeypatch):
    """``resolve_permission`` used to interpolate the caller-supplied ID straight
    into both the pending and resolved paths; a ``../`` ID could read a file
    outside the pending dir and DELETE it (unlink after the "move")."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    victim = tmp_path / "victim.json"
    victim.write_text('{"secret": true}', encoding="utf-8")

    resolution = PermissionResolution(decision="approved", resolved_by="leader")
    with pytest.raises(ValueError):
        await resolve_permission("../victim", resolution, team_name="myteam")
    # The unlink-after-move path must not have deleted the victim.
    assert victim.exists()


async def test_delete_resolved_permission_rejects_traversal(tmp_path, monkeypatch):
    """``delete_resolved_permission`` unlinks the resolved path directly — with
    an unvalidated ID it was an arbitrary-file-delete primitive."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    victim = tmp_path / "important.txt"
    victim.write_text("do not delete", encoding="utf-8")

    with pytest.raises(ValueError):
        await delete_resolved_permission("../important.txt", team_name="myteam")
    assert victim.exists()


async def test_read_resolved_permission_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    outside = tmp_path / "leak.json"
    outside.write_text('{"id": "leak", "created_at": 1.0}', encoding="utf-8")

    with pytest.raises(ValueError):
        await read_resolved_permission("../leak", team_name="myteam")


async def test_write_permission_request_rejects_traversal_request_id(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    req = create_permission_request("Bash", "tu-1", {"command": "ls"})
    req.id = "../../evil-write"
    with pytest.raises(ValueError):
        await write_permission_request(req)
    assert not (tmp_path / "evil-write").exists()


async def test_cleanup_old_resolutions_handles_non_numeric_timestamp(tmp_path, monkeypatch):
    """A corrupt resolved file with a non-numeric ``resolved_at`` used to make
    the age arithmetic raise TypeError, crashing the whole cleanup pass."""
    import json as _json

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_TEAM_NAME", "myteam")
    req = create_permission_request("Bash", "tu-1", {"command": "ls"})
    req.status = "approved"
    req.resolved_at = "not-a-number"
    resolved_dir = tmp_path / ".iterate-harness" / "teams" / "myteam" / "permissions" / "resolved"
    resolved_dir.mkdir(parents=True)
    (resolved_dir / f"{req.id}.json").write_text(
        _json.dumps(req.to_dict()), encoding="utf-8"
    )

    cleaned = await cleanup_old_resolutions(team_name="myteam")
    # Corrupt file treated as stale and removed instead of raising.
    assert cleaned == 1
    assert not (resolved_dir / f"{req.id}.json").exists()
