"""Project onboarding for iterate-harness: ITERATE.md + fingerprints + drift.

Mirrors the skill's ``iterate_cli`` onboarding semantics so both ecosystems
stay byte-compatible on disk:

- ``ITERATE.md`` is split into an AI-maintained region and a user-owned
  region by fixed HTML-comment markers; refresh/reonboard NEVER touch the
  user-owned region;
- manifest SHA-256 fingerprints live in ``iterate.config.yaml`` under
  ``onboarding.fingerprints`` and drive non-blocking drift detection;
- ``onboarding.channel`` records how the file was produced
  (``"ai"`` = model scan, ``"cli"`` = detection fallback), matching the
  skill's schema enum so ``iterate status`` interops.

All filesystem access is defensive: unreadable manifests degrade to
evidence notes instead of raising.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no stubs in this env

from .config_loader import CONFIG_FILENAME

#: Project knowledge base filename (same as the skill's).
ITERATE_MD_FILENAME = "ITERATE.md"

#: Region markers — byte-identical to the skill's generator.py.
AI_START_MARKER = "<!-- ITERATE:AI-MAINTAINED:START -->"
AI_END_MARKER = "<!-- ITERATE:AI-MAINTAINED:END -->"
USER_START_MARKER = "<!-- ITERATE:USER-OWNED:START -->"
USER_END_MARKER = "<!-- ITERATE:USER-OWNED:END -->"

#: Manifests whose SHA-256 is tracked for drift detection (skill parity).
MANIFEST_FILES: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "go.mod",
    "Cargo.toml",
    "Package.swift",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "mix.exs",
    "pubspec.yaml",
    "tsconfig.json",
)

FINGERPRINT_VERSION = "1.0"

ONBOARDING_VERSION = "1.0"

#: Files the onboarding model scan must NEVER read (skill frontmatter parity).
SENSITIVE_SKIP_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "*.crt",
    "*.cer",
    "credentials.json",
    ".aws",
    ".ssh",
)


def utc_now_iso() -> str:
    """Current UTC timestamp in the skill's onboarding format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FingerprintEntry:
    """SHA-256 of one manifest file at onboarding time."""

    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass
class DriftResult:
    """Diff between stored and freshly captured fingerprints."""

    unchanged: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.changed or self.added or self.removed)

    def summary(self) -> str:
        if not self.has_drift:
            return "No drift: all tracked manifests match the onboarding fingerprints."
        parts: list[str] = []
        if self.changed:
            parts.append(f"changed: {', '.join(self.changed)}")
        if self.added:
            parts.append(f"added: {', '.join(self.added)}")
        if self.removed:
            parts.append(f"removed: {', '.join(self.removed)}")
        return "Onboarding drift detected — " + "; ".join(parts)


