"""Skill↔harness dimension-system consistency doctor.

The iterate ecosystem defines its 9 review dimensions in SIX places: the
skill's ``config/dimensions.yaml`` (canonical), the JSON-schema enum, the
skill wizard / harness wizard constants, the harness ``ALL_DIMENSIONS``
list, the harness default config, and every project's
``iterate.config.yaml`` references (enabled list, resources, thresholds,
personalization). Any of those can drift.

This module bundles the canonical definitions (``data/dimensions.yaml``,
byte-identical to the skill's file) and checks every OTHER source against
it — defensively, never raising — so ``ih iterate doctor`` can answer
"are my dimensions consistent?" in one command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config_loader import load_effective_config
from .types import IterateConfig

#: Bundled canonical definitions (byte-identical to skill config/dimensions.yaml).
DIMENSIONS_DATA_PATH = Path(__file__).parent / "data" / "dimensions.yaml"

#: Fields every canonical dimension definition must carry.
REQUIRED_DEFINITION_FIELDS = ("name", "name_en", "priority", "focus")

#: Allowed priority values in canonical definitions.
VALID_PRIORITIES = ("critical", "high", "medium", "low")


@dataclass
class DimensionDefinition:
    """One canonical dimension entry from the bundled yaml."""

    key: str
    name: str
    name_en: str
    priority: str
    focus: str


@dataclass
class DoctorCheckLine:
    """One rendered check outcome (``status=None`` renders as info)."""

    status: bool | None
    text: str


@dataclass
class DimensionDoctorReport:
    """Result of :func:`run_dimension_doctor`.

    ``ok`` is True only when the canonical data loads cleanly, the harness
    internal constants match it, and the project config contains no
    dangling dimension references.
    """

    canonical_order: list[str] = field(default_factory=list)
    definitions: dict[str, DimensionDefinition] = field(default_factory=dict)
    config_source: str = "defaults"
    enabled_dimensions: list[str] = field(default_factory=list)
    checks: list[DoctorCheckLine] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [line.text for line in self.checks if line.status is False]

    @property
    def warnings(self) -> list[str]:
        return [line.text for line in self.checks if line.status is None]

    @property
    def ok(self) -> bool:
        return not self.errors


def load_canonical_dimensions() -> tuple[dict[str, DimensionDefinition], list[str]]:
    """Load the bundled canonical definitions defensively.

    Returns ``(definitions, errors)``; every failure mode (missing file,
    unparseable yaml, non-mapping root, malformed entry) is reported as an
    error string instead of raising.
    """
    errors: list[str] = []
    try:
        raw = yaml.safe_load(DIMENSIONS_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {}, [f"canonical dimensions file unreadable ({DIMENSIONS_DATA_PATH.name}): {exc}"]
    if not isinstance(raw, dict):
        return {}, [f"canonical dimensions root must be a mapping, got {type(raw).__name__}"]

    definitions: dict[str, DimensionDefinition] = {}
    for key, value in raw.items():
        dim = str(key)
        if not isinstance(value, dict):
            errors.append(f"canonical dimension '{dim}' must be a mapping")
            continue
        missing = [f for f in REQUIRED_DEFINITION_FIELDS if not value.get(f)]
        if missing:
            errors.append(f"canonical dimension '{dim}' missing field(s): {', '.join(missing)}")
            continue
        priority = str(value["priority"])
        if priority not in VALID_PRIORITIES:
            errors.append(
                f"canonical dimension '{dim}' has invalid priority '{priority}' "
                f"(expected one of {', '.join(VALID_PRIORITIES)})"
            )
            continue
        definitions[dim] = DimensionDefinition(
            key=dim,
            name=str(value["name"]),
            name_en=str(value["name_en"]),
            priority=priority,
            focus=str(value["focus"]),
        )
    return definitions, errors


def _check_internal_constants(
    definitions: dict[str, DimensionDefinition],
    checks: list[DoctorCheckLine],
) -> None:
    """Verify harness-internal dimension constants against the canonical set."""
    from . import personalize_cmd

    canonical_order = list(definitions)
    if personalize_cmd.ALL_DIMENSIONS == canonical_order:
        checks.append(
            DoctorCheckLine(
                True, f"internal ALL_DIMENSIONS matches canonical ({len(canonical_order)}, same order)"
            )
        )
    else:
        checks.append(
            DoctorCheckLine(
                False,
                "internal ALL_DIMENSIONS drifts from bundled canonical: "
                f"harness={personalize_cmd.ALL_DIMENSIONS} canonical={canonical_order}",
            )
        )

    default_dims = set(IterateConfig().dimensions)
    if default_dims == set(canonical_order):
        checks.append(
            DoctorCheckLine(True, f"default config dimensions match canonical ({len(default_dims)})")
        )
    else:
        checks.append(
            DoctorCheckLine(
                False,
                "default config dimensions drift from canonical: "
                f"defaults={sorted(default_dims)} canonical={sorted(canonical_order)}",
            )
        )


def _check_enabled_dimensions(
    config: IterateConfig,
    canonical: set[str],
    checks: list[DoctorCheckLine],
) -> None:
    """Enabled dimensions must all be canonical keys."""
    unknown = [d for d in config.dimensions if d not in canonical]
    if unknown:
        checks.append(
            DoctorCheckLine(
                False,
                f"dimensions: unknown dimension(s) {unknown} — not in canonical set "
                f"{sorted(canonical)} (typo or skill/harness drift?)",
            )
        )
    else:
        checks.append(
            DoctorCheckLine(True, f"dimensions: all {len(config.dimensions)} enabled are canonical")
        )
    not_enabled = sorted(canonical - set(config.dimensions))
    if not_enabled:
        checks.append(
            DoctorCheckLine(
                None, f"canonical dimensions not enabled (informational): {not_enabled}"
            )
        )


def _check_keyed_references(
    label: str,
    keys: list[str],
    enabled: set[str],
    canonical: set[str],
    checks: list[DoctorCheckLine],
) -> None:
    """Check one keyed dimension-reference mapping (resources / thresholds)."""
    if not keys:
        checks.append(DoctorCheckLine(True, f"{label}: none configured"))
        return
    unknown = sorted(set(keys) - canonical)
    if unknown:
        checks.append(
            DoctorCheckLine(False, f"{label}: unknown dimension(s) {unknown} — not in canonical set")
        )
    disabled = sorted(set(keys) - set(unknown) - enabled)
    if disabled:
        checks.append(
            DoctorCheckLine(
                None, f"{label}: {disabled} configured but not in enabled dimensions (inert)"
            )
        )
    if not unknown and not disabled:
        checks.append(DoctorCheckLine(True, f"{label}: all {len(keys)} key(s) resolve"))


def _check_personalization(
    config: IterateConfig,
    enabled: set[str],
    checks: list[DoctorCheckLine],
) -> None:
    """Personalization dimension references must sit inside the enabled set.

    Mirrors the skill's ``scripts/validate.py`` semantics: a
    ``fix_priority_order`` / ``dimension_focus`` / ``known_intentional``
    entry pointing at a dimension that is not enabled can never fire, so
    it is reported as an error (likely a stale entry after the project
    narrowed its dimensions).
    """
    personalization = config.personalization
    if not isinstance(personalization, dict) or not personalization:
        checks.append(DoctorCheckLine(True, "personalization: no dimension references"))
        return

    dangling: list[str] = []
    for index, dim in enumerate(personalization.get("fix_priority_order") or []):
        if isinstance(dim, str) and dim not in enabled:
            dangling.append(f"fix_priority_order[{index}]='{dim}'")
    for index, item in enumerate(personalization.get("dimension_focus") or []):
        if isinstance(item, dict) and item.get("dimension") not in enabled:
            dangling.append(f"dimension_focus[{index}]='{item.get('dimension')}'")
    for index, item in enumerate(personalization.get("known_intentional") or []):
        if isinstance(item, dict) and item.get("dimension") not in enabled:
            dangling.append(f"known_intentional[{index}]='{item.get('dimension')}'")

    if dangling:
        checks.append(
            DoctorCheckLine(
                False,
                f"personalization: references outside enabled dimensions {sorted(enabled)}: "
                + "; ".join(dangling),
            )
        )
    else:
        checks.append(DoctorCheckLine(True, "personalization: dimension references all resolve"))


def run_dimension_doctor(project_root: str | Path) -> DimensionDoctorReport:
    """Run the full dimension consistency doctor for one project.

    Pure read-only check: loads the bundled canonical yaml, verifies the
    harness internal constants, then validates every dimension reference
    in the project's effective ``iterate.config.yaml``.
    """
    report = DimensionDoctorReport()
    definitions, load_errors = load_canonical_dimensions()
    report.definitions = definitions
    report.canonical_order = list(definitions)
    for error in load_errors:
        report.checks.append(DoctorCheckLine(False, error))

    canonical = set(definitions)
    if canonical:
        _check_internal_constants(definitions, report.checks)

    effective = load_effective_config(project_root)
    config = effective.config
    report.config_source = effective.source
    report.enabled_dimensions = list(config.dimensions)
    enabled = set(config.dimensions)

    _check_enabled_dimensions(config, canonical, report.checks)
    _check_keyed_references(
        "dimension_resources",
        sorted(config.dimension_resources),
        enabled,
        canonical,
        report.checks,
    )
    _check_keyed_references(
        "thresholds.dimensions",
        sorted(config.thresholds.dimensions),
        enabled,
        canonical,
        report.checks,
    )
    _check_personalization(config, enabled, report.checks)
    return report


def render_doctor_report(report: DimensionDoctorReport) -> str:
    """Render the doctor report as plain text (✓ pass / ✗ fail / ⚠ info)."""
    lines = [
        "iterate dimension doctor",
        (
            f"canonical: {len(report.canonical_order)} dimensions from bundled "
            f"{DIMENSIONS_DATA_PATH.name} (byte-identical to skill config/dimensions.yaml)"
        ),
        (
            f"config source: {report.config_source} | enabled dimensions: "
            f"{', '.join(report.enabled_dimensions) or '(none)'}"
        ),
    ]
    for check in report.checks:
        mark = "✓" if check.status is True else ("✗" if check.status is False else "⚠")
        lines.append(f"  {mark} {check.text}")
    if report.ok:
        lines.append("verdict: OK — dimension system consistent")
    else:
        lines.append(f"verdict: FAIL — {len(report.errors)} error(s); fix the ✗ lines above")
    return "\n".join(lines)


__all__ = [
    "DIMENSIONS_DATA_PATH",
    "REQUIRED_DEFINITION_FIELDS",
    "VALID_PRIORITIES",
    "DimensionDefinition",
    "DimensionDoctorReport",
    "DoctorCheckLine",
    "load_canonical_dimensions",
    "render_doctor_report",
    "run_dimension_doctor",
]
