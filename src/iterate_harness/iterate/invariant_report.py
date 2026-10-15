"""Invariant report: the Python half of the kernel's ``invariant-report`` contract.

This is deliberately the *second* implementation of the rules published in
``@iterate/kernel``'s ``fixtures/invariant-report.ok-01.json``, coded from that fixture's
own rules paragraph rather than by calling into node. The harness is Python and the
kernel is TypeScript; the fixture is the only thing they can both be checked against, and
a wrapper would make them agree by construction while proving nothing about the Python
path that actually runs here.

What this module is for. ``iterate_harness.defensive.invariants.check_invariants`` — the
real runner — records only VIOLATIONS plus a count. The contract demands every check,
passing ones named too, because a consumer cannot show "3 of 4 held" without the fourth
name. ``from_defensive_report`` is the transcription; everything else here is the shape,
its digest, and the re-verification request a failing report earns.

The rules, restated from the fixture so this file stands on its own:

1. ``schemaVersion`` is exactly ``iterate.invariant-report/0.1``.
2. ``outcome`` is ``pass|fail|error``; ``kind`` is ``ensure|command|refused``.
3. ``checks`` holds 1..256 entries, each with required ``name`` (1..256 chars), required
   ``kind``, required ``ok`` boolean, optional ``detail`` (<=4096), and no other keys.
4. THE ONE RULE THAT MATTERS: ``outcome`` follows from ``checks``. ``pass`` requires every
   check ok, ``fail`` requires at least one not ok, ``error`` is unconstrained.
5. ``refused`` is a violation, so a report whose only failing check was refused is ``fail``,
   never ``error``. ``error`` is reserved for checks that could not be evaluated at all,
   and it produces no re-verification request.
6. ``round`` (>=1) and ``durationMs`` (>=0) are optional integers, absent rather than null.
7. The digest is ``iv_`` + sha256 of the canonical JSON form (see ``canonical_json``).
8. A re-verification request is ``iterate.reverification/0.1`` with ``trigger.kind``
   ``invariant``, ``trigger.reportDigest`` matching ``^iv_[0-9a-f]{64}$``, and ``targets``
   carrying at least one of ``recipeSteps``/``dimensions``/``operationIds`` (each 1..64);
   an empty target set is refused.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from iterate_harness.iterate.canonical_json import canonical_json

if TYPE_CHECKING:
    from iterate_harness.defensive.invariants import InvariantReport as DefensiveReport
    from iterate_harness.defensive.invariants import InvariantViolation as DefensiveViolation

#: One target entry, named by its list and index so the refusal carries a path.
_ItemCheck = Callable[[str, int, object], None]

INVARIANT_REPORT_SCHEMA_VERSION = "iterate.invariant-report/0.1"
REVERIFICATION_SCHEMA_VERSION = "iterate.reverification/0.1"

DIGEST_PREFIX = "iv_"
DIGEST_PATTERN = re.compile(r"^iv_[0-9a-f]{64}$")

OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_ERROR = "error"
OUTCOMES: frozenset[str] = frozenset((OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_ERROR))

KIND_ENSURE = "ensure"
KIND_COMMAND = "command"
KIND_REFUSED = "refused"
CHECK_KINDS: frozenset[str] = frozenset((KIND_ENSURE, KIND_COMMAND, KIND_REFUSED))

#: The contract's key names, as the kernel spells them.
REPORT_FIELDS: frozenset[str] = frozenset(
    ("schemaVersion", "outcome", "checks", "round", "durationMs")
)
CHECK_FIELDS: frozenset[str] = frozenset(("name", "kind", "ok", "detail"))
TARGET_FIELDS: frozenset[str] = frozenset(("recipeSteps", "dimensions", "operationIds"))

MIN_CHECKS = 1
MAX_CHECKS = 256
MIN_CHECK_NAME_LENGTH = 1
MAX_CHECK_NAME_LENGTH = 256
MAX_CHECK_DETAIL_LENGTH = 4096
MIN_ROUND = 1
MIN_DURATION_MS = 0

TRIGGER_KIND = "invariant"
MAX_TARGET_ITEMS = 64
MAX_DIMENSION_ID_LENGTH = 64
DIMENSION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
OPERATION_ID_PATTERN = re.compile(r"^op_[0-9A-HJKMNP-TV-Z]{26}$")

#: Details the producer writes for a check it could not measure. The prefix is the
#: fixture's own (``errorInput`` says ``not measured: ...``), so an unmeasured check reads
#: the same in either implementation rather than looking like a fabricated verdict.
UNMEASURED_DETAIL = "not measured: "
UNMEASURED_NO_VERDICT_DETAIL = (
    "not measured: the invariant runner returned no verdict for this check"
)
UNMEASURED_DRY_RUN_DETAIL = (
    "not measured: previewed in a dry run; the command was never executed"
)

INVARIANT_REPORT_E_SCHEMA = "INVARIANT_REPORT_E_SCHEMA"
INVARIANT_REPORT_E_FIELD = "INVARIANT_REPORT_E_FIELD"
INVARIANT_REPORT_E_OUTCOME = "INVARIANT_REPORT_E_OUTCOME"
INVARIANT_REPORT_E_CHECKS = "INVARIANT_REPORT_E_CHECKS"
INVARIANT_REPORT_E_CHECK = "INVARIANT_REPORT_E_CHECK"
INVARIANT_REPORT_E_ROUND = "INVARIANT_REPORT_E_ROUND"
INVARIANT_REPORT_E_DURATION = "INVARIANT_REPORT_E_DURATION"
INVARIANT_REPORT_E_TARGETS = "INVARIANT_REPORT_E_TARGETS"
INVARIANT_REPORT_E_UNMEASURED = "INVARIANT_REPORT_E_UNMEASURED"
INVARIANT_REPORT_E_CONFIGURED = "INVARIANT_REPORT_E_CONFIGURED"


class InvariantReportError(ValueError):
    """A report that cannot be built, read, digested or acted on.

    Carries ``code``/``message``/``remedy`` (and the offending ``path``) to match the
    kernel's error frames. The remedy is written for the agent that has to act on it, not
    for whoever is reading this source: it names the field, the limit, and what to change.
    """

    def __init__(self, code: str, message: str, remedy: str, path: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.remedy = remedy
        self.path = path


@dataclass(frozen=True)
class InvariantCheck:
    """One configured invariant and whether it held.

    The field types are deliberately permissive — this is a carrier, not the gate. A
    report that contradicts itself has to be constructible long enough to be refused
    loudly, which is ``validate_report``'s job.
    """

    name: str
    kind: str
    ok: bool
    detail: str | None = None

    def to_json(self) -> dict[str, object]:
        """The contract's key names, with an absent ``detail`` omitted rather than null."""
        out: dict[str, object] = {"name": self.name, "kind": self.kind, "ok": self.ok}
        if self.detail is not None:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class InvariantReport:
    """What one run may state about its own invariant checks."""

    outcome: str
    checks: Sequence[InvariantCheck]
    round: int | None = None
    duration_ms: int | None = None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {
            "schemaVersion": INVARIANT_REPORT_SCHEMA_VERSION,
            "outcome": self.outcome,
            "checks": [check.to_json() for check in self.checks],
        }
        # Rule (6): both are optional. Emitting ``"round": null`` would be a third state
        # the contract does not have — the kernel's schema fixes the key set.
        if self.round is not None:
            out["round"] = self.round
        if self.duration_ms is not None:
            out["durationMs"] = self.duration_ms
        return out


