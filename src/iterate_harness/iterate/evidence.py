"""Deterministic code-evidence verification for review findings.

The iterate review loop requires that reviewer subagents ANCHOR every finding
to real code instead of speculating. This module makes that requirement
enforceable, not just a prompt exhortation:

- a finding's ``file`` must resolve to an existing file under the scope root,
  otherwise the evidence is poisoned (``file_not_found``) — this kills
  fabricated file paths;
- a finding with an explicit line must reference a line that actually exists
  within that file (``line_out_of_range``) — this kills invented line numbers;
- a whole-file finding (``line`` is ``None`` or ``0``) must still reference an
  existing file, so even structural findings cannot point at nothing;
- ``read_verified`` is a best-effort, NON-gating hint that the file appeared
  in the current session's ``read_file`` carry-over. It is never used to fail
  the audit because the per-dimension reviewer reads happen inside subagents
  whose tool calls are not aggregated onto the main context at this point.

The gate rule is deliberately strict (user preference): ANY localizable
finding whose evidence fails flips the whole attestation to ``passed=False``.

The file-system half (path resolution + line counting) is separated from the
pure math so the module can be unit-tested without touching disk.

This module is an I/O-capable companion to the pure :mod:`.meta_review`; it
contains no agent spawning.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

#: Sentinel for whole-file findings (`line` unset or 0 means the whole file).
WHOLE_FILE_LINE = 0

EvidenceError = Literal["file_not_found", "line_out_of_range"]


def count_lines(text: str) -> int:
    """Number of physical lines in ``text``.

    Uses :meth:`str.splitlines` so ``""`` is 0 and a trailing newline does not
    fabricate an extra line (``"a\\nb\\n"`` is 2, not 3).
    """
    if text == "":
        return 0
    return len(text.splitlines())


@dataclass(frozen=True)
class FindingEvidence:
    """Per-finding attestation result."""

    file: str
    line: int | None
    line_total: int | None
    resolved_path: str | None
    verified: bool
    error: EvidenceError | None = None
    #: True/False only when a read-set is supplied; None = not checkable.
    read_verified: bool | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "file": self.file,
            "line": self.line,
            "lineTotal": self.line_total,
            "resolvedPath": self.resolved_path,
            "verified": self.verified,
        }
        if self.error is not None:
            out["error"] = self.error
        if self.read_verified is not None:
            out["readVerified"] = self.read_verified
        return out


@dataclass
class EvidenceAudit:
    """Aggregate attestation over a findings list."""

    checked: int
    results: list[FindingEvidence] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        # read_verified is a hint only; only real existence failures gate.
        return all(r.error is None for r in self.results)

    @property
    def errors(self) -> list[FindingEvidence]:
        return [r for r in self.results if r.error is not None]

    def to_dict(self) -> dict[str, object]:
        return {
            "checked": self.checked,
            "passed": self.passed,
            "violations": [r.to_dict() for r in self.errors],
            "readVerifiedRatio": _ratio(self),
        }


def _ratio(audit: EvidenceAudit) -> float | None:
    computable = [r for r in audit.results if r.read_verified is not None]
    if not computable:
        return None
    return round(sum(1 for r in computable if r.read_verified) / len(computable), 3)


def resolve_within(root: Path, rel: str) -> Path | None:
    """Resolve ``root / rel`` and reject any path escaping ``root``.

    Returning ``None`` on traversal or on unresolvable paths keeps every
    subsequent existence check safe against path-escape.
    """
    try:
        resolved = (root / rel).resolve()
    except OSError:
        return None
    try:
        root_resolved = root.resolve()
    except OSError:
        return None
    if resolved == root_resolved:
        return resolved
    if root_resolved not in resolved.parents:
        return None
    return resolved


def verify_line_bounds(line: int | None, text: str) -> tuple[bool, int]:
    """Pure check that ``line`` (if anchored) exists in ``text``.

    Whole-file findings (``line is None`` or ``0``) are always bounds-valid — a
    structural finding only needs its file to exist. Returns ``(in_bounds,
    line_total)``.
    """
    total = count_lines(text)
    if line is None or line == WHOLE_FILE_LINE:
        return True, total
    if line < 1:
        return False, total
    return line <= total, total


def verify_finding(
    root: Path,
    *,
    rel_file: str,
    line: int | None,
    read_set: set[str] | None = None,
) -> FindingEvidence:
    """Verify a single finding's location against the real filesystem.

    ``read_set`` carries the current session's unique resolved read paths; it is
    only used to fill the non-gating ``read_verified`` hint.
    """
    resolved = resolve_within(root, rel_file)
    if resolved is None or not resolved.is_file():
        return FindingEvidence(
            file=rel_file,
            line=line,
            line_total=None,
            resolved_path=str(resolved) if resolved is not None else None,
            verified=False,
            error="file_not_found",
        )

    try:
        raw = resolved.read_bytes()
    except OSError:
        # Unreadable (permissions, broken link...) — treat as missing evidence.
        return FindingEvidence(
            file=rel_file,
            line=line,
            line_total=None,
            resolved_path=str(resolved),
            verified=False,
            error="file_not_found",
        )

    # A file is not line-addressable if it contains a NUL byte (binary payload).
    # Anchored line numbers on a binary file cannot be trusted, so treat them
    # the same as an out-of-range line rather than credulously accepting them.
    if b"\x00" in raw:
        return FindingEvidence(
            file=rel_file,
            line=line,
            line_total=None,
            resolved_path=str(resolved),
            verified=False,
            error="line_out_of_range",
        )

    text = raw.decode("utf-8", errors="replace")
    in_bounds, total = verify_line_bounds(line, text)
    if not in_bounds:
        return FindingEvidence(
            file=rel_file,
            line=line,
            line_total=total,
            resolved_path=str(resolved),
            verified=False,
            error="line_out_of_range",
        )

    read_verified: bool | None = None
    if read_set is not None:
        read_verified = _normalized(resolved) in read_set

    return FindingEvidence(
        file=rel_file,
        line=line,
        line_total=total,
        resolved_path=str(resolved),
        verified=True,
        read_verified=read_verified,
    )


def _normalized(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path))


def verify_findings(
    root: Path,
    *,
    findings: list[object],
    read_set: set[str] | None = None,
) -> EvidenceAudit:
    """Attest every finding in a list (any object exposing ``file``/``line``)."""
    results: list[FindingEvidence] = []
    for finding in findings:
        rel_file = str(getattr(finding, "file", ""))
        line = getattr(finding, "line", None)
        results.append(
            verify_finding(
                root, rel_file=rel_file, line=line, read_set=read_set
            )
        )
    return EvidenceAudit(checked=len(results), results=results)


def read_set_from_metadata(metadata: dict[str, object] | None) -> set[str] | None:
    """Normalize the session's ``read_file_state`` carry-over into a path set.

    Returns ``None`` when no read trace is present (so callers skip the
    non-gating hint rather than falsely reporting every file as unread).
    """
    if not metadata:
        return None
    raw = metadata.get("read_file_state")
    if not isinstance(raw, list) or not raw:
        return None
    paths: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if isinstance(path, str) and path:
            paths.add(os.path.normcase(path))
    return paths or None