"""The Python half of the kernel's invariant-report contract, checked against its fixture.

Two separate claims, and both have to be falsifiable:

* the digest must equal the one the published TypeScript kernel computes for the same
  report (cross-implementation parity — the only reason a second implementation is
  allowed to exist), and
* the report this harness *produces* from its own runner must be a report the contract
  accepts, with the passing checks named rather than invented.

The fixture is the vendored half of the contract in ``tests/kernel_fixtures`` (pinned by
``test_kernel_fixtures.py``), or a live kernel checkout when ``KERNEL_FIXTURES_DIR``
points at one. It is never skipped: a cross-implementation check that does not run is the
one outcome this file must not produce, because it would report green while proving that
the two halves agree on nothing.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.defensive.invariants import InvariantReport as DefensiveReport
from iterate_harness.defensive.invariants import InvariantViolation
from iterate_harness.iterate.canonical_json import (
    CANONICAL_JSON_E_KEY_ORDER,
    CANONICAL_JSON_E_KEY_TYPE,
    CANONICAL_JSON_E_VALUE,
    CanonicalJSONError,
    canonical_json,
)
from iterate_harness.iterate.invariant_report import (
    CHECK_KINDS,
    DIGEST_PATTERN,
    INVARIANT_REPORT_E_CHECK,
    INVARIANT_REPORT_E_CHECKS,
    INVARIANT_REPORT_E_CONFIGURED,
    INVARIANT_REPORT_E_OUTCOME,
    INVARIANT_REPORT_E_TARGETS,
    INVARIANT_REPORT_E_UNMEASURED,
    INVARIANT_REPORT_SCHEMA_VERSION,
    OUTCOMES,
    REVERIFICATION_SCHEMA_VERSION,
    TRIGGER_KIND,
    InvariantCheck,
    InvariantReport,
    InvariantReportError,
    failing_checks,
    from_defensive_report,
    invariant_report_digest,
    invariant_report_from_json,
    re_verification_request,
    re_verification_request_from_targets,
    validate_report,
)

#: The vendored half of the kernel contract, pinned by ``test_kernel_fixtures.py``.
KERNEL_CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"


def _fixture() -> dict[str, Any]:
    """Locate and read the contract. A missing corpus is a failure, never a skip."""
    name = "invariant-report.ok-01.json"
    override = os.environ.get("KERNEL_FIXTURES_DIR")
    candidates = [Path(override) / name] if override else []
    candidates.append(KERNEL_CORPUS / name)
    for candidate in candidates:
        if candidate.is_file():
            parsed: dict[str, Any] = json.loads(candidate.read_text(encoding="utf-8"))
            return parsed
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise AssertionError(
        f"kernel fixture {name} is not present (looked in: {searched}). Restore it from "
        "iterate-kernel's fixtures/ and re-pin its sha256 in "
        "tests/kernel_fixtures/manifest.json — skipping this cross-implementation check "
        "is not a remedy."
    )


#: Read at import, because parametrisation needs the cases before any test body runs.
FIXTURE = _fixture()

#: The config the fixture's provenance paragraph says it was captured from: the
#: ``invariants.ensure`` list plus a file the run does not have, and the two real
#: ``<module>:<command>`` entries — the second being the metacharacter case the runner
#: refuses.
CONFIGURED_ENSURE = ["README.md", "iterate.config.yaml"]
CONFIGURED_COMMANDS: dict[str, list[str]] = {
    "typescript": ["npm run compile"],
    "python": ["ruff check src/ ; rm -rf /"],
}


def _every_case(name: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = list(FIXTURE[name])
    assert cases, f"the fixture's {name} array is empty; nothing was pinned"
    return cases


def _case_id(case: dict[str, Any]) -> str:
    return str(case["name"])


def _defensive_report_from_fixture() -> DefensiveReport:
    """The runner's own dataclass, carrying the fixture's captured labels and details.

    Built from the real types rather than a stand-in shape: the producer's input is what
    ``check_invariants`` returns — a verdict, a count, and violations named by the runner's
    own ``ensure:<path>`` / ``<module>:<command>`` label formats.
    """
    entries: list[dict[str, Any]] = list(FIXTURE["input"]["checks"])
    failing = [entry for entry in entries if entry["ok"] is False]
    for entry in failing:
        assert "detail" in entry, (
            "the runner always names a detail for a violation; a fixture failing check "
            "without one is a different contract"
        )
    violations = [
        InvariantViolation(
            kind=str(entry["kind"]), label=str(entry["name"]), detail=str(entry["detail"])
        )
        for entry in failing
    ]
    return DefensiveReport(
        passed=False, checks_run=len(entries), violations=violations
    )


# --------------------------------------------------------------------------- parity ---


def test_the_published_digest_matches_the_kernel_implementation() -> None:
    """The whole reason this module exists: the same bytes, the same ``iv_`` digest."""
    report = invariant_report_from_json(FIXTURE["input"])
    assert report.to_json() == FIXTURE["input"], (
        "reading then re-emitting must be lossless: absent optionals stay absent"
    )
    assert invariant_report_digest(report) == FIXTURE["expectedDigest"]
    assert failing_checks(report) == FIXTURE["expectedFailingChecks"]
    assert FIXTURE["expectedDigest"].startswith("iv_")
    assert DIGEST_PATTERN.match(FIXTURE["expectedDigest"])


def test_pass_and_error_reports_digest_as_published() -> None:
    assert (
        invariant_report_digest(invariant_report_from_json(FIXTURE["passInput"]))
        == FIXTURE["passDigest"]
    )
    assert (
        invariant_report_digest(invariant_report_from_json(FIXTURE["errorInput"]))
        == FIXTURE["errorDigest"]
    )


def test_the_digest_is_over_the_canonical_form_not_the_serialisation() -> None:
    """Re-ordering keys is the same report; changing a check's result is not."""
    shuffled = {key: value for key, value in reversed(list(FIXTURE["input"].items()))}
    assert invariant_report_digest(invariant_report_from_json(shuffled)) == (
        FIXTURE["expectedDigest"]
    )
    assert canonical_json(shuffled) == canonical_json(FIXTURE["input"])

    cleared = copy.deepcopy(FIXTURE["input"])
    cleared["outcome"] = "pass"
    for entry in cleared["checks"]:
        entry["ok"] = True
        entry.pop("detail", None)
    assert invariant_report_digest(invariant_report_from_json(cleared)) != (
        FIXTURE["expectedDigest"]
    ), "a run that claims everything held cannot cite the digest of the run that did not"

    # One character of one detail is a different run, and must digest differently.
    reworded = copy.deepcopy(FIXTURE["input"])
    reworded["checks"][1]["detail"] = str(reworded["checks"][1]["detail"]) + " "
    assert invariant_report_digest(invariant_report_from_json(reworded)) != (
        FIXTURE["expectedDigest"]
    )


