"""Who is allowed to say `invariant_state`, and what "nobody looked" must look like.

The convergence contract reads this field to decide whether a round may stop, so the two ways
to break it are both covered here: producing a verdict nobody measured (the empty report that
defaults to `passed=True` is the real shape of that bug — it is the dataclass default, not a
lie someone wrote), and a consumer inventing a verdict when the key is absent.
"""

from __future__ import annotations

from iterate_harness.defensive.invariants import InvariantReport, InvariantViolation
from iterate_harness.iterate.invariant_state import (
    INVARIANT_STATE_VALUES,
    invariant_state_from_report,
    read_invariant_state,
)
from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY, _read_state_unsafe


def test_no_report_is_absent_not_a_verdict() -> None:
    assert invariant_state_from_report(None) is None


def test_an_untouched_report_is_unmeasured_not_pass() -> None:
    """`InvariantReport()` defaults to passed=True; that must not read as a green gate."""
    assert invariant_state_from_report(InvariantReport()) == "unmeasured"
    assert invariant_state_from_report(InvariantReport(checks_run=0, violations=[])) == "unmeasured"


def test_measured_reports_map_onto_the_three_verdicts() -> None:
    assert invariant_state_from_report(InvariantReport(passed=True, checks_run=2)) == "pass"
    violated = InvariantReport(
        passed=False, checks_run=2,
        violations=[InvariantViolation("command", "pytest -q", "exit 1")],
    )
    assert invariant_state_from_report(violated) == "fail"
    refused = InvariantReport(
        passed=False, checks_run=1,
        violations=[InvariantViolation("refused", "rm -rf /", "metacharacters")],
    )
    # A check that refused to run is the *check* failing, not the project: the
    # policy halts on `error` and keeps looping on `fail`.
    assert invariant_state_from_report(refused) == "error"


def test_metadata_without_a_kernel_is_absent() -> None:
    assert read_invariant_state(None) is None
    assert read_invariant_state({}) is None


def test_a_kernel_in_the_metadata_is_read_through_its_own_report() -> None:
    class _Kernel:
        last_invariant_report = InvariantReport(passed=True, checks_run=3)

    assert read_invariant_state({"__defensive_kernel__": _Kernel()}) == "pass"


def test_the_loop_state_never_invents_a_value() -> None:
    for raw, expected in [("pass", "pass"), ("unmeasured", "unmeasured"), ("green", None), (3, None)]:
        snapshot = _read_state_unsafe({ITERATE_STATE_KEY: {"mode": "code", "invariant_state": raw}})
        assert snapshot.invariant_state == expected, raw
    assert _read_state_unsafe({ITERATE_STATE_KEY: {"mode": "code"}}).invariant_state is None


def test_the_produced_values_are_the_ones_the_contract_accepts() -> None:
    from iterate_harness.iterate.convergence_policy import INVARIANT_STATES

    assert set(INVARIANT_STATE_VALUES) == set(INVARIANT_STATES)
