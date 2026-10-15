"""The Python half of the kernel's evidence -> decision transcription.

Why a second implementation exists instead of a wrapper: the shared kernel is TypeScript
and this harness is Python, so the only thing the two can hold in common without a build
step is the contract corpus published inside ``iterate-kernel``. ``kernel/fixtures/evidence-decision.ok-01.json``
states the rules in its ``comment`` and carries eight cases with their expected outcome and
expected sentence; this module is coded from that, and
``tests/test_iterate/test_evidence_transcription.py`` asserts it reproduces the fixture. If
either side changes the mapping and the other does not, one of the two repos goes red. That
is the point: agreement by construction (a wrapper) would prove nothing about the path that
actually runs here.

This is a *rendering*, not a judgement. The engine decided everything read below
(``attribution``, ``assertion``, ``diagnosis``, ``circuitBreaker``); nothing here may
re-derive a verdict, and nothing here can upgrade one.

One divergence risk is stated rather than hidden: the expected/actual values are rendered
the way JavaScript's ``JSON.stringify`` would. For the shapes the engine produces (bool,
string, integer, finite float) the two agree; a float that JavaScript would print without
its ``.0`` (``2`` vs ``2.0``) would not, and no archived pack has ever carried one. If one
ever does, the fixture comparison is what catches it — not a comment like this.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

#: The four values the kernel's decision-log entry schema accepts for `outcome`.
Outcome = str

_SUMMARY_LIMIT: Final[int] = 2048

Pack = Mapping[str, object]


def _field(pack: Pack, key: str) -> Mapping[str, object] | None:
    """Read an optional object field.

    Absent and explicit-null are two shapes the engine really emits (the frozen fixtures
    carry ``"diagnosis": null``), and both mean "nothing was recorded here".
    """
    value = pack.get(key)
    return value if isinstance(value, Mapping) else None


def _js(value: object) -> str:
    """Render a value the way JavaScript's JSON.stringify would for these shapes."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _count(value: object) -> int:
    """Read a numeric field the engine may deliver as int, digit-string, null or absent.

    Typed rather than `int(x or 0)` because a Mapping read returns `object`, and silently
    swallowing a TypeError there would mean a malformed breaker level reads as "no trips".
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def decision_outcome_from_evidence(pack: Pack) -> Outcome:
    """Map an evidence pack to the decision-log vocabulary. Order is precedence."""
    breaker = _field(pack, "circuitBreaker") or {}
    if _count(breaker.get("level")) >= 3:
        # The loop was stopped by harness policy. Calling that a `fail` would blame the
        # app for a limit the tool set on itself.
        return "blocked"

    assertion = _field(pack, "assertion")
    if assertion is not None:
        base = "pass" if assertion.get("passed") else "fail"
    else:
        diagnosis = _field(pack, "diagnosis")
        if diagnosis is None:
            base = "inconclusive"
        elif diagnosis.get("class") == "NO_ANOMALY":
            base = "pass"
        elif diagnosis.get("class") == "INCONCLUSIVE":
            base = "inconclusive"
        else:
            base = "fail"

    if base != "pass":
        return base

    # A pass the engine itself refuses to attribute to this operation must not be logged
    # as though it had been. Downgrade only; nothing here can upgrade a verdict.
    attribution = _field(pack, "attribution") or {}
    if attribution.get("contaminated"):
        return "inconclusive"
    if attribution.get("level") == "weak":
        return "inconclusive"
    return "pass"


def decision_summary_from_evidence(pack: Pack) -> str:
    """Render the entry's `summary` from the same fields, in the kernel's order."""
    parts: list[str] = []

    assertion = _field(pack, "assertion")
    if assertion is not None:
        parts.append(
            "assertion {property} {state}: expected {expected}, actual {actual}".format(
                property=assertion.get("property"),
                state="passed" if assertion.get("passed") else "failed",
                expected=_js(assertion.get("expected")),
                actual=_js(assertion.get("actual")),
            )
        )

    diagnosis = _field(pack, "diagnosis")
    if diagnosis is not None:
        report = _field(diagnosis, "report") or {}
        parts.append(f"diagnosis {diagnosis.get('class')}: {report.get('anomaly')}")

    breaker = _field(pack, "circuitBreaker") or {}
    level = _count(breaker.get("level"))
    if level > 0:
        reason = breaker.get("reason")
        parts.append(f"circuitBreaker level {level}" + (f" ({reason})" if reason else ""))

    attribution = _field(pack, "attribution") or {}
    contaminated = ", contaminated" if attribution.get("contaminated") else ""
    parts.append(f"attribution {attribution.get('level')}{contaminated}")

    summary = "; ".join(parts)
    if len(summary) > _SUMMARY_LIMIT:
        return f"{summary[: _SUMMARY_LIMIT - 3]}..."
    return summary