def test_the_published_request_object_is_built_field_for_field() -> None:
    """Byte-for-byte: the fixture's ``request`` must equal what we build, as dicts."""
    report = invariant_report_from_json(FIXTURE["input"])
    expected: dict[str, Any] = FIXTURE["request"]
    targets: dict[str, Any] = expected["targets"]
    built = re_verification_request(
        report,
        recipe_steps=targets["recipeSteps"],
        dimensions=targets["dimensions"],
    )
    assert built == expected
    assert built is not None
    assert built["schemaVersion"] == REVERIFICATION_SCHEMA_VERSION
    assert built["trigger"] == {
        "kind": TRIGGER_KIND,
        "reportDigest": FIXTURE["expectedDigest"],
    }


@pytest.mark.parametrize("case", _every_case("cases"), ids=_case_id)
def test_every_pinned_case_is_accepted_or_refused_as_stated(case: dict[str, Any]) -> None:
    expect = case["expect"]
    assert expect in ("accepted", "refused"), f"unknown expectation {expect!r}"
    if expect == "refused":
        with pytest.raises(InvariantReportError) as caught:
            invariant_report_from_json(case["input"])
        path = str(case.get("path", ""))
        offending = str(case.get("messageIncludes")) if not path else path
        assert offending in caught.value.message, (
            f"the refusal must name the offending field {offending!r}, got "
            f"{caught.value.message!r}"
        )
        assert caught.value.remedy.strip(), f"{case['name']}: a refusal needs a remedy"
        return
    report = invariant_report_from_json(case["input"])
    assert report.to_json() == case["input"]
    if "digest" in case:
        assert invariant_report_digest(report) == case["digest"]
    if "failingChecks" in case:
        assert failing_checks(report) == case["failingChecks"]


