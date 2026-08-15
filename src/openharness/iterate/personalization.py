"""Structured iterate personalization data (9 categories, per-project).

Python port of the iterate skill's ``PersonalizationData`` model. Unlike the
upstream OpenHarness personalization framework (10 flat regex facts, global
storage — design §11.3.2 finding #12), iterate personalization is:

- **structured**: 7 structured categories + 2 free-text categories
- **project-scoped**: stored under ``~/.openharness/iterate/<cwd-sha>/``
- **config-bridged**: the ``known_intentional`` list is ALSO surfaced to
  ``iterate.config.yaml`` semantics (personalization overrides are merged by
  the user, not silently).

Storage is JSON (atomic write via temp file + rename); corrupt files are
replaced by defaults on load rather than crashing the harness.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .types import KnownIntentional

log = logging.getLogger(__name__)

PERSONALIZATION_FILENAME = "personalization.json"

#: The 9 category keys (7 structured + 2 free text).
STRUCTURED_CATEGORIES = (
    "known_intentional",
    "code_style_preferences",
    "naming_conventions",
    "preferred_libraries",
    "validation_preferences",
    "risk_tolerances",
    "review_focus_areas",
)
FREE_TEXT_CATEGORIES = ("project_quirks", "communication_preferences")


@dataclass
class PersonalizationData:
    """All 9 categories of iterate personalization for one project."""

    # Structured categories.
    known_intentional: list[KnownIntentional] = field(default_factory=list)
    code_style_preferences: dict[str, str] = field(default_factory=dict)
    naming_conventions: dict[str, str] = field(default_factory=dict)
    preferred_libraries: dict[str, str] = field(default_factory=dict)
    validation_preferences: dict[str, str] = field(default_factory=dict)
    risk_tolerances: dict[str, str] = field(default_factory=dict)
    review_focus_areas: list[str] = field(default_factory=list)
    # Free-text categories.
    project_quirks: str = ""
    communication_preferences: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["known_intentional"] = [
            {
                "file": k.file,
                "dimension": k.dimension,
                "reason": k.reason,
                "line": k.line,
            }
            for k in self.known_intentional
        ]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object] | None) -> PersonalizationData:
        if not isinstance(data, dict):
            return cls()
        known_raw = data.get("known_intentional")
        known = [
            KnownIntentional(
                file=str(item.get("file", "")),
                dimension=str(item.get("dimension", "")),
                reason=str(item.get("reason", "")),
                line=item.get("line") if isinstance(item.get("line"), int) else None,
            )
            for item in (known_raw if isinstance(known_raw, list) else [])
            if isinstance(item, dict)
        ]
        focus_raw = data.get("review_focus_areas")
        focus = [str(x) for x in focus_raw if str(x).strip()] if isinstance(focus_raw, list) else []

        def _mapping(key: str) -> dict[str, str]:
            raw = data.get(key)
            if not isinstance(raw, dict):
                return {}
            return {str(k): str(v) for k, v in raw.items() if str(k).strip()}

        return cls(
            known_intentional=known,
            code_style_preferences=_mapping("code_style_preferences"),
            naming_conventions=_mapping("naming_conventions"),
            preferred_libraries=_mapping("preferred_libraries"),
            validation_preferences=_mapping("validation_preferences"),
            risk_tolerances=_mapping("risk_tolerances"),
            review_focus_areas=focus,
            project_quirks=str(data.get("project_quirks") or ""),
            communication_preferences=str(data.get("communication_preferences") or ""),
        )


def storage_dir(base_dir: str | Path | None, project_root: str | Path) -> Path:
    """Resolve the per-project storage directory (created on demand)."""
    root = Path(base_dir).expanduser() if base_dir else Path.home() / ".openharness" / "iterate"
    digest = hashlib.sha1(str(Path(project_root).resolve()).encode("utf-8")).hexdigest()[:12]
    directory = root / f"{Path(project_root).resolve().name}-{digest}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save(base_dir: str | Path | None, project_root: str | Path, data: PersonalizationData) -> Path:
    """Persist personalization JSON atomically (temp file + rename)."""
    target = storage_dir(base_dir, project_root) / PERSONALIZATION_FILENAME
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def load(base_dir: str | Path | None, project_root: str | Path) -> PersonalizationData:
    """Load personalization for a project; corrupt data falls back to empty."""
    target = storage_dir(base_dir, project_root) / PERSONALIZATION_FILENAME
    if not target.exists():
        return PersonalizationData()
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("corrupt personalization file %s — resetting to empty", target)
        return PersonalizationData()
    return PersonalizationData.from_dict(parsed if isinstance(parsed, dict) else None)


def known_intentional_of(base_dir: str | Path | None, project_root: str | Path) -> list[KnownIntentional]:
    """Convenience: just the known_intentional list for review filtering."""
    return load(base_dir, project_root).known_intentional
