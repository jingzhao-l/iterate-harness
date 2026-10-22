"""Which convergence gates this round can honestly report.

``RoundState`` (the kernel contract the loop is being moved onto) demands
``invariant_state``, ``evidence_complete`` and ``breaker_level`` as plain values, but the harness
only sometimes has real answers: the invariant gate exists only in code mode, evidence
validation is a project switch, and there is no circuit breaker in this host at all. This
module's whole job is to keep that difference visible — it returns **only** what was measured,
so an absent gate stays absent instead of arriving as a default that reads like a verdict.

Keeping the selection in one function also means the publish site in the tool is a single
assignment: the part with no end-to-end test is the wiring, not the decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .invariant_state import read_invariant_state

if TYPE_CHECKING:  # pragma: no cover
    from .types import ReviewFinding

GATE_KEYS = ("invariant_state", "evidence_complete")


def measured_gates(
    *,
    evidence_validation: bool,
    findings: "list[ReviewFinding] | None",
    cwd: Path | str,
    metadata: dict[str, Any] | None,
) -> dict[str, object]:
    """Report the gates this round actually measured.

    ``evidence_complete`` is computed here rather than borrowed from the meta-review action:
    the loop decides at the round boundary, and a verdict from a call that may not have
    happened yet is not a measurement — even though both use the same pure check.
    """
    gates: dict[str, object] = {}

    invariant = read_invariant_state(metadata)
    if invariant is not None:
        gates["invariant_state"] = invariant

    if evidence_validation:
        from . import evidence

        audit = evidence.verify_findings(
            Path(cwd),
            findings=list(findings or ()),
            read_set=evidence.read_set_from_metadata(metadata),
        )
        # An empty findings list is honestly "complete": every finding the round
        # claimed was attested (there were none to attest). It is not a claim that
        # the project was verified — that is what `open_findings` says.
        gates["evidence_complete"] = audit.passed

    # `breaker_level` is not produced here, and not because nothing owns it: the loop policy
    # counts its own no-progress rounds and feeds that level to the kernel
    # (`loop_policy.IterateLoopPolicy.breaker_level`). A *round* cannot measure the escalation of
    # the run it sits inside, so the round stays silent on it rather than reporting a number it
    # cannot see.
    return gates
