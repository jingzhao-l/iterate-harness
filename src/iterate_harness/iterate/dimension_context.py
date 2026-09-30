"""Dimension context: the Python half of the kernel's ``dimensionContext``.

This module is deliberately the *second* implementation of the contract in
``@iterate/kernel``'s ``fixtures/dimension-context.ok-01.json`` rather than a
wrapper around it. The harness is Python and the kernel is TypeScript; the only
thing the two can share without a build step is the fixture. So the rules are
implemented here from the fixture's rules paragraph, and
``tests/test_iterate/test_dimension_context.py`` asserts this implementation
reproduces the fixture byte for byte.

Why duplicate five rules instead of shelling out to node? Because the value is
that both sides *cannot drift*: if either implementation changes the arithmetic
and the other does not, the fixture comparison fails in one of the two repos'
CI. A wrapper would make them agree by construction, but would not prove the
harness's own Python path — the one that actually runs in production here —
matches the published contract.

THE VOCABULARY IS NOT HERE. The nine dimension ids are owned by
``config/dimensions.yaml`` and locked across six sources by
``tests/test_dimension_lock.py`` in the skill repo. This module validates their
*shape* and never enumerates them; the caller passes the ids it was configured
with. Adding a tenth dimension to the config must not require editing this file,
and a typo'd id must still be refused rather than quietly becoming a new
dimension.

The rules, restated so this file stands on its own:

1. Planned entries keep the caller's order. Status is ``verified`` when the
   recorded decision count is >= 1, else ``unverified``. An id absent from
   ``recorded`` is the same fact as a recorded zero, and normalises to
   ``{"decisions": 0}``.
2. A recorded id that was never planned becomes one ``unplanned`` entry, sorted
   by id ascending. This is the case that must not be dropped: the run checked
   something it never declared.
3. ``totals["decisions"]`` sums planned and unplanned alike, each decision
   counted once. ``verified + unverified == planned`` always holds.
4. A duplicate planned id raises rather than collapsing silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence

#: Kebab-case, the shape every canonical id already has. Validated, not
#: enumerated — see the module docstring.
DIMENSION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

DIMENSION_ID_MAX_LENGTH = 64
DIMENSION_LABEL_MAX_LENGTH = 256
DIMENSION_FOCUS_MAX_LENGTH = 4096
DIMENSION_MIN_PLANNED = 1
DIMENSION_MAX_PLANNED = 64
DIMENSION_MAX_OPERATION_IDS = 32

DimensionStatus = Literal["verified", "unverified", "unplanned"]

DIMENSION_E_DUPLICATE = "KERNEL_E_DIMENSION_DUPLICATE"
DIMENSION_E_EMPTY = "KERNEL_E_DIMENSION_EMPTY"
DIMENSION_E_ID = "KERNEL_E_DIMENSION_ID"


class DimensionContextError(ValueError):
    """A dimension context could not be built.

    Carries ``code``/``dimension``/``remedy`` to match the kernel's
    ``KernelDimensionError`` and the engine's error frames: whoever catches this
    must be able to act on it without reading the source.
    """

    def __init__(self, code: str, message: str, remedy: str, dimension: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.remedy = remedy
        self.dimension = dimension


def _check_id(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise DimensionContextError(
            DIMENSION_E_ID, "a dimension id must be a non-empty string", "pass the id from the config"
        )
    if len(value) > DIMENSION_ID_MAX_LENGTH:
        raise DimensionContextError(
            DIMENSION_E_ID,
            f"dimension id {value!r} is longer than {DIMENSION_ID_MAX_LENGTH} characters",
            "shorten the id in the dimension config",
            value,
        )
    if not DIMENSION_ID_PATTERN.match(value):
        raise DimensionContextError(
            DIMENSION_E_ID,
            f"dimension id {value!r} is not lower-case kebab-case",
            "ids come from config/dimensions.yaml; fix it there rather than renaming it at the call site",
            value,
        )


@dataclass(frozen=True)
class PlannedDimension:
    """One dimension the run declared it would review."""

    id: str
    label: str | None = None
    focus: str | None = None

    def __post_init__(self) -> None:
        _check_id(self.id)


@dataclass(frozen=True)
class DimensionEvidence:
    """What the engine recorded for one dimension.

    ``decisions`` is a count rather than a list of hashes on purpose: this
    structure exists to survive compaction, and a list is the thing compaction
    drops.
    """

    decisions: int = 0
    operation_ids: tuple[str, ...] | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"decisions": self.decisions}
        if self.operation_ids is not None:
            out["operationIds"] = list(self.operation_ids)
        return out

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "DimensionEvidence":
        ids = raw.get("operationIds")
        decisions = int(raw.get("decisions", 0))
        if decisions < 0:
            raise DimensionContextError(
                DIMENSION_E_EMPTY,
                f"a recorded decision count cannot be negative (got {decisions})",
                "this is a count of ledger entries; a negative value means the producer is wrong",
            )
        if ids is not None and len(ids) > DIMENSION_MAX_OPERATION_IDS:
            raise DimensionContextError(
                DIMENSION_E_EMPTY,
                f"at most {DIMENSION_MAX_OPERATION_IDS} operation ids may be carried per dimension",
                "the cap exists so this structure survives compaction; keep counts, not histories",
            )
        return cls(decisions=decisions, operation_ids=tuple(ids) if ids is not None else None)


@dataclass(frozen=True)
class DimensionContextEntry:
    id: str
    status: DimensionStatus
    evidence: DimensionEvidence
    label: str | None = None
    focus: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "status": self.status}
        if self.label is not None:
            out["label"] = self.label
        if self.focus is not None:
            out["focus"] = self.focus
        out["evidence"] = self.evidence.to_json()
        return out


@dataclass(frozen=True)
class DimensionContext:
    """The result. ``to_json`` is what the fixture compares against."""

    planned: tuple[DimensionContextEntry, ...] = field(default_factory=tuple)
    unplanned: tuple[DimensionContextEntry, ...] = field(default_factory=tuple)
    totals: Mapping[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "planned": [entry.to_json() for entry in self.planned],
            "unplanned": [entry.to_json() for entry in self.unplanned],
            "totals": dict(self.totals),
        }


def dimension_context(
    planned: Sequence[PlannedDimension | str],
    recorded: Mapping[str, DimensionEvidence | Mapping[str, Any]] | None = None,
) -> DimensionContext:
    """Set the plan against what was recorded. See the module docstring for the rules."""
    entries = [PlannedDimension(item) if isinstance(item, str) else item for item in planned]
    if len(entries) < DIMENSION_MIN_PLANNED:
        raise DimensionContextError(
            DIMENSION_E_EMPTY,
            "a dimension context needs at least one planned dimension",
            "an empty plan cannot distinguish 'reviewed nothing' from 'never started'",
        )
    if len(entries) > DIMENSION_MAX_PLANNED:
        raise DimensionContextError(
            DIMENSION_E_EMPTY,
            f"a dimension context accepts at most {DIMENSION_MAX_PLANNED} planned dimensions",
            "split the run; a plan this wide is not reviewable anyway",
        )

    seen: set[str] = set()
    for entry in entries:
        if entry.id in seen:
            raise DimensionContextError(
                DIMENSION_E_DUPLICATE,
                f"dimension {entry.id!r} is planned more than once",
                "give each planned dimension exactly one entry; the totals are computed from this list",
                entry.id,
            )
        seen.add(entry.id)

    def as_evidence(value: DimensionEvidence | Mapping[str, Any]) -> DimensionEvidence:
        return value if isinstance(value, DimensionEvidence) else DimensionEvidence.from_json(value)

    recorded_map: dict[str, DimensionEvidence] = {
        key: as_evidence(value) for key, value in (recorded or {}).items()
    }
    for key in recorded_map:
        _check_id(key)

    planned_entries: list[DimensionContextEntry] = []
    for entry in entries:
        evidence = recorded_map.get(entry.id, DimensionEvidence(decisions=0))
        planned_entries.append(
            DimensionContextEntry(
                id=entry.id,
                status="verified" if evidence.decisions >= 1 else "unverified",
                evidence=evidence,
                label=entry.label,
                focus=entry.focus,
            )
        )

    # `sorted()` on str is code-point order; TypeScript's `.sort()` is UTF-16
    # code-unit order. The two agree for every id matching DIMENSION_ID_PATTERN
    # (all BMP, all below U+10000), which is asserted in the tests.
    unplanned_entries = [
        DimensionContextEntry(id=key, status="unplanned", evidence=evidence)
        for key, evidence in sorted(recorded_map.items())
        if key not in seen
    ]

    verified = sum(1 for entry in planned_entries if entry.status == "verified")
    decisions = sum(entry.evidence.decisions for entry in planned_entries + unplanned_entries)

    return DimensionContext(
        planned=tuple(planned_entries),
        unplanned=tuple(unplanned_entries),
        totals={
            "planned": len(planned_entries),
            "verified": verified,
            "unverified": len(planned_entries) - verified,
            "unplanned": len(unplanned_entries),
            "decisions": decisions,
        },
    )


def format_dimension_context(context: DimensionContext) -> str:
    """The one line a compaction summary or report header can carry.

    Names the unverified ids, because a bare "2 unverified" is a number nobody
    can act on. A run where nothing was verified still produces a sentence —
    that line is the one worth printing.
    """
    totals = context.totals
    parts = [f"{totals['verified']}/{totals['planned']} dimensions verified"]
    if totals["unverified"] > 0:
        ids = [entry.id for entry in context.planned if entry.status == "unverified"]
        parts.append(f"{totals['unverified']} unverified ({', '.join(ids)})")
    if totals["unplanned"] > 0:
        ids = [entry.id for entry in context.unplanned]
        parts.append(f"{totals['unplanned']} unplanned ({', '.join(ids)})")
    noun = "decision" if totals["decisions"] == 1 else "decisions"
    parts.append(f"{totals['decisions']} {noun}")
    return ", ".join(parts)


def dimension_context_from_evidence(
    planned: Sequence[PlannedDimension | str],
    evidence_packs: Iterable[Mapping[str, Any]],
    *,
    dimension_key: str = "dimension",
) -> DimensionContext:
    """Build a context from evidence packs that each name their dimension.

    This is the adapter the harness actually calls: the engine emits packs, the
    run knows what it planned, and this is where the two meet. A pack with no
    ``dimension`` key is *not* guessed at — it contributes to no dimension and is
    therefore invisible to the context, which is exactly why the totals are
    computed from what was recorded rather than from what was emitted.
    """
    recorded: dict[str, DimensionEvidence] = {}
    for pack in evidence_packs:
        dimension = pack.get(dimension_key)
        if not isinstance(dimension, str):
            continue
        _check_id(dimension)
        current = recorded.get(dimension, DimensionEvidence(decisions=0))
        operation_id = pack.get("operationId")
        ids = list(current.operation_ids or ())
        if isinstance(operation_id, str) and operation_id and operation_id not in ids:
            if len(ids) >= DIMENSION_MAX_OPERATION_IDS:
                ids = ids[-(DIMENSION_MAX_OPERATION_IDS - 1) :]
            ids.append(operation_id)
        recorded[dimension] = DimensionEvidence(
            decisions=current.decisions + 1,
            operation_ids=tuple(ids) if ids else None,
        )
    return dimension_context(planned, recorded)