@pytest.mark.parametrize(
    "case", _every_case("reverificationCases"), ids=_case_id
)
def test_every_pinned_reverification_case_is_accepted_or_refused(
    case: dict[str, Any],
) -> None:
    expect = case["expect"]
    assert expect in ("accepted", "refused"), f"unknown expectation {expect!r}"
    report = invariant_report_from_json(FIXTURE["input"])
    if expect == "refused":
        with pytest.raises(InvariantReportError) as caught:
            re_verification_request_from_targets(report, case["targets"])
        assert caught.value.code == INVARIANT_REPORT_E_TARGETS
        assert "targets" in caught.value.message
        assert caught.value.remedy.strip()
        return
    request = re_verification_request_from_targets(report, case["targets"])
    assert request is not None
    assert request == {
        "schemaVersion": REVERIFICATION_SCHEMA_VERSION,
        "trigger": {
            "kind": TRIGGER_KIND,
            "reportDigest": invariant_report_digest(report),
        },
        "targets": case["targets"],
    }, (
        "targets are carried verbatim, and the trigger cites this report's own digest: "
        "a request that digests differently from the report it answers is not a citation"
    )


def test_the_vocabularies_cover_every_value_the_fixture_uses() -> None:
    """The constants are stated from the fixture, so check them against its data."""
    seen_outcomes: set[str] = set()
    seen_kinds: set[str] = set()
    for blob in json.dumps(FIXTURE).split('"'):
        if blob in OUTCOMES:
            seen_outcomes.add(blob)
        if blob in CHECK_KINDS:
            seen_kinds.add(blob)
    assert seen_outcomes == set(OUTCOMES)
    assert seen_kinds == set(CHECK_KINDS)
    assert FIXTURE["input"]["schemaVersion"] == INVARIANT_REPORT_SCHEMA_VERSION


# ---------------------------------------------------------------- negative controls ---


def test_a_pass_claim_over_a_failed_check_is_refused() -> None:
    """The contradiction the fixture makes the centerpiece, reached both ways."""
    poisoned = copy.deepcopy(FIXTURE["passInput"])
    assert poisoned["outcome"] == "pass"
    poisoned["checks"][0]["ok"] = False
    poisoned["checks"][0]["detail"] = "missing file: README.md"
    with pytest.raises(InvariantReportError) as caught:
        invariant_report_from_json(poisoned)
    assert caught.value.code == INVARIANT_REPORT_E_OUTCOME
    assert "outcome" in caught.value.message
    assert str(poisoned["checks"][0]["name"]) in caught.value.message

    # The same report assembled straight into the dataclasses: no reader gets past this.
    hand_built = InvariantReport(
        outcome="pass",
        checks=[
            InvariantCheck(
                name="ensure:README.md", kind="ensure", ok=False, detail="missing file"
            )
        ],
    )
    with pytest.raises(InvariantReportError) as caught:
        validate_report(hand_built)
    assert caught.value.code == INVARIANT_REPORT_E_OUTCOME
    # And it cannot be digested either: a citation to a refused report is a lie downstream.
    with pytest.raises(InvariantReportError):
        invariant_report_digest(hand_built)


def test_a_fail_claim_with_every_check_ok_is_refused() -> None:
    flipped = copy.deepcopy(FIXTURE["passInput"])
    assert flipped["outcome"] == "pass"
    flipped["outcome"] = "fail"
    with pytest.raises(InvariantReportError) as caught:
        invariant_report_from_json(flipped)
    assert caught.value.code == INVARIANT_REPORT_E_OUTCOME
    assert "outcome" in caught.value.message


