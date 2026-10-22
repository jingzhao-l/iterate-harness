"""Decide what the iterate loop does next from what one round states about itself.

A second, independent implementation of the contract published as
``iterate-kernel@0.1.5``, coded from ``tests/kernel_fixtures/convergence.ok-01.json``
(whose ``comment`` states the rules in prose) rather than from the TypeScript, so the two
agree because the rule holds and not because one file was copied into the other.
``test_convergence_policy.py`` pins this module to that fixture, and pins the shape of
the rule set too: a reason this policy can name but the fixture has no case for is a
test failure, not an untested branch.

What this module will not do:

- Verify anything. It is arithmetic over verdicts somebody else produced: a round says
  how its invariant checks went, how many findings it still holds and whether its
  evidence closed, and this only reads that and names the next action.
- Run the circuit breaker. ``breaker_level`` is the engine's own level for the run; 3 is
  the policy limit, which is why the accepted range stops there.
- Read ``iterate.config.yaml``. ``max_rounds`` arrives inside the state; the fixture's 7
  comes from that config file and this module neither knows nor cares.
- Decide on a state it did not validate. Every field is checked at construction, so a
  half-known round can never reach the precedence chain.

The distinction that is the whole reason the contract exists: ``unmeasured`` and
incomplete evidence must never read as ``converged``. "We could not look" and "we looked
and it holds" are different answers, and collapsing them is how a run claims success on
rounds that produced no verdict. Both of those states return ``verify``; only a ``pass``
with complete evidence, no open findings, an untripped breaker and budget left returns
``converged``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, NoReturn, TypeAlias

#: The contract this state is written against (the fixture's rule 1). Optional on the
#: state, but a present value must name exactly this version.
CONVERGENCE_SCHEMA_VERSION: Final = "iterate.convergence/0.1"

InvariantState: TypeAlias = Literal["pass", "fail", "error", "unmeasured"]
ConvergenceAction: TypeAlias = Literal["fix", "verify", "converged", "halt"]
ConvergenceReason: TypeAlias = Literal[
    "circuit-breaker-tripped",
    "round-budget-exhausted",
    "invariant-check-errored",
    "open-findings",
    "invariants-failed",
    "evidence-incomplete",
    "invariants-unmeasured",
    "all-gates-green",
]

#: The four verdicts a round may report about its invariant checks. ``error`` means the
#: check itself broke (no verdict), ``unmeasured`` means nobody ran it (no verdict
#: either); they halt and verify respectively precisely because neither is a verdict.
INVARIANT_STATES: Final[tuple[InvariantState, ...]] = ("pass", "fail", "error", "unmeasured")

#: Every reason the policy can name, in the fixture's precedence order. The test asserts
#: each one is exercised by a fixture case.
CONVERGENCE_REASONS: Final[tuple[ConvergenceReason, ...]] = (
    "circuit-breaker-tripped",
    "round-budget-exhausted",
    "invariant-check-errored",
    "open-findings",
    "invariants-failed",
    "evidence-incomplete",
    "invariants-unmeasured",
    "all-gates-green",
)

CONVERGENCE_ACTIONS: Final[tuple[ConvergenceAction, ...]] = ("fix", "verify", "converged", "halt")

MIN_ROUND: Final = 1
MIN_MAX_ROUNDS: Final = 1
MIN_OPEN_FINDINGS: Final = 0
MIN_BREAKER_LEVEL: Final = 0

#: Level at which the breaker is tripped, and the ceiling the state may carry. The
#: ceiling is not slack: 3 is the policy limit, so a state claiming 4 is reporting a
#: level the engine cannot have reached, and is refused rather than clamped.
BREAKER_TRIPPED_LEVEL: Final = 3
MAX_BREAKER_LEVEL: Final = 3

#: Wire spellings from the fixture, so a refusal names the field the caller wrote *and*
#: the attribute they passed.
CONTRACT_KEYS: Final[dict[str, str]] = {
    "round": "round",
    "max_rounds": "maxRounds",
    "invariant_state": "invariantState",
    "open_findings": "openFindings",
    "evidence_complete": "evidenceComplete",
    "breaker_level": "breakerLevel",
    "schema_version": "schemaVersion",
}


class ConvergencePolicyError(Exception):
    """A round that cannot be judged, with a code and a remedy the caller can act on."""

    def __init__(self, code: str, message: str, remedy: str) -> None:
        super().__init__(f"{code}: {message} ({remedy})")
        self.code = code
        self.message = message
        self.remedy = remedy


def _refuse(field: str, detail: str, remedy: str) -> NoReturn:
    """Refuse one field, naming it in both spellings a caller might have used."""
    key = CONTRACT_KEYS[field]
    named = field if key == field else f"{field} (contract key `{key}`)"
    raise ConvergencePolicyError(f"CONVERGENCE_E_{field.upper()}", f"{named} {detail}", remedy)


def _require_integer(field: str, value: int, *, minimum: int, maximum: int | None) -> None:
    # bool is an int to Python. A `True` where a count belongs is a caller bug that would
    # silently read as one finding or one round, so integers are integers here.
    if isinstance(value, bool) or not isinstance(value, int):
        _refuse(field, f"must be an integer, got {value!r}", "pass an int, not a bool or float")
    bounds = f"must be >= {minimum}" if maximum is None else f"must be in {minimum}..{maximum}"
    if value < minimum or (maximum is not None and value > maximum):
        _refuse(field, f"{bounds}, got {value}", "report the value the round actually has")


@dataclass(frozen=True)
class RoundState:
    """What one round says about itself. Construction is the validation gate.

    The signature is the whitelist: the contract carries no other keys, so a caller
    handing over an invented field is refused by the constructor rather than quietly
    accepted and ignored.
    """

    round: int
    max_rounds: int
    invariant_state: InvariantState
    open_findings: int
    evidence_complete: bool
    #: None means "this run has no circuit-breaker channel", which is NOT the same claim as
    #: 0 (an existing breaker reporting untripped). Rule 1 is skipped when absent.
    breaker_level: int | None = None
    schema_version: str | None = None

    def __post_init__(self) -> None:
        _require_integer("round", self.round, minimum=MIN_ROUND, maximum=None)
        _require_integer("max_rounds", self.max_rounds, minimum=MIN_MAX_ROUNDS, maximum=None)
        _require_integer(
            "open_findings",
            self.open_findings,
            minimum=MIN_OPEN_FINDINGS,
            maximum=None,
        )
        # The breaker's ceiling doubles as its trip level, so one range covers both.
        # Absence is legitimate (no breaker channel) and is validated by rule 1 skipping.
        if self.breaker_level is not None:
            _require_integer(
                "breaker_level",
                self.breaker_level,
                minimum=MIN_BREAKER_LEVEL,
                maximum=MAX_BREAKER_LEVEL,
            )
        if self.invariant_state not in INVARIANT_STATES:
            _refuse(
                "invariant_state",
                f"is {self.invariant_state!r}, outside the four verdicts a round may report",
                f"use one of: {', '.join(INVARIANT_STATES)}",
            )
        if not isinstance(self.evidence_complete, bool):
            _refuse(
                "evidence_complete",
                f"must be a bool, got {self.evidence_complete!r}",
                "say whether the round's evidence closed; an unknown is false, not missing",
            )
        if self.schema_version is not None and self.schema_version != CONVERGENCE_SCHEMA_VERSION:
            _refuse(
                "schema_version",
                f"is {self.schema_version!r}, not the version this module reads",
                f"tag the state {CONVERGENCE_SCHEMA_VERSION} or omit it",
            )


@dataclass(frozen=True)
class ConvergenceDecision:
    """The next action for the round that was judged, and the reason the rule gave.

    ``round`` and ``max_rounds`` are echoed unchanged from the state (the fixture's rule
    4), because a ledger line has to say which round it judged.
    """

    action: ConvergenceAction
    reason: ConvergenceReason
    round: int
    max_rounds: int


def _decide(
    state: RoundState,
    action: ConvergenceAction,
    reason: ConvergenceReason,
) -> ConvergenceDecision:
    """The rule's verdict, tagged with the round it judged (rule 4: echoed unchanged)."""
    return ConvergenceDecision(
        action=action, reason=reason, round=state.round, max_rounds=state.max_rounds
    )