def validate_report(report: InvariantReport) -> None:
    """Refuse a report the contract will not carry. Raises :class:`InvariantReportError`.

    Every public producer and reader funnels through this; the dataclasses above only hold
    values. The centerpiece is rule (4): an ``outcome`` the ``checks`` do not support.
    """
    if report.outcome not in OUTCOMES:
        raise InvariantReportError(
            INVARIANT_REPORT_E_OUTCOME,
            f"outcome must be one of {', '.join(sorted(OUTCOMES))}, got {report.outcome!r}",
            "pass means every check held, fail means at least one did not, error means a "
            "check could not be evaluated; pick the one the checks below actually support",
            "outcome",
        )
    if not MIN_CHECKS <= len(report.checks) <= MAX_CHECKS:
        raise InvariantReportError(
            INVARIANT_REPORT_E_CHECKS,
            f"checks must hold {MIN_CHECKS}..{MAX_CHECKS} entries, got {len(report.checks)}",
            "a report with no checks cannot tell 'everything held' from 'nothing ran', so "
            "name at least the checks you configured; above "
            f"{MAX_CHECKS}, split the run into more rounds",
            "checks",
        )
    for index, check in enumerate(report.checks):
        _validate_check(check, index)
    if report.round is not None and report.round < MIN_ROUND:
        raise InvariantReportError(
            INVARIANT_REPORT_E_ROUND,
            f"round must be an integer >= {MIN_ROUND}, got {report.round!r}",
            "rounds are counted from 1; drop the key when this report is not part of a "
            "numbered run rather than sending 0",
            "round",
        )
    if report.duration_ms is not None and report.duration_ms < MIN_DURATION_MS:
        raise InvariantReportError(
            INVARIANT_REPORT_E_DURATION,
            f"durationMs must be an integer >= {MIN_DURATION_MS}, got {report.duration_ms!r}",
            "it is a measured wall-clock duration; drop the key when nothing was timed "
            "instead of sending a negative or zero-substituted guess",
            "durationMs",
        )

    failing = [check.name for check in report.checks if not check.ok]
    if report.outcome == OUTCOME_PASS and failing:
        raise InvariantReportError(
            INVARIANT_REPORT_E_OUTCOME,
            f"outcome is pass but {len(failing)} check(s) are not ok: {', '.join(failing)}",
            "the checks list is the evidence and the outcome is only a claim about it. "
            "Set outcome to fail, or make those checks hold and re-run. Removing or "
            "flipping a failing check to keep the word pass is the invention this "
            "contract refuses on both sides",
            "outcome",
        )
    if report.outcome == OUTCOME_FAIL and not failing:
        raise InvariantReportError(
            INVARIANT_REPORT_E_OUTCOME,
            "outcome is fail but every check is ok",
            "a failure has to name the check that failed: mark it ok=false with the "
            "runner's detail, use outcome pass if everything really held, or use error "
            "if a check never got evaluated",
            "outcome",
        )


