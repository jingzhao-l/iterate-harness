"""Git-based scope resolution for changed-only quick reviews.

Collects the set of files changed relative to a git ref (default ``HEAD``)
so a quick review can target exactly the delta instead of the whole
codebase. Pure subprocess plumbing — no LLM involvement.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

#: Default comparison ref for changed-only reviews.
DEFAULT_REF = "HEAD"

#: Hard cap on the changed-file list (protects prompts from exploding on
#: huge generated-code diffs).
MAX_CHANGED_FILES = 200

#: Timeout for every git subprocess call.
_GIT_TIMEOUT_SECONDS = 15

#: A ref may only contain word chars, ``/``, ``.``, ``-``, ``_``, ``~`` and
#: ``^`` (relative refs like ``HEAD~3`` / ``HEAD^2``) and must start with an
#: alphanumeric (never ``-``, which git would parse as an option).
_REF_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./~^-]*$")


def validate_ref(ref: str) -> str:
    """Return the ref unchanged if it is a safe git ref token.

    Raises ``ValueError`` for refs that could be parsed as git options or
    shell-unsafe tokens (defence at the boundary; subprocess still receives
    a list argv, never a shell string).
    """
    candidate = (ref or "").strip()
    if not candidate or not _REF_PATTERN.match(candidate):
        raise ValueError(f"invalid git ref: {ref!r}")
    return candidate


def detect_repo_root(cwd: str | Path) -> Path | None:
    """Return the repository root for ``cwd`` or ``None`` outside a repo."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    root = proc.stdout.strip()
    return Path(root) if root else None


def _run_git(root: Path, *args: str) -> str | None:
    """Run git in ``root``; return stdout or ``None`` on any failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _clean_path(raw: str) -> str:
    """Strip porcelain quoting/escapes from one path token."""
    path = raw.strip()
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path.strip()


def _parse_diff_paths(output: str) -> list[str]:
    """Parse ``git diff --name-only`` output into relative paths."""
    return [_clean_path(line) for line in output.splitlines() if line.strip()]


def _parse_status_paths(output: str) -> list[str]:
    """Parse ``git status --porcelain`` output into relative paths.

    Renames (``R  old -> new``) contribute only the *new* path — that is
    what a changed-only review should look at.
    """
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        payload = line[3:]
        if "->" in payload:
            payload = payload.split("->", 1)[1]
        path = _clean_path(payload)
        if path:
            paths.append(path)
    return paths


def _is_reviewable_file(root: Path, relative: str) -> bool:
    """Only plain files on disk qualify (porcelain may list directories)."""
    if not relative or relative.endswith("/"):
        return False
    return (root / relative).is_file()


def collect_changed_files(cwd: str | Path, ref: str = DEFAULT_REF) -> list[str]:
    """Collect files changed relative to ``ref`` (committed diff + working
    tree, including untracked files).

    Returns repo-root-relative, deduplicated, sorted paths capped at
    :data:`MAX_CHANGED_FILES`. Outside a git repo the list is empty.
    """
    safe_ref = validate_ref(ref)
    root = detect_repo_root(cwd)
    if root is None:
        return []
    candidates: set[str] = set()
    diff_out = _run_git(root, "diff", "--name-only", safe_ref)
    if diff_out is not None:
        candidates.update(_parse_diff_paths(diff_out))
    status_out = _run_git(root, "status", "--porcelain")
    if status_out is not None:
        candidates.update(_parse_status_paths(status_out))
    reviewable = sorted(p for p in candidates if _is_reviewable_file(root, p))
    return reviewable[:MAX_CHANGED_FILES]
