"""The Python half reproduces the kernel's evidence -> decision transcription.

The oracle is the published corpus (`tests/kernel_fixtures/evidence-decision.ok-01.json`,
pinned by sha256 in `manifest.json` and checked against both the kernel checkout and the
npm artifact by `test_kernel_fixtures.py`). Nothing here re-derives the rules from the
TypeScript: the fixture states them in prose and eight cases, four of which are real engine
packs read off disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.iterate.evidence_transcription import (
    decision_outcome_from_evidence,
    decision_summary_from_evidence,
)

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"
FIXTURE_NAME = "evidence-decision.ok-01.json"


def _fixture() -> dict[str, Any]:
    path = CORPUS / FIXTURE_NAME
    assert path.is_file(), (
        f"{FIXTURE_NAME} is missing from the vendored corpus. Restore it from "
        "iterate-kernel's fixtures/ and re-pin its sha256 in manifest.json — skipping "
        "this comparison is not a remedy."
    )
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    return parsed


FIXTURE = _fixture()


@pytest.mark.parametrize(
    "case",
    FIXTURE["cases"],
    ids=[str(case["name"]) for case in FIXTURE["cases"]],
)
def test_a_case_transcribes_exactly_as_the_corpus_says(case: dict[str, Any]) -> None:
    assert decision_outcome_from_evidence(case["input"]) == case["expectedOutcome"]
    assert decision_summary_from_evidence(case["input"]) == case["expectedSummary"]


def test_the_corpus_still_covers_every_documented_branch() -> None:
    """A corpus that quietly shrinks is not a contract.

    Mirrors the guard on the kernel's own side, so the two repos cannot drift by one of
    them dropping the harder cases and reporting green anyway.
    """
    outcomes = {str(case["expectedOutcome"]) for case in FIXTURE["cases"]}
    for outcome in ("blocked", "pass", "fail", "inconclusive"):
        assert outcome in outcomes, f"no case exercises the {outcome} branch"

    origins = [str(case["origin"]) for case in FIXTURE["cases"]]
    assert any("weak" in origin for origin in origins), "the weak-attribution downgrade is not pinned"
    assert any("contaminated" in origin for origin in origins), "the contamination downgrade is not pinned"
    assert any(
        case["input"].get("assertion") and case["input"].get("diagnosis") for case in FIXTURE["cases"]
    ), "the assertion-outranks-diagnosis order is not pinned"
    assert any(
        int((case["input"].get("circuitBreaker") or {}).get("level") or 0) >= 3 for case in FIXTURE["cases"]
    ), "the breaker-first rule is not pinned"


def test_the_provenance_of_every_case_is_stated() -> None:
    """Real packs name an engine operation; derived cases name the field they changed."""
    import re

    for case in FIXTURE["cases"]:
        origin = str(case["origin"])
        if origin == "real":
            assert re.fullmatch(r"op_[0-9A-Z]{26}", str(case["sourceOperationId"])), case["name"]
            assert case["input"]["operationId"] == case["sourceOperationId"], case["name"]
        else:
            assert origin.startswith("derived: "), f"{case['name']} must say what was derived"
            assert case["input"].get("operationId"), f"{case['name']} lost its operation id"


def test_a_pack_with_nothing_recorded_is_inconclusive_and_still_describes_itself() -> None:
    """The rules' own default case, without leaning on a fixture entry for it."""
    pack: dict[str, Any] = {
        "attribution": {"level": "strong", "contaminated": False},
        "circuitBreaker": {"level": 0},
        "assertion": None,
        "diagnosis": None,
    }
    assert decision_outcome_from_evidence(pack) == "inconclusive"
    assert decision_summary_from_evidence(pack) == "attribution strong"


def test_absent_keys_and_explicit_nulls_are_the_same_fact() -> None:
    absent: dict[str, Any] = {"attribution": {"level": "strong"}, "circuitBreaker": {"level": 0}}
    explicit: dict[str, Any] = {
        "attribution": {"level": "strong"},
        "circuitBreaker": {"level": 0},
        "assertion": None,
        "diagnosis": None,
    }
    assert decision_outcome_from_evidence(absent) == decision_outcome_from_evidence(explicit)
    assert decision_outcome_from_evidence(absent) == "inconclusive"


def test_a_downgrade_never_flips_a_fail_into_something_kinder() -> None:
    pack: dict[str, Any] = {
        "assertion": {"property": "enabled", "passed": False, "expected": True, "actual": False},
        "attribution": {"level": "weak", "contaminated": True},
        "circuitBreaker": {"level": 1, "reason": "two resets"},
    }
    # The pass-only downgrade cannot rescue a failing assertion, and the breaker note
    # still has to be rendered.
    assert decision_outcome_from_evidence(pack) == "fail"
    assert decision_summary_from_evidence(pack).startswith("assertion enabled failed: expected true, actual false")
    assert "circuitBreaker level 1 (two resets)" in decision_summary_from_evidence(pack)
    assert decision_summary_from_evidence(pack).endswith("attribution weak, contaminated")