def _validate_check(check: InvariantCheck, index: int) -> None:
    """Rule (3), one check at a time, with the path the fixture's errors carry."""
    path = f"checks.{index}"
    if not isinstance(check.name, str) or not isinstance(check.ok, bool):
        raise InvariantReportError(
            INVARIANT_REPORT_E_CHECK,
            f"{path} needs a string name and a boolean ok, got name "
            f"{type(check.name).__name__} and ok {type(check.ok).__name__}",
            "name is the runner's own label and ok is whether that check held; both are "
            f"required on every entry under {path}",
            path,
        )
    if not MIN_CHECK_NAME_LENGTH <= len(check.name) <= MAX_CHECK_NAME_LENGTH:
        raise InvariantReportError(
            INVARIANT_REPORT_E_CHECK,
            f"{path}.name must be {MIN_CHECK_NAME_LENGTH}..{MAX_CHECK_NAME_LENGTH} "
            f"characters, got {len(check.name)}",
            "a check is named by the runner's label (ensure:<path> or <module>:<command>); "
            f"shorten what was configured rather than truncating the name under {path}",
            f"{path}.name",
        )
    if check.kind not in CHECK_KINDS:
        raise InvariantReportError(
            INVARIANT_REPORT_E_CHECK,
            f"{path}.kind must be one of {', '.join(sorted(CHECK_KINDS))}, got "
            f"{check.kind!r}",
            "ensure is a file assertion, command is a configured command that ran, "
            "refused is a configured command the runner declined to execute; anything "
            "else is a kind the other implementation cannot route",
            f"{path}.kind",
        )
    if check.detail is not None:
        if not isinstance(check.detail, str):
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"{path}.detail must be a string when present, got {type(check.detail).__name__}",
                f"drop {path}.detail or pass the runner's own text; a non-string detail "
                "has no canonical form",
                f"{path}.detail",
            )
        if len(check.detail) > MAX_CHECK_DETAIL_LENGTH:
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"{path}.detail exceeds {MAX_CHECK_DETAIL_LENGTH} characters "
                f"(got {len(check.detail)})",
                "the runner already truncates a failing command's output to its last "
                "three lines; keep the evidence that fits the contract and leave the "
                "full log where the report can cite it by name",
                f"{path}.detail",
            )


