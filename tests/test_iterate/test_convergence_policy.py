"""The convergence policy, driven entirely by the kernel's published fixture.

Two claims have to be falsifiable here, and they are separate: every case in
``convergence.ok-01.json`` must produce the decision the file says (cross-language
parity, with the expected values read from the file and never written into this one),
and the *shape* of the rule set must still be the fixture's -- a reason this policy can
name but no case exercises is an untested branch, which is exactly how a precedence rule
rots.

The negative controls at the bottom are not in the fixture. They hold the two properties
the contract exists for: "we could not look" never reads as "we looked and it holds", and
a limit the harness set on itself halts before any of the round's own claims are counted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.iterate.convergence_policy import (
    CONVERGENCE_ACTIONS,
    CONVERGENCE_REASONS,
    CONVERGENCE_SCHEMA_VERSION,
    ConvergenceDecision,
    ConvergencePolicyError,
    RoundState,
    convergence_decision,
)

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"


def _convergence_fixture() -> dict[str, Any]:
    path = CORPUS / "convergence.ok-01.json"
    assert path.is_file(), (
        "convergence.ok-01.json is missing from the vendored corpus; restore it from "
        "iterate-kernel's fixtures/ and re-pin it in manifest.json"
    )
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), "the convergence fixture must be one JSON object"
    return {str(key): value for key, value in parsed.items()}


FIXTURE: dict[str, Any] = _convergence_fixture()
CASES: list[dict[str, Any]] = [dict(case) for case in FIXTURE["cases"]]

#: The contract's camelCase keys to this module's snake_case attributes. Spelled out
#: here rather than imported from the module, so a wrong mapping cannot agree with itself.
CONTRACT_TO_FIELD: dict[str, str] = {
    "round": "round",
    "maxRounds": "max_rounds",
    "invariantState": "invariant_state",
    "openFindings": "open_findings",
    "evidenceComplete": "evidence_complete",
    "breakerLevel": "breaker_level",
    "schemaVersion": "schema_version",
}


def _state(contract: dict[str, Any]) -> RoundState:
    """Build a ``RoundState`` from one fixture state object.

    Keys the contract does not define are passed through untranslated on purpose: the
    constructor's signature is the whitelist, so an invented key must be refused by the
    module, not quietly dropped by this helper.
    """
    kwargs: dict[str, Any] = {}
    for key, value in contract.items():
        kwargs[CONTRACT_TO_FIELD.get(key, key)] = value
    return RoundState(**kwargs)


def _decide(contract: dict[str, Any]) -> ConvergenceDecision:
    return convergence_decision(_state(contract))


def _camel_to_snake(name: str) -> str:
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in name)


def _names_field(message: str, field: str) -> bool:
    """Whether the refusal names the offending field.

    The fixture spells fields in camelCase, the dataclass in snake_case, and a TypeError
    from the constructor -- which is how a missing or invented key is refused -- can only
    ever spell it the Python way. Both spellings count as naming it; neither is inferred.
    """
    return field in message or _camel_to_snake(field) in message


def _decision_cases() -> list[dict[str, Any]]:
    return [case for case in CASES if not isinstance(case["expect"], str)]


@pytest.mark.parametrize("case", CASES, ids=[str(case["name"]) for case in CASES])
def test_fixture_case(case: dict[str, Any]) -> None:
    expect = case["expect"]

    if expect == "refused":
        with pytest.raises((ConvergencePolicyError, TypeError)) as caught:
            _decide(case["state"])
        error = caught.value
        message = str(error)
        if isinstance(error, ConvergencePolicyError):
            assert error.code.startswith("CONVERGENCE_E_")
            assert error.remedy, "a refusal must carry a remedy the caller can act on"
        path = str(case.get("path", ""))
        includes = str(case.get("messageIncludes", ""))
        assert path or includes, "the fixture refuses a state without saying where"
        if path:
            assert _names_field(message, path), f"{path} not named in: {message}"
        if includes:
            assert includes in message, f"{includes} not named in: {message}"
        return

    decision = _decide(case["state"])
    assert decision.action == expect["action"]
    assert decision.reason == expect["reason"]
    assert decision.round == expect["round"]
    assert decision.max_rounds == expect["maxRounds"]


def test_top_level_input_produces_the_top_level_decision() -> None:
    """The worked example at the head of the fixture, checked the same way."""
    decision = _decide(FIXTURE["input"])
    expected = FIXTURE["expectedDecision"]
    assert decision.action == expected["action"]
    assert decision.reason == expected["reason"]
    assert decision.round == expected["round"]
    assert decision.max_rounds == expected["maxRounds"]
    assert FIXTURE["input"]["schemaVersion"] == CONVERGENCE_SCHEMA_VERSION


def test_every_reason_the_policy_can_name_has_a_fixture_case() -> None:
    """A rule nobody exercises is a rule nobody can notice breaking.

    The precedence chain has eight links and the fixture pins eight; if either side
    drifts, this fails rather than leaving a branch dark.
    """
    exercised = {str(case["expect"]["reason"]) for case in _decision_cases()}
    untested = [reason for reason in CONVERGENCE_REASONS if reason not in exercised]
    assert not untested, f"no fixture case exercises these reasons: {untested}"
    unknown = sorted(exercised - set(CONVERGENCE_REASONS))
    assert not unknown, f"the fixture expects reasons this policy does not declare: {unknown}"


def test_every_action_the_policy_can_name_has_a_fixture_case() -> None:
    exercised = {str(case["expect"]["action"]) for case in _decision_cases()}
    untested = [action for action in CONVERGENCE_ACTIONS if action not in exercised]
    assert not untested, f"no fixture case exercises these actions: {untested}"


def test_every_refused_case_names_a_field_the_policy_validates() -> None:
    validated = set(CONTRACT_TO_FIELD.values())
    for case in CASES:
        if case["expect"] != "refused":
            continue
        path = str(case.get("path", ""))
        if path:
            assert _camel_to_snake(path) in validated, f"{case['name']} points at an unknown field"


def _round(**overrides: Any) -> RoundState:
    """A round that states nothing is wrong, with the caller's overrides applied."""
    fields: dict[str, Any] = {
        "round": 2,
        "max_rounds": 7,
        "invariant_state": "pass",
        "open_findings": 0,
        "evidence_complete": True,
        "breaker_level": 0,
    }
    fields.update(overrides)
    return RoundState(**fields)


