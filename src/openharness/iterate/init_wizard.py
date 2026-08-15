"""Detection-driven ``iterate init`` wizard (design §11.2.1 "看类" last item).

The skill's init is a plain-text Q&A; this module gives the harness a
detection step: probe the project's marker files (package.json, pyproject,
go.mod, …), infer the language stack / test command / recommended review
dimensions from real evidence, preview the generated ``iterate.config.yaml``,
and write it only after the user confirms.

Safety semantics preserved from ``config_loader``:
- validation commands are suggested ONLY from explicit evidence (a real
  ``scripts.test`` entry, a real ``[tool.pytest]`` table, a spec dir, …);
  nothing trusted is invented for unknown stacks;
- the emitted yaml is produced via ``yaml.safe_dump`` so user-supplied goal
  text can never inject yaml structure.

Pure functions + defensive parsing throughout; ``detect_project`` never
raises on malformed marker files (evidence lines degrade gracefully).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config_loader import CONFIG_FILENAME

#: Core dimensions recommended for every detected stack.
BASE_DIMENSIONS: tuple[str, ...] = ("correctness", "security", "performance", "architecture")

#: Frontend frameworks (package.json dependency names) that unlock the
#: frontend-backend / ui-ux dimensions.
FRONTEND_MARKERS: tuple[str, ...] = ("react", "vue", "svelte", "next", "nuxt", "@angular/core")

#: Marker file → language label (checked in this order; first hit wins per file).
LANGUAGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("package.json", "node"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("requirements.txt", "python"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("Gemfile", "ruby"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("build.gradle.kts", "java"),
    ("composer.json", "php"),
)

#: Python test dirs/files that prove a pytest-style layout.
PYTHON_TEST_MARKERS: tuple[str, ...] = ("tests", "test")


@dataclass
class ProjectProfile:
    """Detection result shown to the user before any file is written."""

    languages: list[str] = field(default_factory=list)
    test_command: str | None = None
    suggested_dimensions: list[str] = field(default_factory=lambda: list(BASE_DIMENSIONS))
    evidence: list[str] = field(default_factory=list)

    def is_unknown(self) -> bool:
        return not self.languages


def _read_json(path: Path) -> dict | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _detect_node(pkg_path: Path, profile: ProjectProfile) -> None:
    pkg = _read_json(pkg_path)
    if pkg is None:
        profile.evidence.append("package.json found but unreadable — skipped")
        return
    profile.evidence.append("package.json found")
    scripts = pkg.get("scripts")
    if isinstance(scripts, dict) and isinstance(scripts.get("test"), str) and scripts["test"].strip():
        profile.test_command = "npm test"
        profile.evidence.append(f'scripts.test = "{scripts["test"]}" → npm test')
    deps: dict = {}
    for key in ("dependencies", "devDependencies"):
        value = pkg.get(key)
        if isinstance(value, dict):
            deps.update(value)
    frontend = [name for name in FRONTEND_MARKERS if name in deps]
    if frontend:
        for dim in ("frontend-backend", "ui-ux"):
            if dim not in profile.suggested_dimensions:
                profile.suggested_dimensions.append(dim)
        profile.evidence.append(f"frontend deps ({', '.join(frontend[:3])}) → +frontend-backend/ui-ux")


def _detect_python(root: Path, profile: ProjectProfile) -> None:
    pyproject = root / "pyproject.toml"
    content = _read_text(pyproject) if pyproject.exists() else None
    if content is not None:
        profile.evidence.append("pyproject.toml found")
        if "[tool.pytest" in content or "pytest" in content:
            profile.test_command = "pytest -q"
            profile.evidence.append("pytest referenced → pytest -q")
    else:
        marker = next((m for m in PYTHON_TEST_MARKERS if (root / m).exists()), None)
        if marker is not None:
            profile.test_command = "pytest -q"
            profile.evidence.append(f"{marker}/ layout found → pytest -q")
        profile.evidence.append("python marker file found")


def _detect_go(profile: ProjectProfile) -> None:
    profile.test_command = "go test ./..."
    profile.evidence.append("go.mod found → go test ./...")


def _detect_rust(profile: ProjectProfile) -> None:
    profile.test_command = "cargo test"
    profile.evidence.append("Cargo.toml found → cargo test")


def _detect_ruby(root: Path, profile: ProjectProfile) -> None:
    profile.test_command = "bundle exec rspec"
    profile.evidence.append("Gemfile found → bundle exec rspec" + (" (spec/)" if (root / "spec").exists() else ""))


def _detect_java(profile: ProjectProfile) -> None:
    profile.test_command = "mvn test"
    profile.evidence.append("maven/gradle marker found → mvn test (adjust if you use gradle)")


def _detect_php(profile: ProjectProfile) -> None:
    profile.test_command = "composer test"
    profile.evidence.append("composer.json found → composer test (adjust to phpunit if needed)")


def detect_project(cwd: str | Path) -> ProjectProfile:
    """Probe the project root and infer language / test command / dimensions."""
    root = Path(cwd)
    profile = ProjectProfile()
    seen: set[str] = set()
    for marker, language in LANGUAGE_MARKERS:
        if language in seen or not (root / marker).exists():
            continue
        seen.add(language)
        profile.languages.append(language)
        if language == "node":
            _detect_node(root / marker, profile)
        elif language == "python":
            _detect_python(root, profile)
        elif language == "go":
            _detect_go(profile)
        elif language == "rust":
            _detect_rust(profile)
        elif language == "ruby":
            _detect_ruby(root, profile)
        elif language == "java":
            _detect_java(profile)
        elif language == "php":
            _detect_php(profile)
    if not profile.languages:
        profile.evidence.append("no language marker files found — using base dimensions")
    return profile


def build_config_dict(
    *,
    goal: str,
    dimensions: list[str],
    max_rounds: int,
    test_command: str | None,
) -> dict[str, object]:
    """Assemble the init yaml payload (safe_dump substrate)."""
    config: dict[str, object] = {"goal": goal, "max_rounds": max_rounds, "dimensions": list(dimensions)}
    if test_command:
        config["validation"] = {"commands": {"test": [test_command]}}
    return config


def render_config_text(config: dict[str, object]) -> str:
    """Serialize the init config to commented yaml text (injection-safe)."""
    header = (
        "# iterate configuration — generated by `oh iterate init`\n"
        "# Docs: validation.commands entries are matched EXACTLY (whitelist).\n"
    )
    return header + yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def existing_config_path(cwd: str | Path) -> Path:
    return Path(cwd) / CONFIG_FILENAME


def write_config(cwd: str | Path, config: dict[str, object]) -> Path:
    """Write the confirmed config; returns the path (parent dirs must exist)."""
    path = existing_config_path(cwd)
    path.write_text(render_config_text(config), encoding="utf-8")
    return path


def parse_dimension_selection(raw: str, offered: list[str]) -> list[str] | None:
    """Parse a user's dimension selection; None on invalid input.

    Accepted forms: empty (keep all), or comma/space separated 1-based
    indexes / exact names (``2,4`` / ``security, style-tests``). Duplicates
    are collapsed in offered order.
    """
    text = raw.strip()
    if not text:
        return list(offered)
    picked: list[str] = []
    name_to_dim = {dim.lower().replace("_", "-"): dim for dim in offered}
    for token in text.replace(",", " ").split():
        if token.isdigit():
            index = int(token)
            if not 1 <= index <= len(offered):
                return None
            dim = offered[index - 1]
        else:
            dim = name_to_dim.get(token.lower().replace("_", "-"))
            if dim is None:
                return None
        if dim not in picked:
            picked.append(dim)
    return picked or None


__all__ = [
    "BASE_DIMENSIONS",
    "CONFIG_FILENAME",
    "ProjectProfile",
    "build_config_dict",
    "detect_project",
    "existing_config_path",
    "parse_dimension_selection",
    "render_config_text",
    "write_config",
]
