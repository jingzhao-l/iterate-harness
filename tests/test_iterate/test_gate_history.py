"""``gate_history`` — the structured half of the recipe-gate read (design §17.3 P9).

The CLI renderer already had tests; these pin the fields the WebUI consumes and
the fact that both surfaces read the same records, so a page can never show a
different answer from ``ih iterate gate``.
"""

from __future__ import annotations

from pathlib import Path

from iterate_harness.iterate.decision_log import DecisionLogEntry, append_entry
from iterate_harness.iterate.glasspane_recipe import gate_history, render_gate_state


def _append(root: Path, *, timestamp: str, round_number: int, entry_type: str, data: dict[str, object]) -> None:
    append_entry(
        root,
        DecisionLogEntry(
            timestamp=timestamp,
            round=round_number,
            type=entry_type,
            data=data,
        ),
    )


def test_an_empty_project_has_no_records(tmp_path: Path) -> None:
    assert gate_history(tmp_path) == ()


def test_entries_without_a_gate_are_ignored(tmp_path: Path) -> None:
    _append(
        tmp_path,
        timestamp="2026-01-01T00:00:00+00:00",
        round_number=1,
        entry_type="report",
        data={"verdict": "approved", "findings": []},
    )
    assert gate_history(tmp_path) == ()


def test_records_are_oldest_first_and_carry_the_published_fields(tmp_path: Path) -> None:
    _append(
        tmp_path,
        timestamp="2026-01-01T00:00:00+00:00",
        round_number=1,
        entry_type="reverify",
        data={
            "recipeStepGate": {
                "allowed": True,
                "invariantState": "pass",
                "recipeDigest": "sha256:" + "a" * 64,
                "steps": ["build", "test"],
                "fixProof": {
                    "entryId": "dl_" + "A" * 26,
                    "operationId": "op_" + "B" * 26,
                    "outcome": "pass",
                    "ledger": "/tmp/ledger.jsonl",
                    "ledgerSource": "link",
                    "createdAt": "2025-12-31T23:00:00.000Z",
                },
            }
        },
    )
    _append(
        tmp_path,
        timestamp="2026-01-02T00:00:00+00:00",
        round_number=2,
        entry_type="reverify",
        data={
            "recipeStepGate": {
                "allowed": False,
                "invariantState": "fail",
                "steps": ["build"],
                "refusal": {
                    "code": "RECIPE_GATE_E_INVARIANT_GATE",
                    "message": "the invariant verdict was not a pass",
                    "remedy": "fix it, then re-run",
                },
            }
        },
    )

    first, second = gate_history(tmp_path)
    assert (first.round, first.allowed, first.invariant_verdict) == (1, True, "pass")
    assert first.recipe_digest == "sha256:" + "a" * 64
    assert first.steps == ("build", "test")
    assert first.fix_proof == {
        "entry_id": "dl_" + "A" * 26,
        "operation_id": "op_" + "B" * 26,
        "outcome": "pass",
        "ledger": "/tmp/ledger.jsonl",
        "ledger_source": "link",
        "created_at": "2025-12-31T23:00:00.000Z",
    }
    assert second.allowed is False
    assert second.fix_proof is None
    assert second.refusal == {
        "code": "RECIPE_GATE_E_INVARIANT_GATE",
        "message": "the invariant verdict was not a pass",
        "remedy": "fix it, then re-run",
    }
    assert first.to_dict()["round"] == 1
    assert second.to_dict()["refusal"]["code"] == "RECIPE_GATE_E_INVARIANT_GATE"


def test_a_malformed_payload_is_reported_rather_than_raised(tmp_path: Path) -> None:
    # A hand-written or older writer's line must not take the page down: every
    # field falls back to a stated default instead of a KeyError/TypeError.
    _append(
        tmp_path,
        timestamp="2026-01-01T00:00:00+00:00",
        round_number=7,
        entry_type="reverify",
        data={
            "recipeStepGate": {
                "steps": "build",  # not a list
                "fixProof": "not-a-mapping",
                "refusal": {"code": 42},
            }
        },
    )

    (record,) = gate_history(tmp_path)
    assert record.allowed is False
    assert record.invariant_verdict == "unknown"
    assert record.recipe_digest is None
    assert record.steps == ()
    assert record.fix_proof is None
    assert record.refusal == {"code": "42", "message": "", "remedy": ""}


def test_the_renderer_keeps_its_exact_text(tmp_path: Path) -> None:
    # Both surfaces read the same records; this pins the terminal wording the
    # WebUI's "raw output" block compares against line for line.
    _append(
        tmp_path,
        timestamp="2026-01-01T00:00:00+00:00",
        round_number=2,
        entry_type="reverify",
        data={
            "recipeStepGate": {
                "allowed": True,
                "invariantState": "pass",
                "recipeDigest": "sha256:" + "a" * 64,
                "steps": ["build", "test"],
                "fixProof": {
                    "entryId": "dl_" + "A" * 26,
                    "operationId": "op_" + "B" * 26,
                    "outcome": "pass",
                    "ledger": "/tmp/ledger.jsonl",
                    "ledgerSource": "link",
                    "createdAt": "2025-12-31T23:00:00.000Z",
                },
            }
        },
    )
    _append(
        tmp_path,
        timestamp="2026-01-02T00:00:00+00:00",
        round_number=3,
        entry_type="reverify",
        data={
            "recipeStepGate": {
                "allowed": False,
                "invariantState": "fail",
                "steps": ["build", "test"],
                "refusal": {
                    "code": "RECIPE_GATE_E_INVARIANT_GATE",
                    "message": "the invariant verdict was not a pass",
                    "remedy": "fix the failing invariant, then re-run",
                },
            }
        },
    )

    assert render_gate_state(tmp_path) == (
        "round 3: recipe gate — not allowed\n"
        "  invariant verdict: fail\n"
        "  steps configured: build, test\n"
        "  refused: RECIPE_GATE_E_INVARIANT_GATE: the invariant verdict was not a pass\n"
        "  remedy: fix the failing invariant, then re-run"
    )