#: One row per link of the fixture's rule 2 chain, except the last: each state fires the
#: row's rule *and* every rule below it, so the verdict names the rule that is higher in
#: the chain. The fixture pins some of these pairs and not others (it has no case with
#: open findings and incomplete evidence, for instance); this row table is what stops two
#: neighbouring links from being swapped without a test going red.
_BEATS_EVERYTHING_BELOW: list[tuple[RoundState, str, str, str]] = [
    (
        _round(
            breaker_level=3,
            round=7,
            invariant_state="error",
            open_findings=4,
            evidence_complete=False,
        ),
        "breaker",
        "halt",
        "circuit-breaker-tripped",
    ),
    (
        _round(round=7, invariant_state="error", open_findings=4, evidence_complete=False),
        "budget",
        "halt",
        "round-budget-exhausted",
    ),
    (
        _round(invariant_state="error", open_findings=4, evidence_complete=False),
        "errored-check",
        "halt",
        "invariant-check-errored",
    ),
    (
        _round(invariant_state="fail", open_findings=4, evidence_complete=False),
        "open-findings",
        "fix",
        "open-findings",
    ),
    (
        _round(invariant_state="fail", evidence_complete=False),
        "failed-invariants",
        "fix",
        "invariants-failed",
    ),
    (
        _round(invariant_state="unmeasured", evidence_complete=False),
        "incomplete-evidence",
        "verify",
        "evidence-incomplete",
    ),
]


@pytest.mark.parametrize(
    ("state", "rule", "action", "reason"),
    _BEATS_EVERYTHING_BELOW,
    ids=[row[1] for row in _BEATS_EVERYTHING_BELOW],
)
def test_each_rule_in_the_chain_beats_every_rule_below_it(
    state: RoundState,
    rule: str,
    action: str,
    reason: str,
) -> None:
    """The chain itself, not only its individual links.

    The fixture states its precedence as an order, so an implementation that answers the
    right question in the wrong sequence is wrong even though every reason string it can
    emit still appears in a case.
    """
    decision = convergence_decision(state)
    assert decision.action == action, f"{rule} should win"
    assert decision.reason == reason, f"{rule} should win"


def test_a_state_where_nothing_failed_but_nothing_was_shown_never_converges() -> None:
    """The two `verify` links together: neither may fall through to `all-gates-green`."""
    for state in (
        _round(evidence_complete=False),
        _round(invariant_state="unmeasured"),
        _round(invariant_state="unmeasured", evidence_complete=False),
    ):
        decision = convergence_decision(state)
        assert decision.action == "verify"
        assert decision.reason in ("evidence-incomplete", "invariants-unmeasured")


