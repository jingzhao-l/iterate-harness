"""Produce the run plan the shared kernel's dimension contracts are read against.

Why this half exists. ``dimensionContext`` (kernel) accounts a *plan* against what was
recorded and refuses to invent a plan — correct for an accountant, useless with no
source. The plan has lived only inside ``iterate.config.yaml``: the GlassPane harness
cannot read that file, so its compaction hook renders "0/N verified" for a run that
verified most of the plan. This module turns the config we own into the contract the
other side reads, and writes it where they can pick it up.

The digest is a *third* implementation of the same rule (Python here, TypeScript in the
kernel, Swift elsewhere), pinned against ``tests/kernel_fixtures/run-plan.ok-01.json``
by ``test_run_plan.py``. Deliberately not a call into node: an agreement enforced by
delegation proves nothing about the path that actually runs in production here.

What this module will not do: read the plan back to decide a verdict (judgement stays
in the engine), sort the dimensions (planned order is the reviewer's, and sorting it
would make two different plans digest identically), or invent a scope's focus text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

RUN_PLAN_SCHEMA_VERSION = "iterate.run-plan/0.1"

#: Same limits as the kernel's dimension contract. Restated as *numbers* rather
#: imported from YAML, because the contract's owner is the kernel and the fixture
#: comparison is what keeps these honest — a silent divergence fails a test here.
DIMENSION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DIMENSION_ID_MAX_LENGTH = 64
DIMENSION_LABEL_MAX_LENGTH = 256
DIMENSION_FOCUS_MAX_LENGTH = 4096
MIN_PLANNED = 1
MAX_PLANNED = 64

DIGEST_PREFIX = "rp_"

#: Where the GlassPane engine owns state. A plan file inside it would be a second
#: writer in somebody else's directory — the defect the ledger rule already refuses.
ENGINE_STATE_ROOTS = (".glasspane",)


class RunPlanError(Exception):
    """A plan that cannot be built, digested or written, with a code and a remedy."""

    def __init__(self, code: str, message: str, remedy: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.remedy = remedy


@dataclass(frozen=True)
class PlannedDimension:
    """One planned review dimension, in the caller's order."""

    id: str
    label: str | None = None
    focus: str | None = None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"id": self.id}
        if self.label is not None:
            out["label"] = self.label
        if self.focus is not None:
            out["focus"] = self.focus
        return out


@dataclass(frozen=True)
class WrittenRunPlan:
    """Where the plan went, and the digest the ledger should record alongside it.

    The digest is returned because a caller that writes a plan almost always needs to
    cite it — a decision entry that says "this run used plan X" is only checkable if
    the writer kept X's digest.
    """

    path: Path
    digest: str


def _canonical(value: object) -> str:
    """The canonical JSON form the kernel's ``canonicalJson`` describes.

    Keys sorted in UTF-16 code unit order, no insignificant whitespace, arrays in the
    caller's order. Integers and booleans only appear here; the shortest-roundtrip
    number question is settled by the kernel's own canonical-JSON cases, not by us.
    """
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    if isinstance(value, Mapping):
        keys = [str(k) for k in value]
        # UTF-16 code-unit order: Python sorts str by code point, which differs only
        # above the BMP. Keys here are ASCII ids and field names; anything else is
        # refused below rather than silently ordered differently.
        for key in keys:
            if any(ord(ch) > 0xFFFF for ch in key):
                raise RunPlanError(
                    "RUN_PLAN_E_KEY_ORDER",
                    f"key {key!r} contains a non-BMP character; the two implementations order it differently",
                    "keep plan keys in the ASCII/BMP range",
                )
        return "{" + ",".join(
            f"{json.dumps(k, ensure_ascii=False)}:{_canonical(value[k])}" for k in sorted(keys)
        ) + "}"
    raise RunPlanError(
        "RUN_PLAN_E_VALUE",
        f"cannot canonicalise a {type(value).__name__} in a run plan",
        "keep plan values to strings, booleans, null and integers",
    )


def run_plan_digest(plan: Mapping[str, object]) -> str:
    """``rp_`` + sha256 over the plan's canonical form."""
    return DIGEST_PREFIX + hashlib.sha256(_canonical(plan).encode("utf-8")).hexdigest()


def _validate_id(value: object, index: int) -> str:
    if not isinstance(value, str):
        raise RunPlanError(
            "RUN_PLAN_E_ID",
            f"planned dimension {index} has a non-string id",
            "ids are lowercase kebab tokens",
        )
    if len(value) > DIMENSION_ID_MAX_LENGTH:
        raise RunPlanError(
            "RUN_PLAN_E_ID",
            f"planned dimension {index} id exceeds {DIMENSION_ID_MAX_LENGTH} characters",
            "shorten the id",
        )
    if not DIMENSION_ID_PATTERN.match(value):
        raise RunPlanError(
            "RUN_PLAN_E_ID",
            f"planned dimension {index} id {value!r} is not a lowercase kebab token",
            "ids match ^[a-z0-9]+(?:-[a-z0-9]+)*$",
        )
    return value


