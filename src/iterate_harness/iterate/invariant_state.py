"""Where the loop's `invariant_state` comes from — and when it must stay absent.

The convergence contract (``convergence_policy.RoundState``) takes ``invariant_state`` as one of
``pass | fail | error | unmeasured``, and its rule set turns on the difference: an unmeasured
gate must never read as a passed one. This module is the only place allowed to make that
statement for the iterate loop, and it makes it from a report that was actually produced.

WHY IT CAN RETURN ``None``. ``None`` means "this run has no invariant verdict to report", and
the caller then leaves the key out of the loop state entirely. Defaulting to ``"pass"`` would
let a run that never checked anything converge on the strength of a verdict nobody produced,
and defaulting to ``"unmeasured"`` would be a claim too — several iterate loops genuinely have
no invariant channel (the defensive kernel exists only in code mode), so the honest answer is
"absent", and whoever consumes it must say so out loud rather than compute a number.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import only for typing
    from ..defensive.invariants import InvariantReport

#: Mirrors ``INVARIANT_STATES`` in the kernel port; kept as literals so this module does not
#: have to import the policy it feeds.
INVARIANT_STATE_VALUES = ("pass", "fail", "error", "unmeasured")


def invariant_state_from_report(report: "InvariantReport | None") -> str | None:
    """Map one aggregated invariant report onto the contract's four verdicts."""
    if report is None:
        return None
    # Nothing was executed: no commands declared, or all of them were refused
    # before running. Either way there is no verdict to report.
    if getattr(report, "checks_run", 0) <= 0:
        # Measured by the first probe of this module: an empty report still carries
        # ``passed=True`` (the dataclass default), so falling through here would
        # hand the policy a "pass" for a gate that never ran.
        return "unmeasured"
    return _from_violations(report)


def _from_violations(report: "InvariantReport") -> str:
    # A `refused` violation is the check failing to run (an unsafe or empty
    # command), not the project breaking an invariant: that is `error`, and
    # the policy halts on it rather than treating it as a failed assertion.
    for violation in getattr(report, "violations", ()) or ():
        if getattr(violation, "kind", "") == "refused":
            return "error"
    return "pass" if getattr(report, "passed", False) else "fail"


def read_invariant_state(metadata: dict[str, Any] | None) -> str | None:
    """Take the invariant verdict out of tool metadata, if a kernel is wired in.

    ``DEFENSIVE_KERNEL_KEY`` is placed in the exec metadata by the engine
    (``engine/query.py``), so this is the sanctioned path — no global, no
    re-running of commands at report time.
    """
    if not metadata:
        return None
    from ..defensive.kernel import DEFENSIVE_KERNEL_KEY

    kernel = metadata.get(DEFENSIVE_KERNEL_KEY)
    if kernel is None:
        return None
    report = getattr(kernel, "last_invariant_report", None)
    if callable(report):  # a method rather than a property
        report = report()
    return invariant_state_from_report(report)