def invariant_report_digest(report: InvariantReport) -> str:
    """``iv_`` + sha256 over the report's canonical JSON form.

    Validates first: a digest of a report the contract refuses would be a citation to
    something the other side cannot read, and the re-verification request would carry it.
    Absent optionals stay absent here too — ``round`` null and ``round`` missing are
    different bytes, so the digest pins which one was said.
    """
    validate_report(report)
    return DIGEST_PREFIX + hashlib.sha256(
        canonical_json(report.to_json()).encode("utf-8")
    ).hexdigest()


def failing_checks(report: InvariantReport) -> list[str]:
    """The names of the checks that did not hold, in report order.

    Order is the report's own, not sorted: the producer emits configured order, so a
    consumer comparing this list against ``expectedFailingChecks`` is comparing the run.
    """
    return [check.name for check in report.checks if not check.ok]


def invariant_report_from_json(payload: object) -> InvariantReport:
    """Read the contract's JSON shape, refusing anything it would not carry.

    Key presence and key type live here because a frozen dataclass cannot express them:
    ``{"ok": null}`` is not a check and ``{"note": ...}`` is not a report — the fixture
    refuses both (``check-without-ok``, ``extra-top-level-key``). Field limits and the
    outcome/checks rule are then enforced by the same ``validate_report`` a hand-built
    report goes through, so there is one gate rather than two that can drift.
    """
    if not isinstance(payload, Mapping):
        raise InvariantReportError(
            INVARIANT_REPORT_E_FIELD,
            f"the report must be a JSON object, got {type(payload).__name__}",
            "read it as one object carrying schemaVersion, outcome and checks",
            "",
        )
    unexpected = sorted(str(key) for key in payload if str(key) not in REPORT_FIELDS)
    if unexpected:
        raise InvariantReportError(
            INVARIANT_REPORT_E_FIELD,
            f"unknown key(s) in the report: {', '.join(unexpected)}",
            "the contract's report key set is exactly schemaVersion, outcome, checks, "
            "round, durationMs; carry anything else in your own state, not in the "
            "shared report",
            ", ".join(unexpected),
        )

    version = payload.get("schemaVersion")
    if version != INVARIANT_REPORT_SCHEMA_VERSION:
        raise InvariantReportError(
            INVARIANT_REPORT_E_SCHEMA,
            f"schemaVersion must be {INVARIANT_REPORT_SCHEMA_VERSION!r}, got {version!r}",
            "this producer reads 0.1 only; when the kernel publishes a new version, "
            "re-vendor its fixture and update the constant here rather than accepting "
            "an unknown shape",
            "schemaVersion",
        )
    outcome = payload.get("outcome")
    if not isinstance(outcome, str):
        raise InvariantReportError(
            INVARIANT_REPORT_E_OUTCOME,
            f"outcome is required and must be one of {', '.join(sorted(OUTCOMES))}, got "
            f"{outcome!r}",
            "pass, fail or error — decided by the checks list, never left out, because a "
            "consumer cannot branch on a verdict that is absent",
            "outcome",
        )
    raw_checks = payload.get("checks")
    if not isinstance(raw_checks, list):
        raise InvariantReportError(
            INVARIANT_REPORT_E_CHECKS,
            f"checks is required and must be an array of {MIN_CHECKS}..{MAX_CHECKS} entries"
            f", got {raw_checks!r}",
            "list every check the run configured, passing ones included; the runner's "
            "violation list alone cannot show what held",
            "checks",
        )

    checks: list[InvariantCheck] = []
    for index, item in enumerate(raw_checks):
        path = f"checks.{index}"
        if not isinstance(item, Mapping):
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"{path} must be a JSON object, got {type(item).__name__}",
                "each entry carries name, kind, ok and optionally detail",
                path,
            )
        stray = sorted(str(key) for key in item if str(key) not in CHECK_FIELDS)
        if stray:
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"unknown key(s) in {path}: {', '.join(stray)}",
                f"a check is exactly name, kind, ok and optional detail; {path} carries "
                "more, which the other implementation would drop or reject",
                f"{path}.{stray[0]}",
            )
        name = item.get("name")
        if not isinstance(name, str):
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"{path}.name is required and must be a string, got {name!r}",
                "reuse the runner's label — ensure:<path> for a file assertion, "
                "<module>:<command> for a configured command",
                f"{path}.name",
            )
        kind = item.get("kind")
        if not isinstance(kind, str):
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"{path}.kind is required and must be one of "
                f"{', '.join(sorted(CHECK_KINDS))}, got {kind!r}",
                "the runner reports this per check; carry it verbatim instead of "
                "guessing from the name",
                f"{path}.kind",
            )
        if "ok" not in item:
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"{path}.ok is required",
                "a check without a verdict is not a check: set ok true only when the "
                "runner evaluated it, and give it a detail saying why it could not",
                f"{path}.ok",
            )
        ok = item["ok"]
        if not isinstance(ok, bool):
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"{path}.ok must be a boolean, got {ok!r}",
                "ok is the runner's measured result; a string, number or null is not "
                "a result",
                f"{path}.ok",
            )
        detail = item.get("detail")
        if "detail" in item and detail is None:
            # An explicit null is not the same claim as omitting the key, and the digest
            # is a claim about exact bytes: normalising one into the other would make two
            # different reports share a digest.
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"{path}.detail is present but null",
                "either carry the runner's detail string or leave the key out entirely; "
                "a null detail is neither",
                f"{path}.detail",
            )
        if detail is not None and not isinstance(detail, str):
            raise InvariantReportError(
                INVARIANT_REPORT_E_CHECK,
                f"{path}.detail must be a string when present, got {detail!r}",
                "drop the key or carry the runner's own text; a missing detail is "
                "allowed, a non-string one is not",
                f"{path}.detail",
            )
        checks.append(InvariantCheck(name=name, kind=kind, ok=ok, detail=detail))

    report = InvariantReport(
        outcome=outcome,
        checks=tuple(checks),
        round=_optional_int(payload, "round"),
        duration_ms=_optional_int(payload, "durationMs"),
    )
    validate_report(report)
    return report


