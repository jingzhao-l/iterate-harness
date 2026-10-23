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
#: entry: a citation beside the answer, kept verbatim in the annotations too, and read by no
#: verdict here on purpose — see :func:`echo_contestation` for why the judgement is taken from
#: the witnessed call rather than from these rows. Named here so writer and any future reader
#: share one key.
ECHOED_DIMENSION_DATA_KEY = "echoedDimension"

#: Where the MCP adapter parks the declarations it witnessed, in the run's tool metadata — the
#: same durable channel ``iterate_state`` uses, so a later ``iterate_decision_log`` call in this
#: session can be judged against the call that came before it.
ECHO_METADATA_KEY = "glasspane_echoed_dimensions"

#: How many witnessed declarations the stash keeps. Bounded because this dict is folded into
#: every following turn, and an unbounded one grows with the session.
ECHO_METADATA_CAP = 64

#: Refusal code for a decision whose declared dimension contradicts the witnessed echo. The
#: shell owns the shape refusal (``GP_E_DIMENSION``); this one is about a claim that was
#: well-formed and still does not match the call it cites, so it is the harness's own.
DIMENSION_CONTEST_CODE = "KERNEL_E_DIMENSION_CONTEST"


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


def record_observed_echo(
    metadata: dict[str, Any] | None, operation_id: object, echoed: object
) -> None:
    """Write down what the shell said a call was declared for, keyed by the operation it answered.

    Called by the MCP adapter on the reply it is about to hand the model, so the witness is
    contemporaneous: what the shell echoed *at the time of the call*, not what anyone remembers
    later. Absent on purpose when either half is missing or malformed — an echo without an
    operation, or an operation without an echo, is nothing anybody may act on.

    The stash is bounded and lives in the run's tool metadata (the same channel
    ``iterate_state`` travels on), because a long session must not grow an unbounded dict that
    gets folded into every following turn.

    One stated limit, in the safe direction. Two MCP calls in the SAME turn both start from a
    shallow copy with no witness dict, so each builds its own; the engine's fold therefore
    **merges** this one key instead of replacing it (see ``engine/query.py``), and both siblings'
    witnesses survive. A sibling that overflows the cap can still evict the oldest entry — the
    failure mode is a missed contest, never a wrong one, and nothing here can refuse a decision
    that agrees with what was witnessed.
    """
    if metadata is None or not is_valid_operation_id(operation_id):
        return
    if not is_valid_dimension_id(echoed):
        return
    stash = metadata.get(ECHO_METADATA_KEY)
    if not isinstance(stash, dict):
        stash = {}
        metadata[ECHO_METADATA_KEY] = stash
    stash[str(operation_id)] = str(echoed)
    while len(stash) > ECHO_METADATA_CAP:
        # Insertion order is arrival order; dropping the oldest keeps the window on the calls
        # this round can still cite.
        stash.pop(next(iter(stash)))


def echo_contestation(
    *, metadata: object, dimension: object, operation_id: object
) -> dict[str, str] | None:
    """The refusal payload when the ledger's claim contradicts what the shell echoed.

    ``None`` — no objection — in every case this module cannot measure: no stash, no observation
    for that operation, or a declaration that agrees. A mismatch means the decision being
    recorded now cites an operation whose evidence was fetched under a *different* declared
    dimension, which is the ledger renaming what a session claims it covered.

    Why the judgement sits here and not over the ledger's own rows: the only sender that could
    put an echo into a reverification row is scenario ①'s re-verification, and ① can declare a
    dimension for an operation only by quoting the very attribution the check would test — the
    request carries no per-operation dimension, and inventing one would make the comparison
    circular. Here the two sides are genuinely independent: the shell witnessed the call, the
    agent is now making a claim about it.

    Nothing here decides which side is true, and nothing edits the earlier call. It refuses the
    new write and names the two ways forward, because a harness that quietly kept the more
    flattering attribution would be the thing being lied to.

    And please read this before "fixing the asymmetry" on the other surface: the fork's plugin
    (``packages/opencode/src/plugin/glasspane-decision-log.ts``) writes its decision from the
    evidence frame in the reply it just handled, and its dimension comes from that same call's
    tool metadata. Declaration and record are one source there, so there is no second witness to
    disagree — a check copied onto that side would compare a value with itself. The contest is
    meaningful exactly where two moments exist: a call witnessed now, a claim written later.
    """
    if not isinstance(metadata, dict):
        return None
    if not is_valid_dimension_id(dimension) or not is_valid_operation_id(operation_id):
        return None
    stash = metadata.get(ECHO_METADATA_KEY)
    if not isinstance(stash, dict):
        return None
    observed = stash.get(str(operation_id))
    if not is_valid_dimension_id(observed) or observed == dimension:
        return None
    return {
        "error": (
            f"dimension {dimension!r} was declared for {operation_id}, but the shell answered "
            f"that call under {observed!r}"
        ),
        "code": DIMENSION_CONTEST_CODE,
        "remedy": (
            "one of the two is wrong and the ledger cannot hold both: record the decision under "
            f"{observed!r} (the declaration the evidence was actually fetched for), or re-read it "
            f"with gp_last_evidence {{operationId: {operation_id!r}, dimension: {dimension!r}}} "
            "and cite that call instead. Do not edit the earlier call's declaration — it is the "
            "witness this check just used"
        ),
    }


def project_dimension_context(
    project_root: str | Path, *, fallback: Sequence[str] | None = None
) -> DimensionContext | None:
    """The kernel's verdict for this project, or ``None`` when either half cannot be built.

    ``None`` is the honest answer and it is not the same answer as an all-zero context: a
    caller that ordered compaction on an invented verdict would be clearing evidence the
    kernel never said was verified.
    """
    planned = planned_dimension_ids(project_root, fallback=fallback)
    if not planned:
        return None
    recorded = {
        dimension: {"decisions": count}
        for dimension, count in recorded_dimension_counts(project_root).items()
    }
    try:
        return dimension_context(planned, recorded)
    except DimensionContextError as error:
        # A malformed id on either side is a producer bug; the run keeps working, but this
        # verdict is not printed rather than printed wrong.
        log.warning("iterate dimension coverage could not be computed: %s", error)
        return None