def test_unmeasured_invariants_with_complete_evidence_are_verify_and_never_converged() -> None:
    """Negative control: the collapse this contract exists to prevent.

    An unmeasured check is not a passing one. If this ever returns ``converged``, a run
    reports success on a round that produced no verdict at all.
    """
    decision = convergence_decision(
        RoundState(
            round=2,
            max_rounds=7,
            invariant_state="unmeasured",
            open_findings=0,
            evidence_complete=True,
            breaker_level=0,
        )
    )
    observed = (decision.action, decision.reason)
    assert observed != ("converged", "all-gates-green"), (
        "an unmeasured check is not a passing one; collapsing the two is the defect "
        "this contract exists to prevent"
    )
    assert observed == ("verify", "invariants-unmeasured")


def test_a_tripped_breaker_halts_an_otherwise_perfect_round() -> None:
    """Negative control: the breaker is counted before the round's own claims."""
    decision = convergence_decision(
        RoundState(
            round=1,
            max_rounds=7,
            invariant_state="pass",
            open_findings=0,
            evidence_complete=True,
            breaker_level=3,
        )
    )
    assert decision.action == "halt"
    assert decision.reason == "circuit-breaker-tripped"
    assert decision.round == 1
    assert decision.max_rounds == 7


def test_a_zero_round_is_refused() -> None:
    """Negative control: rounds are 1-based, and a bad state never reaches the chain."""
    with pytest.raises(ConvergencePolicyError) as caught:
        RoundState(
            round=0,
            max_rounds=7,
            invariant_state="pass",
            open_findings=0,
            evidence_complete=True,
            breaker_level=0,
        )
    assert caught.value.code == "CONVERGENCE_E_ROUND"
    assert "round" in caught.value.message
    assert caught.value.remedy


def test_a_zero_round_budget_is_refused() -> None:
    with pytest.raises(ConvergencePolicyError) as caught:
        RoundState(
            round=1,
            max_rounds=0,
            invariant_state="pass",
            open_findings=0,
            evidence_complete=True,
            breaker_level=0,
        )
    assert caught.value.code == "CONVERGENCE_E_MAX_ROUNDS"
    assert "maxRounds" in str(caught.value)


def test_a_foreign_schema_version_is_refused_and_an_absent_one_is_not() -> None:
    for version in ("iterate.convergence/0.2", "iterate.run-plan/0.1"):
        with pytest.raises(ConvergencePolicyError) as caught:
            _decide({**FIXTURE["input"], "schemaVersion": version})
        assert caught.value.code == "CONVERGENCE_E_SCHEMA_VERSION"
        assert CONVERGENCE_SCHEMA_VERSION in caught.value.remedy

    untagged = {key: value for key, value in FIXTURE["input"].items() if key != "schemaVersion"}
    assert _decide(untagged).reason == "all-gates-green"


def test_a_value_that_is_not_an_integer_is_refused_where_a_count_belongs() -> None:
    """`True` is an int to Python: as findings it would read as one, as a level as 1."""
    a_bool: Any = True
    a_string: Any = "3"
    base: dict[str, Any] = {
        "round": 1,
        "max_rounds": 7,
        "invariant_state": "pass",
        "open_findings": 0,
        "evidence_complete": True,
        "breaker_level": 0,
    }
    with pytest.raises(ConvergencePolicyError) as caught:
        RoundState(**{**base, "open_findings": a_bool})
    assert caught.value.code == "CONVERGENCE_E_OPEN_FINDINGS"

    with pytest.raises(ConvergencePolicyError) as caught:
        RoundState(**{**base, "breaker_level": a_string})
    assert caught.value.code == "CONVERGENCE_E_BREAKER_LEVEL"

    with pytest.raises(ConvergencePolicyError) as caught:
        RoundState(**{**base, "evidence_complete": a_string})
    assert caught.value.code == "CONVERGENCE_E_EVIDENCE_COMPLETE"


def test_an_invented_key_is_refused_not_ignored() -> None:
    with pytest.raises(TypeError) as caught:
        _state({**FIXTURE["input"], "confidence": 0.9})
    assert "confidence" in str(caught.value)


def test_the_decision_echoes_the_round_it_judged() -> None:
    """Rule 4: a ledger line has to say which round was judged, unchanged."""
    judged = [FIXTURE["input"], *[case["state"] for case in _decision_cases()]]
    for contract in judged:
        decision = _decide(contract)
        assert decision.round == contract["round"]
        assert decision.max_rounds == contract["maxRounds"]
