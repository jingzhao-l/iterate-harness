"""Tests for the cross-process control channel (``ih iterate control``).

Two layers are covered here:

* the on-disk journal/state contract (append, poll, consume, clear, stale
  runs, malformed lines) — the part a second terminal depends on;
* the loop-policy wiring — a command must actually change the run: a nudge
  lands in the next-round instruction, a stop/pause ends the loop at the next
  round boundary even when automatic convergence is disabled, and neither is
  applied twice.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.iterate import control
from iterate_harness.iterate.control import (
    ControlChannel,
    ControlChannelError,
    append_command,
    begin_run,
    clear_pending,
    consume_commands,
    end_run,
    local_run_channel,
    read_pending,
    run_status,
)
from iterate_harness.iterate.decision_log import read_entries
from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY, IterateLoopPolicy


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def state(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "mode": "dry-run",
        "rounds_seen": 1,
        "total_findings": 2,
        "findings_by_round": [2],
        "converged": False,
        "by_dimension": {"correctness": 2},
    }
    base.update(overrides)
    return base


# --- journal / state contract ------------------------------------------------


def test_begin_run_publishes_a_live_state(tmp_path: Path) -> None:
    run_id = begin_run(tmp_path)
    view = run_status(tmp_path)
    assert view["active"] is True
    assert view["stale"] is False
    assert view["run"]["run_id"] == run_id
    assert view["run"]["pid"] == os.getpid()
    assert view["pendingCount"] == 0


def test_a_nudge_is_pending_until_consumed_then_disappears(tmp_path: Path) -> None:
    begin_run(tmp_path)
    command = append_command(tmp_path, "nudge", "focus on auth")
    assert command.kind == "nudge"
    assert command.content == "focus on auth"

    pending = read_pending(tmp_path)
    assert [c.id for c in pending] == [command.id]

    consume_commands(tmp_path, pending)
    assert read_pending(tmp_path) == []
    assert run_status(tmp_path)["pendingCount"] == 0


def test_consuming_is_monotonic_and_idempotent(tmp_path: Path) -> None:
    begin_run(tmp_path)
    first = append_command(tmp_path, "nudge", "one")
    second = append_command(tmp_path, "nudge", "two")
    pending = read_pending(tmp_path)
    consume_commands(tmp_path, [pending[0]])
    assert [c.id for c in read_pending(tmp_path)] == [second.id]
    # Re-consuming an older command must not rewind the cursor.
    consume_commands(tmp_path, [first])
    assert [c.id for c in read_pending(tmp_path)] == [second.id]


def test_no_active_run_refuses_a_command_with_a_remedy(tmp_path: Path) -> None:
    with pytest.raises(ControlChannelError) as excinfo:
        append_command(tmp_path, "stop")
    assert excinfo.value.code == "no_active_run"
    assert "ih iterate run" in excinfo.value.remedy


def test_finished_run_refuses_further_commands(tmp_path: Path) -> None:
    run_id = begin_run(tmp_path)
    end_run(tmp_path, run_id)
    assert run_status(tmp_path)["active"] is False
    assert run_status(tmp_path)["stale"] is False
    with pytest.raises(ControlChannelError):
        append_command(tmp_path, "nudge", "late")


def test_end_run_does_not_clobber_a_newer_run(tmp_path: Path) -> None:
    old_id = begin_run(tmp_path)
    new_id = begin_run(tmp_path)
    end_run(tmp_path, old_id)
    view = run_status(tmp_path)
    assert view["run"]["run_id"] == new_id
    assert view["active"] is True


def test_stale_running_state_is_reported_but_not_active(tmp_path: Path) -> None:
    begin_run(tmp_path)
    state_path = tmp_path / ".iterate" / control.STATE_FILE
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["pid"] = 999_999_999  # certainly dead
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    view = run_status(tmp_path)
    assert view["active"] is False
    assert view["stale"] is True
    with pytest.raises(ControlChannelError):
        append_command(tmp_path, "stop")


def test_invalid_kind_and_empty_nudge_are_refused(tmp_path: Path) -> None:
    begin_run(tmp_path)
    with pytest.raises(ControlChannelError) as excinfo:
        append_command(tmp_path, "explode")
    assert excinfo.value.code == "invalid_kind"
    with pytest.raises(ControlChannelError) as excinfo:
        append_command(tmp_path, "nudge", "   ")
    assert excinfo.value.code == "empty_nudge"
    with pytest.raises(ControlChannelError) as excinfo:
        append_command(tmp_path, "nudge", "x" * (control.MAX_CONTENT_CHARS + 1))
    assert excinfo.value.code == "too_long"


def test_clear_pending_drops_unconsumed_commands(tmp_path: Path) -> None:
    begin_run(tmp_path)
    append_command(tmp_path, "nudge", "one")
    append_command(tmp_path, "stop")
    assert clear_pending(tmp_path) == 2
    assert read_pending(tmp_path) == []
    assert clear_pending(tmp_path) == 0


def test_malformed_journal_lines_are_skipped(tmp_path: Path) -> None:
    begin_run(tmp_path)
    journal = tmp_path / ".iterate" / control.JOURNAL_FILE
    journal.write_text(
        'not json\n'
        '{"id": "bad-kind", "kind": "explode"}\n'
        '{"id": "ok", "kind": "nudge", "content": "real", "created_at": 1}\n',
        encoding="utf-8",
    )
    pending = read_pending(tmp_path)
    assert [c.id for c in pending] == ["ok"]


def test_a_truncated_journal_restarts_from_zero(tmp_path: Path) -> None:
    begin_run(tmp_path)
    append_command(tmp_path, "nudge", "one")
    consume_commands(tmp_path, read_pending(tmp_path))
    journal = tmp_path / ".iterate" / control.JOURNAL_FILE
    journal.write_text(
        json.dumps({"id": "fresh", "kind": "nudge", "content": "new", "created_at": 2}) + "\n",
        encoding="utf-8",
    )
    assert [c.id for c in read_pending(tmp_path)] == ["fresh"]


def test_status_includes_checkpoint_and_last_decision(tmp_path: Path) -> None:
    from iterate_harness.iterate.checkpoint import save_checkpoint
    from iterate_harness.iterate.decision_log import append_entry, make_entry

    begin_run(tmp_path)
    save_checkpoint(
        tmp_path,
        round=2,
        new_findings=1,
        total_findings=3,
        per_dimension={"correctness": 3},
        converged=False,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.25,
        mode="dry-run",
    )
    append_entry(tmp_path, make_entry(entry_type="round_start", round_number=2))
    view = run_status(tmp_path)
    assert view["checkpoint"]["round"] == 2
    assert view["checkpoint"]["total_findings"] == 3
    assert view["lastEntry"]["type"] == "round_start"
    assert view["lastEntry"]["round"] == 2


def test_a_webui_run_is_active_but_not_controllable(tmp_path: Path) -> None:
    from iterate_harness.iterate.run_lock import ROLE_WEBUI, claim_run_lease

    with claim_run_lease(tmp_path, role=ROLE_WEBUI):
        view = run_status(tmp_path)
    assert view["active"] is False
    assert view["leaseActive"] is True
    assert view["lease"]["role"] == ROLE_WEBUI


def test_local_channel_only_for_the_owning_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    begin_run(tmp_path)
    assert local_run_channel(tmp_path) is not None
    state_path = tmp_path / ".iterate" / control.STATE_FILE
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["pid"] = os.getpid() + 1
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    # The pid is alive (it is us + 1 only in this synthetic check, so force
    # liveness to prove the ownership rule rather than the liveness rule).
    monkeypatch.setattr(control, "pid_is_live", lambda pid: True)
    assert local_run_channel(tmp_path) is None


# --- loop-policy wiring ------------------------------------------------------


def _policy(tmp_path: Path, **kwargs: object) -> IterateLoopPolicy:
    return IterateLoopPolicy(
        max_review_rounds=5,
        control_channel=ControlChannel(tmp_path),
        **kwargs,
    )


def test_a_nudge_reaches_the_next_round_instruction(tmp_path: Path) -> None:
    begin_run(tmp_path)
    append_command(tmp_path, "nudge", "focus on auth")
    policy = _policy(tmp_path)
    policy.before_request()
    decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
    assert decision.stop_reason is None
    assert decision.inject_message is not None
    assert "[用户督促]" in decision.inject_message
    assert "focus on auth" in decision.inject_message
    assert read_pending(tmp_path) == []


def test_a_stop_ends_the_loop_at_the_next_boundary(tmp_path: Path) -> None:
    begin_run(tmp_path)
    append_command(tmp_path, "stop", "operator is done for today")
    policy = _policy(tmp_path)
    decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
    assert decision.stop_reason is not None
    assert "operator stop" in decision.stop_reason
    assert "operator is done for today" in decision.stop_reason
    assert decision.progress is not None
    # The intervention is part of the run's durable audit trail.
    entries = read_entries(tmp_path)
    assert any(
        entry.type == "decision"
        and entry.data.get("kind") == "control-stop"
        and entry.data.get("detail") == "operator is done for today"
        for entry in entries
    )


def test_a_stop_is_honored_even_when_automatic_stops_are_disabled(tmp_path: Path) -> None:
    begin_run(tmp_path)
    append_command(tmp_path, "stop")
    policy = _policy(tmp_path, stop_on_convergence=False)
    decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
    assert decision.stop_reason is not None
    assert "operator stop" in decision.stop_reason


def test_a_pause_explains_that_the_checkpoint_is_resumable(tmp_path: Path) -> None:
    begin_run(tmp_path)
    append_command(tmp_path, "pause")
    policy = _policy(tmp_path)
    decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
    assert decision.stop_reason is not None
    assert "operator pause" in decision.stop_reason
    assert "ih iterate resume --run" in decision.stop_reason


def test_a_stop_does_not_fire_before_a_round_boundary(tmp_path: Path) -> None:
    begin_run(tmp_path)
    append_command(tmp_path, "stop")
    policy = _policy(tmp_path)
    # A turn with no new aggregate has no boundary to stop on.
    decision = policy.on_turn_end({}, UsageSnapshot(), "m")
    assert decision.stop_reason is None
    assert policy._control_stop_reason is not None
    # The next aggregate is the boundary that acts on it.
    decision = policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
    assert decision.stop_reason is not None


def test_a_command_is_applied_once_only(tmp_path: Path) -> None:
    begin_run(tmp_path)
    append_command(tmp_path, "nudge", "only once")
    policy = _policy(tmp_path)
    policy.on_turn_end({ITERATE_STATE_KEY: state()}, UsageSnapshot(), "m")
    # Simulate a lost cursor: the same journal is re-read, but the policy's
    # in-memory id set prevents a second injection.
    (tmp_path / ".iterate" / control.CURSOR_FILE).unlink()
    policy.on_turn_end(
        {ITERATE_STATE_KEY: state(rounds_seen=2, findings_by_round=[2, 1], total_findings=3)},
        UsageSnapshot(),
        "m",
    )
    assert policy._pending_nudges == []
    # The first turn drained the nudge; the second must not have re-queued it.
    journal = tmp_path / ".iterate" / control.JOURNAL_FILE
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1
