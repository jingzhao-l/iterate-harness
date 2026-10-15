"""The Python half of the kernel's dimensionContext, checked against its fixture.

The important test here is ``test_matches_the_kernel_fixture``. The TypeScript
implementation in ``@iterate/kernel`` ships
``fixtures/dimension-context.ok-01.json``; this file re-implements the same rules
and must reproduce it byte for byte. If either side changes the arithmetic and
the other does not, one of the two repos' CI goes red — which is the whole point
of keeping the rules in a fixture rather than in prose.

The fixture is read from the vendored corpus in ``tests/kernel_fixtures`` (pinned
by ``test_kernel_fixtures.py``), or from a live kernel checkout when
``KERNEL_FIXTURES_DIR`` points at one. It is never skipped: an absent corpus is a
failure, because a cross-implementation check that does not run is the one thing
this file exists to do.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from iterate_harness.iterate.dimension_context import (
    DIMENSION_E_DUPLICATE,
    DIMENSION_E_EMPTY,
    DIMENSION_E_ID,
    DimensionContextError,
    DimensionEvidence,
    PlannedDimension,
    dimension_context,
    dimension_context_from_evidence,
    format_dimension_context,
)

#: The nine ids the config owns. Mirrored here only as test data — the module
#: itself never enumerates ids, and ``test_dimension_lock.py`` in the skill repo
#: is what keeps this list honest.
CANONICAL = [
    "correctness",
    "security",
    "performance",
    "architecture",
    "style-tests",
    "tech-debt",
    "spec-compliance",
    "frontend-backend",
    "ui-ux",
]


#: The vendored half of the kernel contract, pinned by ``test_kernel_fixtures.py``.
KERNEL_CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"


def _fixture_path() -> Path:
    """Locate the kernel fixture. A missing corpus is a failure, never a skip.

    A skipped cross-implementation check is the worst outcome this arrangement can
    produce: the suite reports green while the one test that could prove the Python
    and TypeScript halves agree has proven nothing. The corpus is vendored precisely
    so that this test always has bytes to compare against; ``KERNEL_FIXTURES_DIR``
    still overrides it, which is what the kernel repo's own CI uses to point the
    comparison at its live ``kernel/fixtures``.
    """
    name = "dimension-context.ok-01.json"
    override = os.environ.get("KERNEL_FIXTURES_DIR")
    candidates = [Path(override) / name] if override else []
    candidates.append(KERNEL_CORPUS / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise AssertionError(
        f"kernel fixture {name} is not present (looked in: {searched}). Restore it from "
        "iterate-kernel's fixtures/ and re-pin its sha256 in "
        "tests/kernel_fixtures/manifest.json — skipping this test is not a remedy."
    )


def test_matches_the_kernel_fixture() -> None:
    """This implementation reproduces the TypeScript one byte for byte."""
    path = _fixture_path()

    fixture = json.loads(path.read_text(encoding="utf-8"))
    raw_input = fixture["input"]

    planned = [
        PlannedDimension(
            id=item["id"],
            label=item.get("label"),
            focus=item.get("focus"),
        )
        for item in raw_input["planned"]
    ]
    context = dimension_context(planned, raw_input["recorded"])

    assert context.to_json() == fixture["expected"]
    assert format_dimension_context(context) == fixture["expectedLine"]
    # The invariant the fixture asserts by construction, re-checked here so a
    # future edit to the fixture cannot quietly break it.
    assert context.totals["verified"] + context.totals["unverified"] == context.totals["planned"]


def test_planned_order_is_the_callers() -> None:
    context = dimension_context(CANONICAL, {"security": {"decisions": 1}})
    assert [entry.id for entry in context.planned] == CANONICAL


def test_absent_and_zero_are_the_same_fact() -> None:
    absent = dimension_context(["security"], {})
    zero = dimension_context(["security"], {"security": {"decisions": 0}})
    assert absent.to_json() == zero.to_json()
    assert absent.planned[0].status == "unverified"


def test_unplanned_is_reported_not_dropped() -> None:
    context = dimension_context(
        ["correctness"],
        {"correctness": {"decisions": 2}, "concurrency": {"decisions": 7}},
    )
    assert context.totals["unplanned"] == 1
    # The unplanned decision still counts toward the total.
    assert context.totals["decisions"] == 9
    assert [entry.id for entry in context.unplanned] == ["concurrency"]


def test_unplanned_is_sorted() -> None:
    first = dimension_context(["security"], {"zeta": {}, "alpha": {}, "mu": {}})
    second = dimension_context(["security"], {"mu": {}, "zeta": {}, "alpha": {}})
    assert [entry.id for entry in first.unplanned] == ["alpha", "mu", "zeta"]
    assert first.to_json() == second.to_json()


def test_duplicate_planned_raises() -> None:
    with pytest.raises(DimensionContextError) as excinfo:
        dimension_context(["security", "security"], {})
    assert excinfo.value.code == DIMENSION_E_DUPLICATE
    assert excinfo.value.dimension == "security"
    assert excinfo.value.remedy


@pytest.mark.parametrize("bad", ["Security", "correctness_", "-leading", "with space", "double--dash"])
def test_non_kebab_ids_are_refused(bad: str) -> None:
    with pytest.raises(DimensionContextError) as excinfo:
        dimension_context([bad], {})
    assert excinfo.value.code == DIMENSION_E_ID


def test_empty_plan_is_refused() -> None:
    with pytest.raises(DimensionContextError):
        dimension_context([], {})


def test_negative_decision_count_is_refused() -> None:
    with pytest.raises(DimensionContextError):
        dimension_context(["security"], {"security": {"decisions": -1}})


def test_the_kernel_does_not_enumerate_ids() -> None:
    """An id this module has never heard of must still work."""
    context = dimension_context(
        ["observability", "cost-efficiency"], {"observability": {"decisions": 2}}
    )
    assert context.totals == {
        "planned": 2,
        "verified": 1,
        "unverified": 1,
        "unplanned": 0,
        "decisions": 2,
    }


def test_format_names_what_was_not_checked() -> None:
    context = dimension_context(
        ["correctness", "security", "ui-ux"],
        {"correctness": {"decisions": 4}, "ui-ux": {"decisions": 1}, "tech-debt": {"decisions": 2}},
    )
    assert format_dimension_context(context) == (
        "2/3 dimensions verified, 1 unverified (security), "
        "1 unplanned (tech-debt), 7 decisions"
    )


def test_format_when_nothing_was_verified() -> None:
    line = format_dimension_context(dimension_context(["correctness", "security"], {}))
    assert line == "0/2 dimensions verified, 2 unverified (correctness, security), 0 decisions"


def test_format_single_decision_is_singular() -> None:
    line = format_dimension_context(dimension_context(["security"], {"security": {"decisions": 1}}))
    assert line == "1/1 dimensions verified, 1 decision"


def test_from_evidence_counts_packs_per_dimension() -> None:
    packs = [
        {"dimension": "security", "operationId": "op_1"},
        {"dimension": "security", "operationId": "op_2"},
        {"dimension": "correctness", "operationId": "op_3"},
        # No dimension key: contributes to nothing rather than being guessed at.
        {"operationId": "op_4"},
    ]
    context = dimension_context_from_evidence(["security", "correctness", "ui-ux"], packs)

    assert context.totals == {
        "planned": 3,
        "verified": 2,
        "unverified": 1,
        "unplanned": 0,
        "decisions": 3,
    }
    security = next(entry for entry in context.planned if entry.id == "security")
    assert security.evidence.operation_ids == ("op_1", "op_2")
    assert security.evidence.decisions == 2


def test_from_evidence_reports_an_unplanned_pack() -> None:
    context = dimension_context_from_evidence(
        ["security"], [{"dimension": "concurrency", "operationId": "op_1"}]
    )
    assert [entry.id for entry in context.unplanned] == ["concurrency"]
    assert context.totals["unplanned"] == 1


def test_operation_ids_are_capped() -> None:
    packs = [{"dimension": "security", "operationId": f"op_{index}"} for index in range(80)]
    context = dimension_context_from_evidence(["security"], packs)
    entry = context.planned[0]
    assert entry.evidence.decisions == 80
    # The count is the fact; the ids are a convenience back-pointer and stay bounded.
    assert entry.evidence.operation_ids is not None
    assert len(entry.evidence.operation_ids) == 32
    assert entry.evidence.operation_ids[-1] == "op_79"


def test_evidence_keeps_valid_operation_ids() -> None:
    evidence = DimensionEvidence.from_json({"decisions": 2, "operationIds": ["op_1", "op_2"]})
    assert evidence.decisions == 2
    assert evidence.operation_ids == ("op_1", "op_2")


def test_evidence_counts_default_to_zero() -> None:
    assert DimensionEvidence.from_json({}).decisions == 0


def test_evidence_accepts_numeric_strings() -> None:
    """Ledger entries survive a JSON round-trip as strings all too often."""
    assert DimensionEvidence.from_json({"decisions": "3"}).decisions == 3


def test_evidence_rejects_a_boolean_decision_count() -> None:
    """``bool`` subclasses ``int``: ``decisions: true`` used to become 1."""
    with pytest.raises(DimensionContextError) as excinfo:
        DimensionEvidence.from_json({"decisions": True})
    assert excinfo.value.code == DIMENSION_E_EMPTY


@pytest.mark.parametrize("junk", ["many", 1.5, None, ["1"]])
def test_evidence_rejects_a_non_numeric_decision_count(junk: object) -> None:
    with pytest.raises(DimensionContextError):
        DimensionEvidence.from_json({"decisions": junk})


def test_evidence_rejects_a_bare_operation_id_string() -> None:
    """``"op_1"`` is not a one-element list — it used to become ('o', 'p', ...)."""
    with pytest.raises(DimensionContextError):
        DimensionEvidence.from_json({"decisions": 1, "operationIds": "op_1"})


def test_evidence_rejects_non_string_operation_ids() -> None:
    with pytest.raises(DimensionContextError):
        DimensionEvidence.from_json({"decisions": 1, "operationIds": [1, 2]})