def _optional_int(payload: Mapping[object, object], name: str) -> int | None:
    """Rule (6): present means a real integer, absent means nothing was said.

    An explicit ``null`` is refused rather than read as absent — the two are different
    claims, and the digest must not let them share bytes.
    """
    if name not in payload:
        return None
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvariantReportError(
            INVARIANT_REPORT_E_FIELD,
            f"{name} must be an integer when present, got {value!r}",
            f"{name} is a count of whole units; drop the key when nothing was measured "
            "rather than sending a string, a float, a null or a boolean",
            name,
        )
    return value


def from_defensive_report(
    configured_ensure: Sequence[str],
    configured_commands: Mapping[str, Sequence[str]],
    defensive_report: DefensiveReport | None,
    *,
    dry_run: bool = False,
) -> InvariantReport:
    """Transcribe the real runner's output into the contract's shape.

    WHY THE CHECK LIST COMES FROM THE CONFIG, NOT FROM THE RUNNER. ``check_invariants``
    returns ``passed``, an integer ``checks_run`` and a list of violations — it never
    records the names of what held. Listing only the violations would produce a report
    whose checks are all failures, and a consumer could not tell "3 of 4 held" from "4
    failed"; inventing the passing names would be worse, since an invented name is a claim
    that a check exists. So every entry comes from what was configured, in configured
    order (``ensure`` entries first, then each module's commands), and each is marked not
    ok by whether the runner recorded a violation for that EXACT label. ``kind`` is taken
    from the violation when there is one — that is how a refused command (the runner
    declined to execute it for carrying a shell metacharacter) reaches the contract as
    ``refused`` rather than as ``command`` that failed.

    WHY ``outcome`` CAN NEVER BE SYNTHESISED TO ``pass`` OVER A VIOLATION. ``pass`` is
    only reachable on the branch where no check is not ok, and a check is not ok whenever
    the runner named a violation for it. So a violation always survives into the list,
    ``pass`` becomes unreachable, and ``validate_report`` refuses it from the other side
    too. The runner's own ``passed`` flag is never consulted to clear a violation: it is a
    summary the runner recomputes, and where it disagrees with the violation list the
    list is the evidence. The opposite asymmetry is guarded as well: a report the runner
    called failed but attributed to no check becomes ``error``, not ``pass`` and not an
    invented ``fail`` — a failure nobody can name is not a finding, and fabricating one to
    satisfy rule (4) would be the invention the whole contract exists to refuse.

    DRY RUNS ARE REPORTED AS UNMEASURED, NOT AS OK. With ``dry_run=True`` the runner
    counts each safe command as a check without executing it, so a naive transcription
    marks those checks ok — a green invariant report from a run that never ran anything,
    which downstream reads as "the edit is safe". The honest option is taken instead: a
    previewed command is a check that could not be evaluated, so it gets ``ok=False`` with
    a ``not measured: ...`` detail and the report's outcome is ``error`` (fixture rule (5)
    reserves ``error`` for exactly this, and rule (5) also keeps a ``refused`` command out
    of it — refusal is a violation, so an all-refused dry run stays ``fail``). ``error``
    then produces no re-verification request: an unmeasured check must not spin up the
    other product's engine to launder it into activity.

    ``checks_run`` is used for one thing only: zero means the runner evaluated nothing, so
    every configured check is unmeasured and the outcome is ``error``. It is never used to
    reconcile individual checks, because it is a count, not a per-check record — a run
    that counted 1 of 4 configured checks cannot say WHICH one, and guessing is how a
    passing name gets invented.

    ``round`` and ``durationMs`` stay absent: the runner measures neither, and a number in
    the shared report has to come from whoever measured it.
    """
    labels: list[tuple[str, str]] = []
    for entry in configured_ensure:
        labels.append((f"ensure:{entry}", KIND_ENSURE))
    for module, commands in configured_commands.items():
        for command in commands:
            labels.append((f"{module}:{command}", KIND_COMMAND))
    if not labels:
        raise InvariantReportError(
            INVARIANT_REPORT_E_CONFIGURED,
            "there are no checks to report: nothing was configured",
            "the contract needs 1..256 checks. With no invariants.ensure and no "
            "invariants.commands, emit no report at all rather than an empty or "
            "all-passing one — 'nothing configured' and 'everything held' are different "
            "facts and this shape cannot tell them apart",
            "checks",
        )

    violations: list[DefensiveViolation] = (
        list(defensive_report.violations) if defensive_report is not None else []
    )
    by_label: dict[str, DefensiveViolation] = {}
    for violation in violations:
        # First wins: the runner records one violation per configured entry, so a repeated
        # label is the same finding seen twice, not two findings to detail twice.
        by_label.setdefault(violation.label, violation)
    no_verdict = defensive_report is None or defensive_report.checks_run <= 0

    checks: list[InvariantCheck] = []
    unmeasured = 0
    for label, kind in labels:
        found = None if no_verdict else by_label.get(label)
        if found is not None:
            checks.append(
                InvariantCheck(
                    name=label,
                    kind=found.kind,
                    ok=False,
                    detail=found.detail,
                )
            )
            continue
        if no_verdict:
            checks.append(
                InvariantCheck(
                    name=label, kind=kind, ok=False, detail=UNMEASURED_NO_VERDICT_DETAIL
                )
            )
            unmeasured += 1
        elif kind == KIND_COMMAND and dry_run:
            checks.append(
                InvariantCheck(name=label, kind=kind, ok=False, detail=UNMEASURED_DRY_RUN_DETAIL)
            )
            unmeasured += 1
        else:
            checks.append(InvariantCheck(name=label, kind=kind, ok=True))

    configured_labels = {label for label, _ in labels}
    for violation in violations:
        if violation.label not in configured_labels:
            # A finding this producer cannot attribute to a configured entry still has to
            # be published: dropping it is the one way a violation could reach ``pass``.
            checks.append(
                InvariantCheck(
                    name=violation.label,
                    kind=violation.kind,
                    ok=False,
                    detail=violation.detail,
                )
            )

    if unmeasured:
        outcome = OUTCOME_ERROR
    elif any(not check.ok for check in checks):
        outcome = OUTCOME_FAIL
    elif defensive_report is not None and not defensive_report.passed:
        # A failed verdict with nothing to point at: not pass, and not fail either,
        # because fail would need a failing check that does not exist.
        outcome = OUTCOME_ERROR
    else:
        outcome = OUTCOME_PASS

    report = InvariantReport(outcome=outcome, checks=tuple(checks))
    validate_report(report)
    return report