def test_an_error_report_produces_no_request_and_names_the_unmeasured_state() -> None:
    report = invariant_report_from_json(FIXTURE["errorInput"])
    assert failing_checks(report) == ["python:pytest tests/ -x -q --timeout=60"]
    with pytest.raises(InvariantReportError) as caught:
        re_verification_request(report, dimensions=["spec-compliance"])
    assert caught.value.code == INVARIANT_REPORT_E_UNMEASURED
    assert "error" in caught.value.message
    assert caught.value.remedy.strip()
    # error is a state the contract carries; only the request is refused.
    assert DIGEST_PATTERN.match(invariant_report_digest(report))


def test_a_passing_report_yields_exactly_none() -> None:
    """Not an empty request, not a request with empty targets: nothing to re-verify."""
    report = invariant_report_from_json(FIXTURE["passInput"])
    assert re_verification_request(report, dimensions=["spec-compliance"]) is None
    assert re_verification_request(report) is None
    assert re_verification_request_from_targets(report, {}) is None


def test_an_empty_target_set_is_refused_rather_than_sent() -> None:
    report = invariant_report_from_json(FIXTURE["input"])
    with pytest.raises(InvariantReportError) as caught:
        re_verification_request(report)
    assert caught.value.code == INVARIANT_REPORT_E_TARGETS
    assert "targets" in caught.value.message


def test_check_bounds_are_enforced_on_a_hand_built_report() -> None:
    """Rules the fixture only exercises through JSON, gated at the validator itself."""
    too_many = InvariantReport(
        outcome="pass",
        checks=tuple(
            InvariantCheck(name=f"ensure:file{index}", kind="ensure", ok=True)
            for index in range(257)
        ),
    )
    with pytest.raises(InvariantReportError) as caught:
        validate_report(too_many)
    assert caught.value.code == INVARIANT_REPORT_E_CHECKS
    assert "checks" in caught.value.message

    over_long_name = InvariantReport(
        outcome="pass", checks=[InvariantCheck(name="e" * 257, kind="ensure", ok=True)]
    )
    with pytest.raises(InvariantReportError) as caught:
        validate_report(over_long_name)
    assert caught.value.code == INVARIANT_REPORT_E_CHECK
    assert "checks.0.name" in caught.value.message

    over_long_detail = InvariantReport(
        outcome="fail",
        checks=[
            InvariantCheck(name="ensure:README.md", kind="ensure", ok=False, detail="x" * 4097)
        ],
    )
    with pytest.raises(InvariantReportError) as caught:
        validate_report(over_long_detail)
    assert caught.value.code == INVARIANT_REPORT_E_CHECK
    assert "checks.0.detail" in caught.value.message


def test_round_and_duration_limits() -> None:
    check = InvariantCheck(name="ensure:README.md", kind="ensure", ok=True)
    with pytest.raises(InvariantReportError) as caught:
        validate_report(InvariantReport(outcome="pass", checks=[check], round=0))
    assert "round" in caught.value.message
    with pytest.raises(InvariantReportError) as caught:
        validate_report(InvariantReport(outcome="pass", checks=[check], duration_ms=-1))
    assert "durationMs" in caught.value.message
    # Absent is a different fact from zero, and both bounds are legal.
    validate_report(InvariantReport(outcome="pass", checks=[check], round=1, duration_ms=0))


def test_a_non_integer_round_is_refused_as_a_field_not_a_crash() -> None:
    payload = copy.deepcopy(FIXTURE["passInput"])
    payload["round"] = "3"
    with pytest.raises(InvariantReportError) as caught:
        invariant_report_from_json(payload)
    assert "round" in caught.value.message
    payload["round"] = 3.5
    with pytest.raises(InvariantReportError):
        invariant_report_from_json(payload)
    payload["round"] = True
    with pytest.raises(InvariantReportError):
        invariant_report_from_json(payload)
    # A null is not an omission: both would otherwise read into one report and one digest.
    payload["round"] = None
    with pytest.raises(InvariantReportError) as caught:
        invariant_report_from_json(payload)
    assert "round" in caught.value.message


