"""Who attributed what: the ledger's dimension attribution, and the kernel's verdict on it.

Scenario ② asks a compaction hook to compress the history of the dimensions the kernel
already verified before it touches anything else. Ordering that needs two facts, and this
module's whole job is to hand both over *without judging them*:

- PLANNED ids: the published run-plan file when one exists — that file is the contract the
  other harness reads — and the caller's fallback (the effective config's dimensions) when
  there is none. A file that exists but lists nothing is a plan, so ``[]`` and ``None`` are
  different facts and never collapsed.
- RECORDED attribution: the ledger's own entries. A decision whose data carries no valid
  ``dimension`` belongs to no dimension and is not guessed at — the same rule
  :func:`~iterate_harness.iterate.dimension_context.dimension_context_from_evidence` applies
  to evidence packs without the key.

The verdict is the kernel's: :func:`project_dimension_context` only feeds
``dimension_context`` the two halves and returns what it says. A caller that compared
decision counts and concluded "verified" on its own would be re-litigating a judgement this
repo already delegated — which is exactly how the convergence rule was lost the first time.

The shapes are imported, never restated: ``DIMENSION_ID_PATTERN``/``DIMENSION_ID_MAX_LENGTH``
come from the dimension-context contract and ``OPERATION_ID_PATTERN`` from the invariant
report's. A validation rule written twice here would be free to drift from the transcription
that decides whether the value survives into the ledger.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import decision_log, run_plan
from .dimension_context import (
    DIMENSION_E_ID,
    DIMENSION_ID_MAX_LENGTH,
    DIMENSION_ID_PATTERN,
    DimensionContext,
    DimensionContextError,
    dimension_context,
)
from .invariant_report import OPERATION_ID_PATTERN

log = logging.getLogger(__name__)

#: The dimension half already has a kernel code (``KERNEL_E_DIMENSION_ID``); the operation
#: half has none, so it follows the same ``KERNEL_E_*`` shape instead of inventing a
#: harness-private name that the other side would never recognise.
OPERATION_ID_E_CODE = "KERNEL_E_OPERATION_ID"

#: What the ledger's ``data`` payloads call these two facts. The kernel's transcription
#: (``kernel_decision_record``) reads exactly these keys; a writer that uses another name
#: gets a silently unattributed decision.
DIMENSION_DATA_KEY = "dimension"
OPERATION_ID_DATA_KEY = "operationId"

#: The relayed echo, as :mod:`.reverify_trigger` writes it into a ``reverification`` ledger
#: entry. Named here because the writer and this reader are two halves of one key: a writer
#: that used another name would leave every cross-check silently finding no echo, which is the
#: quiet direction of wrong.
ECHOED_DIMENSION_DATA_KEY = "echoedDimension"


def is_valid_dimension_id(value: object) -> bool:
    """Whether ``value`` is a dimension id the kernel would carry into the ledger draft."""
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= DIMENSION_ID_MAX_LENGTH
        and DIMENSION_ID_PATTERN.match(value) is not None
    )


def is_valid_operation_id(value: object) -> bool:
    """Whether ``value`` is an operation id the kernel would carry into the ledger draft."""
    return isinstance(value, str) and OPERATION_ID_PATTERN.match(value) is not None


def attribution_refusal(
    *, dimension: object, operation_id: object
) -> dict[str, str] | None:
    """The tool-shaped refusal for a malformed attribution, or ``None`` when it validates.

    A typo'd id that the transcription drops is worse than a rejected call: the coverage
    line then reports a dimension as unverified while the run believes it recorded one, so
    the gap reads as measured. Refusing costs the caller nothing it was not already going
    to pay — the decision simply is not written until the attribution is right.

    The messages interpolate the imported pattern and limit rather than restating them, so
    this cannot disagree with the rule it enforces.
    """
    if dimension is not None and not is_valid_dimension_id(dimension):
        return {
            "error": f"{dimension!r} is not a dimension id this ledger can attribute the decision to",
            "code": DIMENSION_E_ID,
            "remedy": (
                f"a dimension id is lower-case kebab-case, at most {DIMENSION_ID_MAX_LENGTH} "
                f"characters and matches {DIMENSION_ID_PATTERN.pattern}; copy it from "
                "config/dimensions.yaml instead of retyping it, and omit the field when this "
                "call served no declared dimension"
            ),
        }
    if operation_id is not None and not is_valid_operation_id(operation_id):
        return {
            "error": f"{operation_id!r} is not an operation id",
            "code": OPERATION_ID_E_CODE,
            "remedy": (
                f"an operation id matches {OPERATION_ID_PATTERN.pattern}; take it from the "
                "engine's own evidence rather than composing one, and omit the field when this "
                "decision is not tied to one operation"
            ),
        }
    return None


def _plan_file(project_root: str | Path) -> Path:
    """The plan path, composed read-only.

    ``run_plan.plan_path`` creates its directory; reading a plan must not leave a ``.iterate``
    behind in a project that never started a run.
    """
    return Path(project_root) / decision_log.LOG_DIR / run_plan.RUN_PLAN_FILENAME


def planned_ids_from_plan_file(path: Path) -> list[str] | None:
    """The ids in the published plan file, or ``None`` when there is none to read."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    dimensions = parsed.get("dimensions")
    if not isinstance(dimensions, list):
        return None
    return [
        str(item["id"])
        for item in dimensions
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def planned_dimension_ids(
    project_root: str | Path, *, fallback: Sequence[str] | None = None
) -> list[str]:
    """The run's planned dimension ids: the published plan wins over the config fallback."""
    from_file = planned_ids_from_plan_file(_plan_file(project_root))
    return from_file if from_file is not None else [str(item) for item in (fallback or [])]


def _attributed_entries(project_root: str | Path) -> list[tuple[str, str | None]]:
    """``(dimension, operationId)`` per ledger entry that names a valid dimension, in file order.

    An entry naming no valid dimension is skipped rather than guessed at, and an entry that
    names a dimension but no operation id still counts toward that dimension's evidence.
    """
    out: list[tuple[str, str | None]] = []
    for entry in decision_log.read_entries(project_root):
        data: Mapping[str, Any] = entry.data if isinstance(entry.data, dict) else {}
        dimension = data.get(DIMENSION_DATA_KEY)
        if not is_valid_dimension_id(dimension):
            continue
        operation_id = data.get(OPERATION_ID_DATA_KEY)
        out.append(
            (
                str(dimension),
                operation_id if is_valid_operation_id(operation_id) else None,
            )
        )
    return out


def recorded_dimension_counts(project_root: str | Path) -> dict[str, int]:
    """Decisions per attributed dimension — the ``recorded`` half of the kernel's contract."""
    counts: dict[str, int] = {}
    for dimension, _operation_id in _attributed_entries(project_root):
        counts[dimension] = counts.get(dimension, 0) + 1
    return counts


def operation_dimension_map(project_root: str | Path) -> dict[str, str]:
    """Operation id → dimension, as the ledger attributes it.

    Deterministic winner on a collision: an operation id named by more than one decision
    takes the dimension of the LAST such entry in ledger (file) order. The ledger is
    append-only and in decision order, so the most recent attribution is the one the run
    settled on; a tie broken by dict order or by sorting would give two readers of the same
    file different answers.
    """
    mapping: dict[str, str] = {}
    for dimension, operation_id in _attributed_entries(project_root):
        if operation_id is not None:
            mapping[operation_id] = dimension
    return mapping


def echoed_dimension_map(project_root: str | Path) -> dict[str, str]:
    """Operation id → the dimension the shell echoed on the call that answered for it.

    Read out of the ledger's own ``reverification`` entries, because that is the one place this
    harness holds the engine's answer and the shell's declaration for the same operation
    together — and it is a citation of what came back, not a new claim: an answer with no echo
    contributes nothing here, which is what makes "the other surface declares in metadata" read
    as silence rather than as disagreement.
    """
    from .reverify_trigger import LEDGER_DATA_KIND

    echoes: dict[str, str] = {}
    for entry in decision_log.read_entries(project_root):
        data: Mapping[str, Any] = entry.data if isinstance(entry.data, dict) else {}
        if data.get("kind") != LEDGER_DATA_KIND:
            continue
        answers = data.get("answers")
        if not isinstance(answers, list):
            continue
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            operation_id = answer.get(OPERATION_ID_DATA_KEY)
            echoed = answer.get(ECHOED_DIMENSION_DATA_KEY)
            if is_valid_operation_id(operation_id) and is_valid_dimension_id(echoed):
                echoes[str(operation_id)] = str(echoed)
    return echoes


def contested_attributions(project_root: str | Path) -> list[dict[str, str]]:
    """Decisions whose declared dimension the shell's own echo contradicts.

    WHY THIS EXISTS AND WHY IT ONLY SUBTRACTS. A ledger attribution is the agent saying "this
    call served dimension X"; the echo is the shell saying what the call it just answered was
    declared as. When the two disagree, the harness has no basis to keep the more flattering
    reading, and it has no mandate to decide which one is true either — the kernel decides
    coverage from what was *recorded*, so the only honest action is to withhold the contested
    attribution and say how many were withheld. Nothing here ever adds a verification, and a
    missing echo is not a contest: silence leaves the attribution standing.
    """
    echoes = echoed_dimension_map(project_root)
    contested: list[dict[str, str]] = []
    for dimension, operation_id in _attributed_entries(project_root):
        if operation_id is None:
            continue
        echoed = echoes.get(operation_id)
        if echoed is not None and echoed != dimension:
            contested.append(
                {
                    DIMENSION_DATA_KEY: dimension,
                    OPERATION_ID_DATA_KEY: operation_id,
                    ECHOED_DIMENSION_DATA_KEY: echoed,
                }
            )
    return contested


def _uncontested_counts(project_root: str | Path) -> tuple[dict[str, int], list[dict[str, str]]]:
    """Per-dimension decision counts with contested attributions removed, plus what was removed."""
    contested = contested_attributions(project_root)
    withheld = {(item[DIMENSION_DATA_KEY], item[OPERATION_ID_DATA_KEY]) for item in contested}
    counts: dict[str, int] = {}
    for dimension, operation_id in _attributed_entries(project_root):
        if operation_id is not None and (dimension, operation_id) in withheld:
            continue
        counts[dimension] = counts.get(dimension, 0) + 1
    return counts, contested


def dimension_context_with_contested(
    project_root: str | Path, *, fallback: Sequence[str] | None = None
) -> tuple[DimensionContext | None, list[dict[str, str]]]:
    """One accounting pass: the kernel's verdict and the attributions it withheld.

    Returned together because they must come from the SAME read. Two passes would let a writer
    that appends between them produce a verdict and a count that describe different ledgers —
    and a count that does not describe the verdict it is printed under is worse than no count.
    """
    planned = planned_dimension_ids(project_root, fallback=fallback)
    if not planned:
        return None, []
    counts, contested = _uncontested_counts(project_root)
    if contested:
        log.info(
            "iterate dimension coverage withholds %d contested attribution(s): %s",
            len(contested),
            ", ".join(
                f"{item[OPERATION_ID_DATA_KEY]} declared {item[DIMENSION_DATA_KEY]!r} "
                f"while the shell echoed {item[ECHOED_DIMENSION_DATA_KEY]!r}"
                for item in contested
            ),
        )
    recorded = {dimension: {"decisions": count} for dimension, count in counts.items()}
    try:
        return dimension_context(planned, recorded), contested
    except DimensionContextError as error:
        # A malformed id on either side is a producer bug; the run keeps working, but this
        # verdict is not printed rather than printed wrong.
        log.warning("iterate dimension coverage could not be computed: %s", error)
        return None, contested


def project_dimension_context(
    project_root: str | Path, *, fallback: Sequence[str] | None = None
) -> DimensionContext | None:
    """The kernel's verdict for this project, or ``None`` when either half cannot be built.

    ``None`` is the honest answer and it is not the same answer as an all-zero context: a
    caller that ordered compaction on an invented verdict would be clearing evidence the
    kernel never said was verified. Contested attributions are already out of it — see
    :func:`dimension_context_with_contested` for the version that also says how many.
    """
    built, _contested = dimension_context_with_contested(project_root, fallback=fallback)
    return built