def re_verification_request(
    report: InvariantReport,
    *,
    recipe_steps: Sequence[Mapping[str, object]] | None = None,
    dimensions: Sequence[str] | None = None,
    operation_ids: Sequence[str] | None = None,
) -> dict[str, object] | None:
    """The request a failing report earns, or ``None`` when nothing needs re-verifying.

    Keyword arguments cannot express the fixture's ``unknown-target-kind`` refusal — a
    stray key would be a ``TypeError`` from Python, not the contract's error — so the
    validation lives in :func:`re_verification_request_from_targets`, which this wraps
    with the typed lists the callers here actually have.
    """
    targets: dict[str, object] = {}
    if recipe_steps is not None:
        targets["recipeSteps"] = list(recipe_steps)
    if dimensions is not None:
        targets["dimensions"] = list(dimensions)
    if operation_ids is not None:
        targets["operationIds"] = list(operation_ids)
    return re_verification_request_from_targets(report, targets)


def re_verification_request_from_targets(
    report: InvariantReport, targets: Mapping[str, object]
) -> dict[str, object] | None:
    """Build the request from a raw ``targets`` mapping, as the fixture states it.

    A passing report gets no request: there is nothing to re-verify, and the caller may
    build targets unconditionally and have them ignored rather than special-casing green
    runs. An ``error`` report gets a refusal instead of a request — a check that could not
    run is not a finding, and handing it to the other product's engine to "re-verify"
    would turn an unmeasured state into activity that looks like a response to evidence.
    """
    validate_report(report)
    if report.outcome == OUTCOME_PASS:
        return None
    if report.outcome == OUTCOME_ERROR:
        names = [
            check.name
            for check in report.checks
            if check.detail is not None and UNMEASURED_DETAIL in check.detail
        ]
        listed = f" ({', '.join(names)})" if names else ""
        raise InvariantReportError(
            INVARIANT_REPORT_E_UNMEASURED,
            f"outcome is error on a report of {len(report.checks)} check(s), so there is "
            f"nothing measured to re-verify{listed}",
            "a check that could not be evaluated is not a finding. Make the check run — "
            "install what the command needs, or remove it from invariants.commands — then "
            "re-run and request re-verification against the pass or fail report that "
            "produces",
            "outcome",
        )
    validated = _validate_targets(targets)
    return {
        "schemaVersion": REVERIFICATION_SCHEMA_VERSION,
        "trigger": {
            "kind": TRIGGER_KIND,
            "reportDigest": invariant_report_digest(report),
        },
        "targets": validated,
    }


