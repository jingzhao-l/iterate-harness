"""Deterministic review engine for the iterate review loop (dry-run and normal).

Python port of ``harness/iterate-plugin/src/review.ts``.

This module contains NO I/O and NO agent spawning — it is the pure,
testable core of the multi-round convergence loop:

1. dedupe findings across rounds (file + dimension + normalized summary)
2. filter out ``known_intentional`` entries from personalization
3. sort by severity (critical > high > medium > low)
4. compute multi-round convergence stats ("纯反复审查" 收敛统计)
5. assemble the ReviewReport
6. build reviewer task prompts + structured-output schema for subagents

The orchestrator does the spawning: parallel reviewers, feeding back
already-known findings each round, and stopping when a round yields 0 new
findings or the round cap is reached. All deterministic math lives here so
it can be unit-tested.

Divergence from the TS plugin (intentional): ``reviewer_task_prompt``
interpolates the real ``atomic.max_lines`` value into the is_atomic hint —
the TS source emitted a literal ``{atomic.max_lines}`` placeholder because
the value was not threaded into the prompt builder.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .types import (
    ConvergenceInfo,
    IterateConfig,
    KnownIntentional,
    ReportSummary,
    ReviewFinding,
    ReviewMode,
    ReviewReport,
    ReviewRound,
    Scope,
    finding_to_dict,
)

#: Severity ordering: lower rank = more severe.
SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

DEFAULT_ATOMIC_MAX_LINES = 20


def sort_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Sort by severity (most severe first), then by file path, then line."""
    return sorted(
        findings,
        key=lambda f: (SEVERITY_RANK[f.severity], f.file, f.line or 0),
    )


def normalize_summary(summary: str) -> str:
    """Normalize a summary so near-identical duplicates collapse to one key."""
    return re.sub(r"\s+", " ", summary.strip().lower())


def finding_key(f: ReviewFinding) -> str:
    """Dedupe key: same file + same dimension + similar summary."""
    return f"{f.file}|{f.dimension}|{normalize_summary(f.summary)}"


def dedupe_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Remove duplicate findings within a list, keeping the first occurrence."""
    seen: set[str] = set()
    out: list[ReviewFinding] = []
    for f in findings:
        key = finding_key(f)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def filter_known_intentional(
    findings: list[ReviewFinding],
    known: list[KnownIntentional] | None,
) -> list[ReviewFinding]:
    """Filter out findings that match a ``known_intentional`` entry.

    Match rule (mirrors SKILL.md Phase 1 FILTER):
    - same ``file`` AND same ``dimension``, AND
    - entry ``line`` is 0/None (whole file) OR equals the finding's line.
    """
    if not known:
        return findings
    return [f for f in findings if not _matches_known(f, known)]


def _matches_known(f: ReviewFinding, known: list[KnownIntentional]) -> bool:
    for k in known:
        if k.file != f.file or k.dimension != f.dimension:
            continue
        if k.line is None or k.line == 0:
            return True
        if k.line == f.line:
            return True
    return False


@dataclass
class AggregateResult:
    """Output of :func:`aggregate_rounds`."""

    findings: list[ReviewFinding]
    findings_by_round: list[int]
    first_round_by_key: dict[str, int]


def aggregate_rounds(rounds: list[ReviewRound]) -> AggregateResult:
    """Merge per-round findings into one globally-deduped stream.

    Tracks which round first surfaced each finding:

    - ``findings_by_round[r]`` = number of GLOBALLY new findings first seen
      in round r (index 0 = round 1)
    - converged (checked by callers) = the last executed round produced 0
      new findings
    """
    seen: set[str] = set()
    first_round_by_key: dict[str, int] = {}
    merged: list[ReviewFinding] = []

    for round_ in rounds:
        for f in round_.findings:
            key = finding_key(f)
            if key in seen:
                continue
            seen.add(key)
            first_round_by_key[key] = round_.round
            merged.append(f)

    findings_by_round: list[int] = []
    for r in range(1, len(rounds) + 1):
        count = sum(1 for first in first_round_by_key.values() if first == r)
        findings_by_round.append(count)

    return AggregateResult(
        findings=dedupe_findings(merged),
        findings_by_round=findings_by_round,
        first_round_by_key=first_round_by_key,
    )


def compute_convergence(rounds: list[ReviewRound]) -> ConvergenceInfo:
    """Compute convergence statistics for a multi-round review."""
    result = aggregate_rounds(rounds)
    total_rounds = len(rounds)
    last_round_count = result.findings_by_round[total_rounds - 1] if total_rounds > 0 else 0
    converged = total_rounds > 0 and last_round_count == 0
    if total_rounds == 0:
        stopped_reason = "max_rounds_reached"
    elif converged:
        stopped_reason = "converged"
    else:
        stopped_reason = "max_rounds_reached"
    return ConvergenceInfo(
        total_rounds=total_rounds,
        findings_by_round=result.findings_by_round,
        converged=converged,
        stopped_reason=stopped_reason,  # type: ignore[arg-type]
    )


def _summarize(findings: list[ReviewFinding]) -> ReportSummary:
    """Build a severity/summary breakdown for the report."""
    summary = ReportSummary()
    summary.total_findings = len(findings)
    for f in findings:
        if f.severity == "critical":
            summary.critical += 1
        elif f.severity == "high":
            summary.high += 1
        elif f.severity == "medium":
            summary.medium += 1
        else:
            summary.low += 1
        summary.by_dimension[f.dimension] = summary.by_dimension.get(f.dimension, 0) + 1
    return summary


def build_review_report(
    *,
    mode: ReviewMode,
    goal: str,
    dimensions: list[str],
    max_review_rounds: int,
    rounds: list[ReviewRound],
    known_intentional: list[KnownIntentional] | None = None,
) -> ReviewReport:
    """Assemble the final ReviewReport from raw per-round findings.

    Applies known_intentional filtering, cross-round dedupe, severity sort,
    and convergence stats in one deterministic pass. Shared by dry-run (pure
    review) and normal (autonomous loop) modes — the mode only records
    intent; the math is identical.
    """
    # 1. Filter known-intentional per round (before cross-round dedupe).
    filtered_rounds = [
        ReviewRound(
            round=r.round,
            findings=filter_known_intentional(r.findings, known_intentional),
        )
        for r in rounds
    ]

    # 2. Cross-round dedupe + per-round "first seen" tracking.
    result = aggregate_rounds(filtered_rounds)

    # 3. Severity sort the global result.
    sorted_findings = sort_findings(result.findings)

    total_rounds = len(filtered_rounds)
    last_count = result.findings_by_round[total_rounds - 1] if total_rounds > 0 else 0
    if total_rounds == 0:
        stopped_reason = "max_rounds_reached"
    elif last_count == 0:
        stopped_reason = "converged"
    else:
        stopped_reason = "max_rounds_reached"

    return ReviewReport(
        mode=mode,
        goal=goal,
        dimensions=dimensions,
        max_review_rounds=max_review_rounds,
        rounds=filtered_rounds,
        findings=sorted_findings,
        convergence=ConvergenceInfo(
            total_rounds=total_rounds,
            findings_by_round=result.findings_by_round,
            converged=total_rounds > 0 and last_count == 0,
            stopped_reason=stopped_reason,  # type: ignore[arg-type]
        ),
        summary=_summarize(sorted_findings),
    )


def findings_schema() -> dict[str, object]:
    """JSON Schema for reviewer subagent structured output.

    Object-rooted with ``additionalProperties: false`` for full coverage,
    mirroring the skill's findings schema.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "dimension": {"type": "string"},
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low"],
                        },
                        "summary": {"type": "string"},
                        "failure_scenario": {"type": "string"},
                        "suggested_fix": {"type": "string"},
                        "is_atomic": {"type": "boolean"},
                    },
                    "required": [
                        "dimension",
                        "file",
                        "severity",
                        "summary",
                        "failure_scenario",
                        "suggested_fix",
                        "is_atomic",
                    ],
                },
            },
        },
        "required": ["findings"],
    }