def test_a_null_detail_is_refused_rather_than_read_as_absent() -> None:
    payload = copy.deepcopy(FIXTURE["passInput"])
    payload["checks"][0]["detail"] = None
    with pytest.raises(InvariantReportError) as caught:
        invariant_report_from_json(payload)
    assert "checks.0.detail" in caught.value.message
    # Whereas the omitted form is the contract's own, and digests as published.
    assert "detail" not in invariant_report_from_json(FIXTURE["passInput"]).checks[0].to_json()


# ------------------------------------------------------------------ the real producer ---


def test_from_defensive_report_rebuilds_the_published_contract_exactly() -> None:
    """The runner's own output, transcribed into the fixture's bytes.

    ``check_invariants`` names only its violations, so the passing checks here come from
    what was configured — ``ensure:README.md`` is named because the config asked for it and
    no violation carries its label, not because the fixture happens to show it.
    """
    produced = from_defensive_report(
        CONFIGURED_ENSURE, CONFIGURED_COMMANDS, _defensive_report_from_fixture()
    )
    assert produced.outcome == "fail"
    assert [check.kind for check in produced.checks] == ["ensure", "ensure", "command", "refused"]
    # The producer emits exactly the fixture's checks list — same names, same kinds, same
    # order, same details, and the same three omissions for the checks that held.
    assert produced.to_json() == {
        "schemaVersion": INVARIANT_REPORT_SCHEMA_VERSION,
        "outcome": FIXTURE["input"]["outcome"],
        "checks": FIXTURE["input"]["checks"],
    }
    # ``round`` and ``durationMs`` are the fixture's, and the runner measures neither, so
    # the producer leaves them absent rather than inventing them. Attaching the caller's
    # own numbers must land on the published digest: that proves the bytes under it —
    # every check name, kind, verdict and detail — are the ones the kernel recorded.
    stamped = InvariantReport(
        outcome=produced.outcome,
        checks=produced.checks,
        round=FIXTURE["input"]["round"],
        duration_ms=FIXTURE["input"]["durationMs"],
    )
    assert stamped.to_json() == FIXTURE["input"]
    assert invariant_report_digest(stamped) == FIXTURE["expectedDigest"]
    assert failing_checks(stamped) == FIXTURE["expectedFailingChecks"]
    assert produced.round is None and produced.duration_ms is None


def test_a_refused_command_is_a_violation_so_the_report_fails_never_errors() -> None:
    """Fixture rule (5): a declined command is a finding, not an unmeasured check."""
    runner = DefensiveReport(
        passed=False,
        checks_run=2,
        violations=[
            InvariantViolation(
                kind="refused",
                label="python:ruff check src/ ; rm -rf /",
                detail="refused: unsafe command (shell metacharacter or empty)",
            )
        ],
    )
    report = from_defensive_report(
        ["README.md"], {"python": ["ruff check src/ ; rm -rf /"]}, runner
    )
    assert report.outcome == "fail"
    assert failing_checks(report) == ["python:ruff check src/ ; rm -rf /"]
    assert re_verification_request(report, dimensions=["security"]) is not None


def test_a_dry_run_reports_previewed_commands_as_unmeasured_not_ok() -> None:
    """A green invariant report from a run that never ran anything is the refusal here.

    The runner still evaluates ``ensure`` in a dry run, so that check keeps its measured
    result; only the commands it previewed become unmeasured, which lands the report on
    ``error`` — and an ``error`` report cannot buy a re-verification round.
    """
    runner = DefensiveReport(passed=True, checks_run=2, violations=[])
    previewed = from_defensive_report(
        ["README.md"], {"typescript": ["npm run compile"]}, runner, dry_run=True
    )
    by_name = {check.name: check for check in previewed.checks}
    assert previewed.outcome == "error"
    assert by_name["ensure:README.md"].ok is True
    assert by_name["typescript:npm run compile"].ok is False
    assert "not measured" in str(by_name["typescript:npm run compile"].detail)
    with pytest.raises(InvariantReportError) as caught:
        re_verification_request(previewed, dimensions=["security"])
    assert caught.value.code == INVARIANT_REPORT_E_UNMEASURED

    # The same config with the commands actually executed is a pass.
    assert from_defensive_report(
        ["README.md"], {"typescript": ["npm run compile"]}, runner
    ).outcome == "pass"