def _validate_targets(targets: Mapping[str, object]) -> dict[str, list[object]]:
    """Rule (8): at least one of the three lists, each 1..64 entries, each entry shaped."""
    stray = sorted(str(key) for key in targets if str(key) not in TARGET_FIELDS)
    if stray:
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            f"unknown key(s) in targets: {', '.join(stray)}",
            "targets carries exactly recipeSteps, dimensions and operationIds; a kind "
            "outside those three cannot be routed by either implementation, so name the "
            "target you mean or drop it",
            f"targets.{stray[0]}",
        )

    out: dict[str, list[object]] = {}
    steps = targets.get("recipeSteps")
    if steps is not None:
        out["recipeSteps"] = _target_list("recipeSteps", steps, _validate_recipe_step)
    dimensions = targets.get("dimensions")
    if dimensions is not None:
        out["dimensions"] = _target_list("dimensions", dimensions, _validate_dimension_id)
    operation_ids = targets.get("operationIds")
    if operation_ids is not None:
        out["operationIds"] = _target_list("operationIds", operation_ids, _validate_operation_id)

    if not out:
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            "targets is empty: none of recipeSteps, dimensions, operationIds carries "
            "anything",
            "a re-verification with no target re-verifies nothing, and the engine would "
            "start a round that can produce no evidence; name at least one recipe step, "
            "dimension or operation id",
            "targets",
        )
    return out