def reviewer_task_prompt(
    *,
    dimension: str,
    goal: str,
    scope: Scope,
    mode: ReviewMode,
    output_language: str,
    already_known: list[ReviewFinding] | None = None,
    atomic_max_lines: int = DEFAULT_ATOMIC_MAX_LINES,
) -> str:
    """Build the task prompt for one dimension's reviewer subagent.

    In dry-run mode, pass ``already_known`` (the findings from earlier
    rounds) so the reviewer hunts for NEW issues only — that is what makes
    "反复审查" converge.
    """
    parts: list[str] = [
        f'You are the "{dimension}" reviewer for the iterate review.',
        f"Goal: {goal}",
        f"Scope: {'entire codebase' if scope == 'full' else 'changed files only'}.",
    ]
    if mode == "dry-run":
        parts.append(
            "MODE: dry-run / pure review. You MUST NOT modify, create, or delete "
            "ANY file. Read-only analysis only."
        )
    if already_known:
        serialized = json.dumps(
            [finding_to_dict(f) for f in already_known], indent=2, ensure_ascii=False
        )
        parts.append(
            "Already-known findings from earlier rounds (do NOT re-report these; "
            f"find NEW issues only):\n{serialized}"
        )
    else:
        parts.append("This is round 1 — report every issue you find in this dimension.")
    parts.append('Return a JSON object: {"findings": [...]}.')
    parts.append(
        f'Each finding: dimension (must be "{dimension}"), file (relative path), '
        "line (optional integer), severity (critical/high/medium/low), summary "
        "(one line), failure_scenario (how/when it fails, specific evidence), "
        "suggested_fix (the concrete fix), "
        f"is_atomic (true if the fix is <= {atomic_max_lines} lines within a "
        "SINGLE file/function, else false)."
    )
    parts.append(f"Write summaries and details in {output_language}.")
    return "\n".join(parts)


@dataclass
class DimensionPlan:
    """One dimension's reviewer spec inside a review plan."""

    id: str
    reviewer_prompt: str
    findings_schema: dict[str, object] = field(default_factory=findings_schema)


@dataclass
class ReviewPlan:
    """Canonical review spec handed to the orchestrator."""

    mode: ReviewMode
    goal: str
    scope: Scope
    dimensions: list[DimensionPlan]
    max_review_rounds: int
    known_intentional: list[KnownIntentional] = field(default_factory=list)


def build_review_plan(
    *,
    config: IterateConfig,
    mode: ReviewMode,
    max_review_rounds: int,
    known_intentional: list[KnownIntentional] | None = None,
) -> ReviewPlan:
    """Build a review plan: rounds, dimensions, and per-dimension prompts.

    Used by the ``iterate_review`` tool's ``plan`` operation to give the
    orchestrator a canonical spec.
    """
    language = "Chinese (中文)" if config.language == "zh" else "English"
    return ReviewPlan(
        mode=mode,
        goal=config.goal,
        scope=config.review.scope,
        dimensions=[
            DimensionPlan(
                id=d,
                reviewer_prompt=reviewer_task_prompt(
                    dimension=d,
                    goal=config.goal,
                    scope=config.review.scope,
                    mode=mode,
                    already_known=[],
                    output_language=language,
                    atomic_max_lines=config.atomic.max_lines,
                ),
            )
            for d in config.dimensions
        ],
        max_review_rounds=max_review_rounds,
        known_intentional=known_intentional or [],
    )