def test_a_runner_that_produced_no_verdict_is_error_and_never_a_borrowed_pass() -> None:
    report = from_defensive_report(
        CONFIGURED_ENSURE, {"typescript": ["npm run compile"]}, None
    )
    assert report.outcome == "error"
    assert failing_checks(report) == [
        check.name for check in report.checks
    ], "every check is unmeasured, so none may claim to have held"
    assert all("not measured" in str(check.detail) for check in report.checks)
    with pytest.raises(InvariantReportError) as caught:
        re_verification_request(report, dimensions=["security"])
    assert caught.value.code == INVARIANT_REPORT_E_UNMEASURED


def test_a_failed_verdict_naming_no_check_never_becomes_pass_or_an_invented_failure() -> None:
    """``passed=False`` with an empty violation list is unattributable, not clean."""
    runner = DefensiveReport(passed=False, checks_run=1, violations=[])
    report = from_defensive_report(["README.md"], {}, runner)
    assert report.outcome == "error"
    assert all(check.ok for check in report.checks)


def test_a_violation_for_an_unconfigured_label_is_published_not_dropped() -> None:
    """The one way a finding could reach a pass is by being left out of ``checks``."""
    runner = DefensiveReport(
        passed=False,
        checks_run=1,
        violations=[
            InvariantViolation(kind="ensure", label="ensure:drifted.file", detail="missing file")
        ],
    )
    report = from_defensive_report(["README.md"], {}, runner)
    assert report.outcome == "fail"
    assert failing_checks(report) == ["ensure:drifted.file"]


def test_nothing_configured_is_a_refusal_rather_than_an_empty_report() -> None:
    with pytest.raises(InvariantReportError) as caught:
        from_defensive_report([], {}, DefensiveReport(passed=True, checks_run=0, violations=[]))
    assert caught.value.code == INVARIANT_REPORT_E_CONFIGURED
    assert "checks" in caught.value.message

    # A configured label the contract cannot carry is refused, not truncated.
    with pytest.raises(InvariantReportError) as caught:
        from_defensive_report(["e" * 300], {}, DefensiveReport(True, 1, []))
    assert caught.value.code == INVARIANT_REPORT_E_CHECK
    assert "checks.0.name" in caught.value.message


# ------------------------------------------------------------------- canonical module ---


def test_the_canonical_form_is_the_one_the_fixture_describes() -> None:
    assert canonical_json({"b": 1, "a": [True, None, "x"]}) == '{"a":[true,null,"x"],"b":1}'
    assert canonical_json({"outcome": "fail", "checks": []}) == '{"checks":[],"outcome":"fail"}'
    # Keys sort by UTF-16 code unit, which for BMP text is plain code-point order.
    assert canonical_json({"Z": 1, "a": 2, "_": 3, "-": 4}) == '{"-":4,"Z":1,"_":3,"a":2}'


def test_a_non_bmp_key_is_refused_instead_of_ordered_differently() -> None:
    """Python and JavaScript disagree above the BMP, so neither may guess."""
    with pytest.raises(CanonicalJSONError) as caught:
        canonical_json({"\U0001f600": 1})
    assert caught.value.code == CANONICAL_JSON_E_KEY_ORDER
    assert caught.value.remedy.strip()


def test_values_with_no_agreed_form_are_refused_not_stringified() -> None:
    for bad in (1.5, {1, 2}, object()):
        with pytest.raises(CanonicalJSONError) as caught:
            canonical_json(bad)
        assert caught.value.code == CANONICAL_JSON_E_VALUE
    with pytest.raises(CanonicalJSONError) as caught:
        canonical_json({1: "one"})
    assert caught.value.code == CANONICAL_JSON_E_KEY_TYPE
