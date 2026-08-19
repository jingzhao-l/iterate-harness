"""File-inventory collection, chunking, and coverage scoring for review scope.

The iterate review loop must force each reviewer subagent to actually open
EVERY file in the scope it is responsible for (not silently skip or assume
files). This module supplies the deterministic building blocks:

- :func:`collect_scope_files`: produce the sorted relative-path inventory for
  a review scope (changed-only delta, or a full-codebase walk filtered to
  source files and stripped of dependency/build/vendor dirs).
- :func:`chunk_files`: split a large inventory into stable batches so `full`
  reviews stay bounded; consecutive files from the same directory are kept
  together to avoid splitting a module's review across two reviewers.
- :func:`compute_coverage`: compare a reviewer's self-reported ``readFiles``
  against the inventory it was assigned, returning a coverage ratio plus the
  list of files that were not opened. Consumed by meta-review as a
  *prompt-informative* metric (not a hard code-evidence gate — the reporter's
  tool-call trace is not aggregated onto the main context).

Architecture: this is an I/O-capable module (walks the filesystem) but keeps
the pure math (:func:`chunk_files`, :func:`compute_coverage`) separated so it
can be unit-tested without touching disk. Mirror of the TS plugin's
``review-scope.ts``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Relative-scope sentinel for whole-module findings.
WHOLE_FILE_LINE = 0

#: Source extensions a full-scope walk includes (a reviewer only ever anchors
#: findings to code, not to lock files, images, or vendored builds).
SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".java",
    ".rs",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cs",
    ".swift",
    ".kt",
    ".scala",
    ".rb",
    ".php",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".vue",
    ".svelte",
})

#: Directory names always excluded from a full-scope walk (dependency/build/
#: vendor outputs a code reviewer should never spend tokens on).
IGNORED_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    "coverage",
    ".tox",
    ".idea",
    ".vscode",
    "target",
    ".release",
    ".dist_tmp",
})

#: Default chunk size for a `full` scope review (files per batch).
DEFAULT_SCOPE_CHUNK_SIZE = 25
#: Coverage ratio at or above which a scope is considered fully covered
#: (below it, meta-review emits a medium COVERAGE_GAP hint).
COVERAGE_TARGET = 0.95


@dataclass(frozen=True)
class CoverageResult:
    """Self-reported read coverage against an assigned inventory."""

    assigned: list[str]
    read: list[str]
    covered: list[str]
    uncovered: list[str]
    ratio: float

    def to_dict(self) -> dict[str, object]:
        return {
            "assigned": self.assigned,
            "read": self.read,
            "covered": self.covered,
            "uncovered": self.uncovered,
            "ratio": self.ratio,
            "met": self.ratio >= COVERAGE_TARGET,
        }


def _normalized(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _source_ext(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SOURCE_EXTENSIONS


def collect_scope_files(
    root: Path,
    *,
    scope: str,
    changed_files: list[str] | None = None,
) -> list[str]:
    """Return the sorted relative-path inventory for a review scope.

    - ``scope == "full"``: walk ``root`` recursively, keep source files, drop
      ignored directories, return sorted relative paths.
    - ``scope == "changed-only"``: return the existing, source-filtered delta
      from ``changed_files`` (paths are normalized; entries that do not exist
      or are not source files are dropped).
    """
    if scope == "changed-only":
        return _collect_changed(changed_files)
    return _collect_full(root)


def _collect_changed(changed_files: list[str] | None) -> list[str]:
    if not changed_files:
        return []
    out: set[str] = set()
    for rel in changed_files:
        if not isinstance(rel, str) or not rel.strip():
            continue
        # Skip a changed path equal to the whole-file sentinel's string form
        # (mirrors the plugin's `rel === String(WHOLE_FILE_LINE)` filter); a
        # literal file named `0` is not line-addressable review inventory.
        if rel == str(WHOLE_FILE_LINE):
            continue
        cleaned = _normalized(rel).replace(os.sep, "/")
        if cleaned.startswith("../") or cleaned.startswith(".."):
            continue
        if _source_ext(cleaned):
            out.add(cleaned)
    return sorted(out)


def _collect_full(root: Path) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for fn in filenames:
            if not _source_ext(fn):
                continue
            abs_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_path, root)
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def chunk_files(files: list[str], per_chunk: int | None = None) -> list[list[str]]:
    """Split ``files`` into stable batches, keeping directory runs together.

    Files are sorted; consecutive entries sharing the same parent directory
    are grouped into a batch before moving on, and a batch is flushed once it
    reaches ``per_chunk``. Pure and deterministic — no disk access.
    """
    if per_chunk is None or per_chunk < 1:
        per_chunk = DEFAULT_SCOPE_CHUNK_SIZE
    ordered = sorted(files)
    chunks: list[list[str]] = []
    current: list[str] = []
    last_dir: str | None = None
    for rel in ordered:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else "."
        # Keep a directory run together: start a new chunk when the parent
        # changes AND the current chunk is non-empty.
        if current and last_dir is not None and parent != last_dir:
            chunks.append(current)
            current = []
            last_dir = None
        current.append(rel)
        last_dir = parent
        if len(current) >= per_chunk:
            chunks.append(current)
            current = []
            last_dir = None
    if current:
        chunks.append(current)
    return chunks


def compute_coverage(
    assigned: list[str],
    read_files: list[str] | None,
) -> CoverageResult:
    """Score self-reported reads against the assigned inventory.

    ``ratio`` is 1.0 when ``assigned`` is empty. Otherwise it is the fraction
    of assigned files present in ``read_files`` (case-insensitively on the
    normalized path). Pure and deterministic.
    """
    read_norm: set[str] = {
        _normalized(p) for p in (read_files or []) if isinstance(p, str) and p
    }
    assigned_sorted = sorted(assigned)
    covered = [rel for rel in assigned_sorted if _normalized(rel) in read_norm]
    uncovered = [rel for rel in assigned_sorted if _normalized(rel) not in read_norm]

    if not assigned_sorted:
        ratio = 1.0
    else:
        ratio = len(covered) / len(assigned_sorted)
    return CoverageResult(
        assigned=assigned_sorted,
        read=sorted({p for p in (read_files or []) if isinstance(p, str)}),
        covered=covered,
        uncovered=uncovered,
        ratio=round(ratio, 3),
    )