def compute_sha256(path: Path) -> str | None:
    """SHA-256 hex digest of a file; None when unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def capture_fingerprints(
    project_root: str | Path, ignore_patterns: list[str] | None = None
) -> list[FingerprintEntry]:
    """Fingerprint every manifest present in the project root (non-recursive)."""
    root = Path(project_root)
    ignores = ignore_patterns or []
    entries: list[FingerprintEntry] = []
    for name in MANIFEST_FILES:
        path = root / name
        if not path.is_file() or any(fnmatch(name, pattern) for pattern in ignores):
            continue
        digest = compute_sha256(path)
        if digest is not None:
            entries.append(FingerprintEntry(path=name, sha256=digest))
    return entries


def compare_fingerprints(
    stored: list[FingerprintEntry], current: list[FingerprintEntry]
) -> DriftResult:
    """Three-way diff of stored vs current fingerprints (path → sha256 maps)."""
    stored_map = {entry.path: entry.sha256 for entry in stored}
    current_map = {entry.path: entry.sha256 for entry in current}
    result = DriftResult()
    for path in sorted(set(stored_map) & set(current_map)):
        if stored_map[path] == current_map[path]:
            result.unchanged.append(path)
        else:
            result.changed.append(path)
    result.added = sorted(set(current_map) - set(stored_map))
    result.removed = sorted(set(stored_map) - set(current_map))
    return result


def check_drift(
    project_root: str | Path,
    stored: list[FingerprintEntry],
    ignore_patterns: list[str] | None = None,
) -> DriftResult:
    """Capture fresh fingerprints and diff against ``stored``.

    ``ignore_patterns`` filter BOTH sides so a manifest that was fingerprinted
    before being ignored does not report a spurious ``removed``.
    """
    root = Path(project_root)
    ignores = ignore_patterns or []

    def keep(entries: list[FingerprintEntry]) -> list[FingerprintEntry]:
        return [e for e in entries if not any(fnmatch(e.path, p) for p in ignores)]

    current = keep(capture_fingerprints(root))
    return compare_fingerprints(keep(stored), current)


# --- config onboarding section ------------------------------------------------


def fingerprints_to_dict(entries: list[FingerprintEntry]) -> list[dict[str, str]]:
    return [entry.to_dict() for entry in entries]


def fingerprints_from_dict(raw: object) -> list[FingerprintEntry]:
    """Parse ``onboarding.fingerprints`` defensively; bad rows are skipped."""
    if not isinstance(raw, list):
        return []
    entries: list[FingerprintEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        sha = item.get("sha256")
        if isinstance(path, str) and isinstance(sha, str) and path and len(sha) == 64:
            entries.append(FingerprintEntry(path=path, sha256=sha))
    return entries


def build_onboarding_section(
    *,
    channel: str,
    fingerprints: list[FingerprintEntry],
    completed_at: str | None = None,
) -> dict[str, object]:
    """Assemble the ``onboarding`` yaml section (schema-compatible with the skill)."""
    return {
        "version": ONBOARDING_VERSION,
        "completed_at": completed_at or utc_now_iso(),
        "channel": channel,
        "drift_check": True,
        "fingerprints": fingerprints_to_dict(fingerprints),
    }


def load_stored_fingerprints(project_root: str | Path) -> list[FingerprintEntry]:
    """Read ``onboarding.fingerprints`` from the project config; [] when absent."""
    config_path = Path(project_root) / CONFIG_FILENAME
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    onboarding = raw.get("onboarding")
    if not isinstance(onboarding, dict):
        return []
    return fingerprints_from_dict(onboarding.get("fingerprints"))


def load_drift_ignore(project_root: str | Path) -> list[str]:
    """Read ``onboarding.drift_ignore`` from the project config; [] when absent."""
    config_path = Path(project_root) / CONFIG_FILENAME
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    onboarding = raw.get("onboarding")
    if not isinstance(onboarding, dict):
        return []
    value = onboarding.get("drift_ignore")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def drift_check_enabled(project_root: str | Path) -> bool:
    """Whether the project config enables drift checks (default True when onboarding data exists)."""
    config_path = Path(project_root) / CONFIG_FILENAME
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(raw, dict) or not isinstance(raw.get("onboarding"), dict):
        return False
    value = raw["onboarding"].get("drift_check")
    return value is not False


def check_onboarding_drift(project_root: str | Path) -> DriftResult | None:
    """Full drift check for a project; None when onboarding/drift is absent or disabled."""
    root = Path(project_root)
    if not (root / ITERATE_MD_FILENAME).exists():
        return None
    if not drift_check_enabled(root):
        return None
    stored = load_stored_fingerprints(root)
    return check_drift(root, stored, load_drift_ignore(root))


# --- ITERATE.md region operations ---------------------------------------------


def validate_iterate_md(path: Path) -> list[str]:
    """Structural validation of a generated ITERATE.md; [] when well-formed."""
    if not path.exists():
        return [f"{path.name} was not created"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path.name} is unreadable: {exc}"]
    errors: list[str] = []
    ai_start = text.find(AI_START_MARKER)
    ai_end = text.find(AI_END_MARKER)
    user_start = text.find(USER_START_MARKER)
    user_end = text.find(USER_END_MARKER)
    if ai_start < 0:
        errors.append(f"missing {AI_START_MARKER}")
    if ai_end < 0:
        errors.append(f"missing {AI_END_MARKER}")
    if ai_start >= 0 and ai_end >= 0 and ai_end <= ai_start:
        errors.append("AI-maintained markers are out of order")
    if user_start < 0:
        errors.append(f"missing {USER_START_MARKER}")
    if user_end < 0:
        errors.append(f"missing {USER_END_MARKER}")
    if user_start >= 0 and user_end >= 0 and user_end <= user_start:
        errors.append("user-owned markers are out of order")
    if ai_end >= 0 and user_start >= 0 and user_start < ai_end:
        errors.append("user-owned region must come after the AI-maintained region")
    return errors


def extract_user_owned_section(markdown: str) -> str:
    """Verbatim user-owned region; falls back to a fresh default section when
    markers are missing/corrupt (same tolerance as the skill's generator)."""
    start = markdown.find(USER_START_MARKER)
    end = markdown.find(USER_END_MARKER)
    if start < 0 or end <= start:
        return default_user_owned_section()
    return markdown[start : end + len(USER_END_MARKER)]


def default_user_owned_section() -> str:
    """Fresh user-owned region used for first-time onboarding."""
    return "\n".join(
        [
            USER_START_MARKER,
            "",
            "## 自定义代码约定 / Custom Conventions",
            "<!-- 手动维护：你的项目特有约定，刷新时不会被动过 -->",
            "",
            "## 禁区与风险区 / Protected & Risky Areas",
            "<!-- 手动维护：不允许修改的路径、需要谨慎的模块 -->",
            "",
            "## 手动批注 / Manual Notes",
            "<!-- 手动维护：任何你想让 iterate 记住的内容 -->",
            "",
            USER_END_MARKER,
        ]
    )


def replace_user_owned_section(fresh_markdown: str, user_section: str) -> str:
    """Splice a preserved user section into freshly generated markdown."""
    start = fresh_markdown.find(USER_START_MARKER)
    end = fresh_markdown.find(USER_END_MARKER)
    if start < 0 or end <= start:
        return fresh_markdown
    return (
        fresh_markdown[:start]
        + user_section
        + fresh_markdown[end + len(USER_END_MARKER) :]
    )


def update_completed_at_in_md(markdown: str, completed_at: str) -> str:
    """Rewrite the completed_at row of the metadata table (no-op when absent)."""
    lines = markdown.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and "completed_at" in stripped:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 2:
                lines[index] = f"| completed_at | {completed_at} |\n"
                return "".join(lines)
    return markdown


def write_iterate_md(project_root: str | Path, content: str) -> Path:
    path = Path(project_root) / ITERATE_MD_FILENAME
    path.write_text(content, encoding="utf-8")
    return path


def read_iterate_md(project_root: str | Path) -> str | None:
    path = Path(project_root) / ITERATE_MD_FILENAME
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def is_onboarded(project_root: str | Path) -> bool:
    return (Path(project_root) / ITERATE_MD_FILENAME).is_file()


__all__ = [
    "AI_END_MARKER",
    "AI_START_MARKER",
    "CONFIG_FILENAME",
    "DriftResult",
    "FINGERPRINT_VERSION",
    "FingerprintEntry",
    "ITERATE_MD_FILENAME",
    "MANIFEST_FILES",
    "ONBOARDING_VERSION",
    "SENSITIVE_SKIP_PATTERNS",
    "USER_END_MARKER",
    "USER_START_MARKER",
    "build_onboarding_section",
    "capture_fingerprints",
    "check_drift",
    "check_onboarding_drift",
    "compare_fingerprints",
    "compute_sha256",
    "default_user_owned_section",
    "drift_check_enabled",
    "extract_user_owned_section",
    "fingerprints_from_dict",
    "fingerprints_to_dict",
    "is_onboarded",
    "load_drift_ignore",
    "load_stored_fingerprints",
    "read_iterate_md",
    "replace_user_owned_section",
    "update_completed_at_in_md",
    "utc_now_iso",
    "validate_iterate_md",
    "write_iterate_md",
]