def _target_list(name: str, raw: object, check: _ItemCheck) -> list[object]:
    if not isinstance(raw, list):
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            f"targets.{name} must be an array, got {type(raw).__name__}",
            f"{name} is a list of 1..{MAX_TARGET_ITEMS} targets; pass an array, or omit "
            "the key entirely",
            f"targets.{name}",
        )
    if not 1 <= len(raw) <= MAX_TARGET_ITEMS:
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            f"targets.{name} must hold 1..{MAX_TARGET_ITEMS} entries, got {len(raw)}",
            f"an empty {name} list is not a target; narrow the request to what this run "
            f"can re-verify, and keep it at or under {MAX_TARGET_ITEMS}",
            f"targets.{name}",
        )
    items: list[object] = list(raw)
    for index, item in enumerate(items):
        check(name, index, item)
    return items


def _validate_recipe_step(name: str, index: int, item: object) -> None:
    """Steps carry 1..64 bound and must be objects with a ``kind``; the rest is opaque.

    The fixture states the count for ``recipeSteps`` but not the step body, so the step is
    carried verbatim rather than re-shaped: re-serialising a step this module does not own
    would change the bytes the other side is trying to re-run.
    """
    path = f"targets.{name}.{index}"
    if not isinstance(item, Mapping):
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            f"{path} must be a JSON object, got {type(item).__name__}",
            "a recipe step is an object with a kind and its params; carry the step the "
            "run configured instead of a bare string",
            path,
        )
    kind = item.get("kind")
    if not isinstance(kind, str) or not kind:
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            f"{path}.kind is required and must be a non-empty string, got {kind!r}",
            "the engine dispatches on the step's kind; a step without one cannot be run",
            f"{path}.kind",
        )


def _validate_dimension_id(name: str, index: int, item: object) -> None:
    path = f"targets.{name}.{index}"
    if not isinstance(item, str) or not item:
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            f"{path} must be a non-empty string, got {item!r}",
            "dimensions are lower-case kebab ids; pass the id from the dimension config",
            path,
        )
    if len(item) > MAX_DIMENSION_ID_LENGTH:
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            f"{path} is longer than {MAX_DIMENSION_ID_LENGTH} characters",
            "shorten the dimension id",
            path,
        )
    if not DIMENSION_ID_PATTERN.match(item):
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            f"{path} is not a lower-case kebab id: {item!r}",
            "ids match ^[a-z0-9]+(?:-[a-z0-9]+)*$; use the id as config/dimensions.yaml "
            "spells it rather than relabelling it here",
            path,
        )


def _validate_operation_id(name: str, index: int, item: object) -> None:
    path = f"targets.{name}.{index}"
    if not isinstance(item, str) or not OPERATION_ID_PATTERN.match(item):
        raise InvariantReportError(
            INVARIANT_REPORT_E_TARGETS,
            f"{path} is not an operation id: {item!r}",
            "operation ids match ^op_[0-9A-HJKMNP-TV-Z]{26}$ (op_ plus 26 Crockford "
            "base32 characters); copy the id from the evidence rather than typing one",
            path,
        )