def convergence_decision(state: RoundState) -> ConvergenceDecision:
    """What the loop does next for ``state``: first match wins, in this order.

    The order is the contract (the fixture's rules 2 and 3), not a preference: a limit
    the harness set on itself is not the app's failure, the budget is spent before the
    work is counted, and a broken check is the runner's problem rather than the loop's.
    Reading the chain top to bottom *is* reading the rule, so it stays one explicit chain
    even though each branch looks like the last one.
    """
    # 1. The breaker halts before anything is counted (level 3 is the policy limit).
    # An absent breaker is "no channel in this run", not a measured 0 — skipping the rule
    # and halting are different outcomes, so the check is explicit about the difference.
    if state.breaker_level is not None and state.breaker_level >= BREAKER_TRIPPED_LEVEL:
        return _decide(state, "halt", "circuit-breaker-tripped")
    # 2. Budget before work: a run cannot buy another round by re-reporting findings it
    #    already had, so `round >= max_rounds` ends it whatever else the round claims.
    if state.round >= state.max_rounds:
        return _decide(state, "halt", "round-budget-exhausted")
    # 3. An errored check yields no verdict; retrying it is the runner's job, not the
    #    loop's, so this halts instead of fixing or verifying on top of a blank.
    if state.invariant_state == "error":
        return _decide(state, "halt", "invariant-check-errored")
    # 4. Findings somebody recorded are work the loop owns.
    if state.open_findings > MIN_OPEN_FINDINGS:
        return _decide(state, "fix", "open-findings")
    # 5. Failed invariants with nothing open still need a fix round.
    if state.invariant_state == "fail":
        return _decide(state, "fix", "invariants-failed")
    # 6 and 7. Nothing failed below this line -- but nothing was *shown* either. These
    #    two are the reason the contract exists: unlooked evidence and unmeasured
    #    invariants return `verify`, never `converged`.
    if not state.evidence_complete:
        return _decide(state, "verify", "evidence-incomplete")
    if state.invariant_state == "unmeasured":
        return _decide(state, "verify", "invariants-unmeasured")
    # 8. Every gate actually looked and holds.
    return _decide(state, "converged", "all-gates-green")