def build_run_plan(
    config: Mapping[str, object],
    *,
    scopes: Sequence[str] = (),
    source_name: str | None = None,
) -> dict[str, object]:
    """Read the iterate config into a run plan.

    ``scopes`` selects ``dimension_sets`` entries (a reviewer told "the engine layer"
    passes ``("engine",)``; a cross-layer change passes several). The union is ordered
    by the top-level ``dimensions`` list, and any id a scope adds that the base list
    does not carry is appended in the scope's own order — never alphabetically, so the
    plan a human wrote stays the plan the digest covers.
    """
    base_raw = config.get("dimensions")
    if not isinstance(base_raw, list) or not base_raw:
        raise RunPlanError(
            "RUN_PLAN_E_NO_DIMENSIONS",
            "the config carries no dimensions list to plan from",
            "set `dimensions:` in iterate.config.yaml, or pass a scope with its own list",
        )
    base = [str(item) for item in base_raw]

    sets_raw = config.get("dimension_sets")
    sets: Mapping[str, object] = sets_raw if isinstance(sets_raw, Mapping) else {}

    ordered: list[str] = []

    def note(id_: str) -> None:
        if id_ not in ordered:
            ordered.append(id_)

    for scope in scopes:
        entry = sets.get(scope)
        if not isinstance(entry, Mapping):
            raise RunPlanError(
                "RUN_PLAN_E_SCOPE_UNKNOWN",
                f"no dimension set {scope!r} in the config",
                f"declared sets: {', '.join(sorted(str(k) for k in sets)) or '(none)'}",
            )
        members = entry.get("dimensions")
        if isinstance(members, list):
            for member in members:
                note(str(member))
    for id_ in base:
        note(id_)

    if not scopes:
        ordered = list(base)

    if len(ordered) > MAX_PLANNED:
        raise RunPlanError(
            "RUN_PLAN_E_LIMIT",
            f"{len(ordered)} planned dimensions exceeds the contract's {MAX_PLANNED}",
            "narrow the scopes or the config's dimensions list",
        )

    focus_by_id: dict[str, str] = {}
    for scope in scopes:
        entry = sets.get(scope)
        focus = entry.get("focus") if isinstance(entry, Mapping) else None
        if not isinstance(focus, Mapping):
            continue
        for id_, text in focus.items():
            # First scope wins: two scopes both describing one dimension is a
            # contradiction in the config, and joining the texts would invent a third
            # instruction nobody wrote.
            if isinstance(text, str) and str(id_) not in focus_by_id:
                focus_by_id[str(id_)] = text

    dimensions: list[PlannedDimension] = []
    for index, id_ in enumerate(ordered):
        checked = _validate_id(id_, index)
        text = focus_by_id.get(checked)
        if text is not None and len(text) > DIMENSION_FOCUS_MAX_LENGTH:
            raise RunPlanError(
                "RUN_PLAN_E_FOCUS_LENGTH",
                f"focus for {checked!r} exceeds {DIMENSION_FOCUS_MAX_LENGTH} characters",
                "shorten the focus text in the config",
            )
        dimensions.append(PlannedDimension(id=checked, focus=text))

    if len(dimensions) < MIN_PLANNED:  # pragma: no cover - guarded above by the empty-list refusal
        raise RunPlanError("RUN_PLAN_E_LIMIT", "a plan needs at least one dimension", "declare dimensions")

    plan: dict[str, object] = {
        "schemaVersion": RUN_PLAN_SCHEMA_VERSION,
        "dimensions": [dimension.to_json() for dimension in dimensions],
        "source": (
            {"kind": "config", "name": source_name}
            if source_name
            else {"kind": "config"}
        ),
    }
    return plan


def write_run_plan(plan: Mapping[str, object], path: str | Path) -> WrittenRunPlan:
    """Write the plan for the other harness to read, and return where and what digest.

    Refusals rather than fallbacks, all three: a relative path resolves against
    whatever directory a process happened to start in, a directory that cannot be
    created is not something to pretend about, and the engine owns its state root.
    The digest is computed *before* anything is written, so a plan that cannot be
    canonicalised never leaves a half-written file where the other side will look.
    """
    target = Path(path)
    if not target.is_absolute():
        raise RunPlanError(
            "RUN_PLAN_E_PATH",
            f"the run-plan path must be absolute, got {str(target)!r}",
            "pass an absolute path (this mirrors the ledger's rule)",
        )
    if any(part in ENGINE_STATE_ROOTS for part in target.parts):
        raise RunPlanError(
            "RUN_PLAN_E_STATE_ROOT",
            f"{target} is inside an engine state root",
            "choose a directory this harness owns; the engine owns ~/.glasspane",
        )
    digest = run_plan_digest(plan)
    payload = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RunPlanError(
            "RUN_PLAN_E_WRITE",
            f"cannot create {target.parent}: {error.strerror or error}",
            "point the run plan at a writable directory",
        ) from error
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, payload.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return WrittenRunPlan(path=target, digest=digest)


def label_within_limit(label: str) -> str:
    """Validate a label the caller supplied (this module never generates one)."""
    if len(label) > DIMENSION_LABEL_MAX_LENGTH:
        raise RunPlanError(
            "RUN_PLAN_E_LABEL_LENGTH",
            f"label exceeds {DIMENSION_LABEL_MAX_LENGTH} characters",
            "shorten the label",
        )
    return label
