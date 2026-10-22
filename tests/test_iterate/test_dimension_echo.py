"""The shell's echoed dimension becomes a cross-check on a self-declared attribution (场景②/⑧).

``mcp-shell`` answers a call that declared a dimension with an extra content item saying so
(``mcp-shell/src/dimension.ts``). Until this file, nothing in the Python harness read that line —
which would have made 面 A's new slot a surface with no consumer, the shape this repo keeps calling
undelivered. These cases drive the real chain:

    relay_answer            -> RelayedAnswer.echoed_dimension
    record_reverification   -> the ledger's own ``reverification`` row
    dimension_ledger        -> a contested attribution withheld from the kernel's verdict

Two fixture rules hold it together. The evidence pack is read out of the shipped corpus
(``tests/kernel_fixtures/evidence-decision.ok-01.json``, the same source the cross-language
comparison pins), because a hand-written pack would test this module's guess at the shape rather
than the shape; and the ledger rows are written by the real recorder, so a writer that stopped
publishing the field reddens the reader instead of both agreeing with each other.

What the reader is NOT allowed to do is in the last group: a missing echo never contests, a
malformed echo is silence rather than a competing claim, and the cross-check only ever subtracts.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.iterate import decision_log, dimension_ledger, run_plan
from iterate_harness.iterate.decision_log import make_entry
from iterate_harness.iterate.glasspane_reverification import (
    ReverificationRun,
    relay_answer,
)
from iterate_harness.iterate.invariant_report import OPERATION_ID_PATTERN
from iterate_harness.iterate.reverify_trigger import (
    GLASSPANE_TRANSPORT_KEY,
    record_reverification_run,
    trigger_reverification,
)

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"

#: The shipped echo line, transcribed from ``mcp-shell/src/dimension.ts``. The prefix is the
#: contract; the sentence after the pipe is prose this reader must not depend on.
ECHO_SECURITY = (
    "dimension: security | declared by this call; this shell records no decision, so the ledger "
    "gains an attribution only where a decision is written for this operation"
)
PROVIDER_LINE = (
    "provider: glasspane-engine-ae version 0.1.9 method last_evidence"
    " | declared prerequisites: evidencePack with at least one non-empty signal field"
)


def _real_pack() -> dict[str, Any]:
    corpus = json.loads((CORPUS / "evidence-decision.ok-01.json").read_text(encoding="utf-8"))
    for case in corpus["cases"]:
        candidate = case.get("input")
        if isinstance(candidate, dict) and isinstance(candidate.get("operationId"), str):
            return candidate
    raise AssertionError("the corpus carries no pack; nothing here would be production-shaped")


PACK = _real_pack()
OP = str(PACK["operationId"])
assert OPERATION_ID_PATTERN.match(OP), OP


def _reply(echo: str | None = None) -> list[str]:
    """The shipped multi-item reply: frame, provider attribution, and the echo where declared."""
    items = [json.dumps({"evidencePack": PACK}), PROVIDER_LINE]
    if echo is not None:
        items.append(echo)
    return items


def _frame_item() -> str:
    return json.dumps({"evidencePack": PACK})


def _plan(tmp_path: Path, dimensions: list[str]) -> None:
    plan = run_plan.build_run_plan({"dimensions": dimensions}, source_name="test")
    run_plan.write_run_plan(plan, run_plan.plan_path(tmp_path))


def _decision(tmp_path: Path, dimension: str, operation_id: str = OP) -> None:
    decision_log.append_entry(
        tmp_path,
        make_entry(
            entry_type="decision",
            round_number=1,
            data={
                "kind": "evidence",
                "summary": f"engine answered for {dimension}",
                "dimension": dimension,
                "operationId": operation_id,
            },
        ),
    )


def _record_echo(tmp_path: Path, echoed: str | None) -> None:
    """One ``reverification`` ledger row written by the real recorder, echoing ``echoed``."""
    answer = relay_answer(
        "gp_last_evidence",
        {"operationId": OP},
        _reply(None if echoed is None else f"dimension: {echoed} | declared by this call"),
    )
    record_reverification_run(
        tmp_path,
        1,
        {"schemaVersion": "iterate.reverification/0.1", "trigger": {}, "targets": {}},
        ReverificationRun(request_digest="iv_" + "a" * 64, attempted=True, answers=(answer,)),
    )


# ---------------------------------------------------------------- extraction from the reply itself


def test_the_echo_item_is_read_and_stays_verbatim_beside_the_frame() -> None:
    answer = relay_answer("gp_last_evidence", {"operationId": OP}, _reply(ECHO_SECURITY))
    assert answer.state == "answered"
    assert answer.echoed_dimension == "security"
    # The line is not consumed by being read: the model still gets it exactly as the shell wrote it.
    assert ECHO_SECURITY in answer.annotations
    assert PROVIDER_LINE in answer.annotations


def test_a_reply_without_an_echo_claims_nothing() -> None:
    answer = relay_answer("gp_last_evidence", {"operationId": OP}, _reply())
    assert answer.echoed_dimension is None
    assert len(answer.annotations) == 1


def test_the_single_item_shape_has_no_echo_to_read() -> None:
    answer = relay_answer("gp_last_evidence", {"operationId": OP}, _frame_item())
    assert answer.state == "answered"
    assert answer.echoed_dimension is None


def test_a_malformed_or_shifted_echo_is_silence_not_a_competing_claim() -> None:
    for line in [
        "dimension: Spec Compliance | declared by this call",
        "dimension: spec_compliance | declared by this call",
        "dimension:  | declared by this call",
        "the dimension: security | not an echo the shell writes",
    ]:
        answer = relay_answer("gp_diagnose", {}, [_frame_item(), line])
        assert answer.echoed_dimension is None, line
        assert line in answer.annotations, "the side item is still the model's to read"


# ------------------------------------------------------------------ what lands in the ledger


def test_the_recorder_publishes_the_echo_as_a_field_not_only_as_prose(tmp_path: Path) -> None:
    """The reader must not have to scrape a sentence, so the row carries the id structurally."""

    def transport(tool: str, arguments: dict[str, Any]) -> object:
        return _reply(ECHO_SECURITY)

    from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY
    from iterate_harness.iterate.loop_gates import measured_gates

    (tmp_path / "README.md").write_text("# real project\n", encoding="utf-8")
    (tmp_path / "iterate.config.yaml").write_text(
        "invariants:\n  ensure:\n    - README.md\n    - not-a-real-file.md\n"
        "reviewer:\n  evidence_validation: false\n",
        encoding="utf-8",
    )
    kernel = asyncio.run(_failing_kernel(tmp_path))
    decision_log.append_entry(
        tmp_path,
        make_entry(
            entry_type="decision",
            round_number=1,
            data={"kind": "evidence", "summary": "cited", "operationId": OP},
        ),
    )
    metadata = {DEFENSIVE_KERNEL_KEY: kernel, GLASSPANE_TRANSPORT_KEY: transport}
    published = trigger_reverification(
        project_root=tmp_path,
        metadata=metadata,
        gates=measured_gates(
            evidence_validation=False,
            findings=[],
            cwd=tmp_path,
            metadata={DEFENSIVE_KERNEL_KEY: kernel},
        ),
        round_number=1,
        planned_dimensions=["security"],
    )
    assert published is not None and published["attempted"] is True
    echoed = [answer.get("echoedDimension") for answer in published["answers"]]
    assert "security" in echoed, published["answers"]

    rows = [entry.data for entry in decision_log.read_entries(tmp_path)]
    reverification_rows = [
        row for row in rows if isinstance(row, dict) and row.get("kind") == "reverification"
    ]
    assert reverification_rows, "the send earned no ledger row"
    assert any(
        answer.get("echoedDimension") == "security" for answer in reverification_rows[-1].get("answers", [])
    ), reverification_rows[-1]


async def _failing_kernel(tmp_path: Path):
    from iterate_harness.defensive.kernel import DefensiveKernel
    from iterate_harness.iterate.config_loader import load_effective_config

    kernel = DefensiveKernel(tmp_path, load_effective_config(tmp_path))
    await kernel.check_invariants()
    assert kernel.last_invariant_report is not None
    return kernel


# ------------------------------------------------------- the cross-check over the ledger


def test_a_disagreement_between_declaration_and_echo_is_withheld(tmp_path: Path) -> None:
    _plan(tmp_path, ["security", "correctness"])
    _decision(tmp_path, "security")
    _record_echo(tmp_path, "correctness")

    contested = dimension_ledger.contested_attributions(tmp_path)
    assert contested == [
        {"dimension": "security", "operationId": OP, "echoedDimension": "correctness"}
    ], contested
    built = dimension_ledger.project_dimension_context(tmp_path)
    assert built is not None
    # The contested decision is the only evidence `security` had, so it reads unverified again.
    assert {entry.id: entry.status for entry in built.planned} == {
        "security": "unverified",
        "correctness": "unverified",
    }, built


def test_an_agreeing_echo_leaves_the_attribution_standing(tmp_path: Path) -> None:
    _plan(tmp_path, ["security"])
    _decision(tmp_path, "security")
    _record_echo(tmp_path, "security")
    assert dimension_ledger.contested_attributions(tmp_path) == []
    built = dimension_ledger.project_dimension_context(tmp_path)
    assert built is not None and built.planned[0].status == "verified"


def test_no_echo_never_contests(tmp_path: Path) -> None:
    """Silence from the shell is not disagreement — the other surface declares in metadata."""
    _plan(tmp_path, ["security"])
    _decision(tmp_path, "security")
    _record_echo(tmp_path, None)
    assert dimension_ledger.contested_attributions(tmp_path) == []
    built = dimension_ledger.project_dimension_context(tmp_path)
    assert built is not None and built.planned[0].status == "verified"


def test_the_cross_check_only_subtracts(tmp_path: Path) -> None:
    """An echo can demote a decision; it can never evidence a dimension the ledger did not record."""
    _plan(tmp_path, ["security", "correctness"])
    _record_echo(tmp_path, "correctness")
    built = dimension_ledger.project_dimension_context(tmp_path)
    assert built is not None
    assert {entry.id: entry.status for entry in built.planned} == {
        "security": "unverified",
        "correctness": "unverified",
    }
    assert dimension_ledger.recorded_dimension_counts(tmp_path) == {}


def test_the_verdict_and_the_withheld_count_come_from_one_read(tmp_path: Path) -> None:
    _plan(tmp_path, ["security"])
    _decision(tmp_path, "security")
    _record_echo(tmp_path, "correctness")
    built, contested = dimension_ledger.dimension_context_with_contested(tmp_path)
    assert len(contested) == 1
    assert built == dimension_ledger.project_dimension_context(tmp_path)
    assert built is not None and built.planned[0].status == "unverified"


def test_a_dimension_with_a_second_uncontested_decision_stays_verified(tmp_path: Path) -> None:
    """Withholding one decision is not striking the dimension out."""
    other = "op_" + "B" * 26
    assert OPERATION_ID_PATTERN.match(other), other
    _plan(tmp_path, ["security"])
    _decision(tmp_path, "security")
    _decision(tmp_path, "security", other)
    _record_echo(tmp_path, "correctness")
    assert len(dimension_ledger.contested_attributions(tmp_path)) == 1
    built = dimension_ledger.project_dimension_context(tmp_path)
    assert built is not None and built.planned[0].status == "verified"
    assert dimension_ledger.recorded_dimension_counts(tmp_path) == {"security": 2}


# ------------------------------------------------------------------- the published coverage line


def test_the_coverage_line_names_withholding_only_when_it_happened(tmp_path: Path) -> None:
    from iterate_harness.iterate.config_loader import load_effective_config
    from iterate_harness.tools.base import ToolExecutionContext
    from iterate_harness.tools.iterate_tools import _dimension_coverage_line

    _plan(tmp_path, ["security"])
    _decision(tmp_path, "security")
    context = ToolExecutionContext(cwd=tmp_path)
    effective = load_effective_config(tmp_path)
    plain = _dimension_coverage_line(context, effective)
    assert plain is not None and "withheld" not in plain

    _record_echo(tmp_path, "correctness")
    contested_line = _dimension_coverage_line(context, effective)
    assert contested_line is not None
    # The verdict itself moves — the withheld decision was the only evidence — and the line says
    # why, so a reader who wonders where the decision went has an answer in the same sentence.
    assert "1/1 dimensions verified" in plain
    assert "0/1 dimensions verified" in contested_line
    assert "1 attribution(s) withheld" in contested_line


def test_the_new_names_resolve_through_the_lazy_export_map() -> None:
    from iterate_harness import iterate as iterate_pkg

    for name in (
        "contested_attributions",
        "dimension_context_with_contested",
        "echoed_dimension_map",
        "ECHOED_DIMENSION_DATA_KEY",
    ):
        assert getattr(iterate_pkg, name) is getattr(dimension_ledger, name), name
        assert name in iterate_pkg.__all__, name


@pytest.mark.parametrize("letter", ["B", "C"])
def test_the_operation_id_fixtures_follow_the_imported_pattern(letter: str) -> None:
    """A fixture that stopped matching the pattern would make every case silently unattributed."""
    assert OPERATION_ID_PATTERN.match(f"op_{letter * 26}")
