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

from .config_loader import resources_to_dict
from .review_scope import chunk_files
from .types import (
    SEVERITY_METRICS,
    ConvergenceInfo,
    DimensionResources,
    IterateConfig,
    KnownIntentional,
    ReportSummary,
    ReviewFinding,
    ReviewMode,
    ReviewReport,
    ReviewRound,
    Scope,
    ThresholdsConfig,
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

    # `findings_by_round` is indexed by the ACTUAL round number (round r →
    # index r-1), sized to the highest present round. Sizing by `len(rounds)`
    # would silently drop findings first-seen in a non-contiguous round
    # (e.g. a resumed run skipping round 2); sizing by the max round number
    # mirrors `iterate-plugin/src/review.ts` aggregateRounds.
    max_round = max((r.round for r in rounds), default=0)
    findings_by_round: list[int] = []
    for r in range(1, max_round + 1):
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
    # Read the LAST PRESENT round's count by its reported round number, not
    # `total_rounds - 1` (only valid for contiguous 1..N round numbers) —
    # mirrors the plugin's `computeConvergence`.
    last_round = rounds[-1].round if total_rounds > 0 else 0
    last_round_count = (
        result.findings_by_round[last_round - 1] if last_round > 0 else 0
    )
    converged = total_rounds > 0 and last_round_count == 0
    if total_rounds == 0:
        stopped_reason = "no_rounds"
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
    # findings_by_round is sized/indexed by the ACTUAL round number (index
    # round - 1), not by list position: resume runs may skip round numbers and
    # duplicate round numbers can inflate the list length. Index by the last
    # round's real number so convergence reflects the final round's count.
    last_round_index = filtered_rounds[-1].round - 1 if total_rounds > 0 else -1
    last_count = (
        result.findings_by_round[last_round_index]
        if total_rounds > 0 and 0 <= last_round_index < len(result.findings_by_round)
        else 0
    )
    if total_rounds == 0:
        stopped_reason = "no_rounds"
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
            "readFiles": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Every file you actually opened with read_file while reviewing "
                    "your assigned scope. Used to audit coverage; files you never "
                    "opened count as un-reviewed."
                ),
            },
        },
        "required": ["findings", "readFiles"],
    }


def _attachment_clause(attachments: list[ReviewAttachment]) -> str:
    """Build the "attached visual context" instruction block for a reviewer prompt.

    ``path``/``data`` attachments (screenshots, mockups, failure repros) are
    evidence a reviewer must weigh alongside the code — this clause names each
    one and mandates that the reviewer inspect/consider it (e.g. by opening the
    file with a vision-capable tool or the ``image_to_text`` bridge) before
    judging. Returns ``""`` when there are no attachments.
    """
    if not attachments:
        return ""
    lines: list[str] = []
    for attachment in attachments:
        if attachment.path:
            suffix = f" ({attachment.caption})" if attachment.caption else ""
            lines.append(f"- {attachment.path}{suffix}")
        elif attachment.data:
            kind = attachment.media_type or "image"
            suffix = f" ({attachment.caption})" if attachment.caption else ""
            lines.append(f"- inline {kind} image{suffix}")
    if not lines:
        return ""
    return (
        "ATTACHED VISUAL CONTEXT (mandatory): the following image attachment(s) "
        "were provided with this review — each one is part of the evidence you "
        "must weigh:\n"
        + "\n".join(lines)
        + "\nYou MUST inspect/consider EVERY attachment before judging your "
        "dimension (open it with a vision-capable tool, or use image_to_text if "
        "your model cannot see images). If an attachment is inaccessible, state "
        "that and judge solely on the code. Do not ignore an attachment just "
        "because it is not code."
    )


def reviewer_task_prompt(
    *,
    dimension: str,
    goal: str,
    scope: Scope,
    mode: ReviewMode,
    output_language: str,
    already_known: list[ReviewFinding] | None = None,
    atomic_max_lines: int = DEFAULT_ATOMIC_MAX_LINES,
    changed_files: list[str] | None = None,
    scope_files: list[str] | None = None,
    attachments: list[ReviewAttachment] | None = None,
) -> str:
    """Build the task prompt for one dimension's reviewer subagent.

    In dry-run mode, pass ``already_known`` (the findings from earlier
    rounds) so the reviewer hunts for NEW issues only — that is what makes
    "反复审查" converge. Pass ``changed_files`` for changed-only quick
    reviews: the reviewer then restricts itself to the listed delta.

    ``scope_files`` carries the file inventory the reviewer is RESPONSIBLE
    for. When provided, a mandatory COVERAGE RULE is injected: the reviewer
    must actually open every listed file with read_file and return a
    ``readFiles`` array of what it opened. This is the enforcement half of
    "每个子 agent 必须逐文件读取自己负责的审查范围" — files never opened
    lower the meta-review coverage score.
    """
    parts: list[str] = [
        f'You are the "{dimension}" reviewer for the iterate review.',
        f"Goal: {goal}",
        f"Scope: {'entire codebase' if scope == 'full' else 'changed files only'}.",
    ]
    if attachments:
        clause = _attachment_clause(attachments)
        if clause:
            parts.append(clause)
    if scope_files:
        listing = "\n".join(f"- {path}" for path in scope_files if isinstance(path, str))
        parts.append(
            "COVERAGE RULE (mandatory): below is the exact file inventory you are "
            "assigned to review. You MUST open EVERY file in this inventory with "
            "the read_file tool before judging it — do not skip, skim-declare, or "
            "assume any file without reading it. Files you did not actually open "
            "are considered un-reviewed and will lower your coverage score. "
            f"Return a ``readFiles`` array listing every file you actually opened.\n"
            f"Assigned file inventory:\n{listing}"
        )
    elif changed_files:
        listing = "\n".join(f"- {path}" for path in changed_files if isinstance(path, str))
        parts.append(
            "Changed files in this quick review (review ONLY these files; "
            "touch other files solely when directly implicated by a change "
            "above). You MUST open EVERY listed file with read_file before "
            f"judging it — never skip or assume a file.\n{listing}"
        )
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
    parts.append(
        "EVIDENCE RULE (mandatory): read every file you report on with the "
        "read_file tool BEFORE judging it. NEVER report a location you did not "
        "actually read — speculation about code you never inspected is a "
        "disqualifying failure, and fabricated line numbers are treated as "
        "poisoned evidence. Anchor every finding to real code."
    )
    parts.append('Return a JSON object: {"findings": [...], "readFiles": [...]}.')
    parts.append(
        f'Each finding: dimension (must be "{dimension}"), file (relative path), '
        "line (REQUIRED positive integer — the exact line you READ for an "
        "anchored, line-targeted issue; use 0 for whole-file/module-level "
        "issues), severity (critical/high/medium/low), summary (one line), "
        "failure_scenario (how/when it fails, backed by the code you actually "
        "read), suggested_fix (the concrete fix), "
        f"is_atomic (true if the fix is <= {atomic_max_lines} lines within a "
        "SINGLE file/function, else false)."
    )
    parts.append(f"Write summaries and details in {output_language}.")
    return "\n".join(parts)


@dataclass
class BudgetAudit:
    """Deterministic audit of per-dimension token usage against budgets."""

    dimensions: list[dict[str, object]]
    exceeded_dimensions: list[str]
    all_budgeted_exhausted: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "dimensions": self.dimensions,
            "exceededDimensions": self.exceeded_dimensions,
            "allBudgetedExhausted": self.all_budgeted_exhausted,
        }


def audit_dimension_budgets(
    budgets: dict[str, int],
    dimension_usage: dict[str, int],
) -> BudgetAudit:
    """Compare reported per-dimension token usage against configured budgets.

    ``budgets`` carries only the dimensions that HAVE a budget; dimensions
    without one are never audited. Usage is clamped at 0 (defensive).
    """
    rows: list[dict[str, object]] = []
    exceeded: list[str] = []
    for dimension in sorted(budgets):
        budget = budgets[dimension]
        used = max(0, int(dimension_usage.get(dimension, 0)))
        row_exceeded = used > budget
        if row_exceeded:
            exceeded.append(dimension)
        rows.append(
            {
                "dimension": dimension,
                "budget": budget,
                "used": used,
                "remaining": max(0, budget - used),
                "exceeded": row_exceeded,
            }
        )
    return BudgetAudit(
        dimensions=rows,
        exceeded_dimensions=exceeded,
        all_budgeted_exhausted=bool(budgets) and len(exceeded) == len(budgets),
    )


@dataclass
class ThresholdGateResult:
    """Outcome of evaluating project threshold gates on a report."""

    passed: bool
    violations: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "violations": self.violations}


def evaluate_threshold_gates(
    thresholds: ThresholdsConfig,
    findings: list[ReviewFinding],
) -> ThresholdGateResult:
    """Evaluate ``thresholds.max_<metric>`` caps (+ per-dimension).

    Metrics cover critical / high / medium / low; each counts findings at
    exactly that severity. A violation is ``{scope, metric, limit,
    actual}``; the gate passes only when every configured cap holds.
    Pure and deterministic.
    """
    if thresholds.is_empty():
        return ThresholdGateResult(passed=True, violations=[])

    violations: list[dict[str, object]] = []

    def _check(scope: str, metric: str, limit: int | None, actual: int) -> None:
        if limit is not None and actual > limit:
            violations.append(
                {"scope": scope, "metric": metric, "limit": limit, "actual": actual}
            )

    def _check_scope(scope: str, caps: object, scope_findings: list[ReviewFinding]) -> None:
        counts = {metric: 0 for metric in SEVERITY_METRICS}
        for finding in scope_findings:
            if finding.severity in counts:
                counts[finding.severity] += 1
        for metric in SEVERITY_METRICS:
            limit = getattr(caps, f"max_{metric}")
            if limit is not None:
                _check(scope, metric, limit, counts[metric])

    _check_scope("global", thresholds, findings)
    for dimension, dim_thresholds in thresholds.dimensions.items():
        dim_findings = [f for f in findings if f.dimension == dimension]
        _check_scope(f"dimension:{dimension}", dim_thresholds, dim_findings)

    return ThresholdGateResult(passed=not violations, violations=violations)


@dataclass
class DimensionPlan:
    """One dimension's reviewer spec inside a review plan."""

    id: str
    reviewer_prompt: str
    findings_schema: dict[str, object] = field(default_factory=findings_schema)
    resources: DimensionResources | None = None


@dataclass
class ReviewAttachment:
    """An image/visual attachment threaded into a review (screenshot, mockup, failure repro).

    ``path`` resolves relative to the project root; ``data`` is a base64
    payload described by ``media_type``. ``caption`` gives human context.
    Reviewers are told (via ``reviewer_task_prompt``) to inspect/consider every
    attachment before judging their dimension.
    """

    path: str | None = None
    data: str | None = None
    media_type: str = "image/png"
    caption: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to the tool-layer wire shape (camelCase)."""
        out: dict[str, object] = {}
        if self.path:
            out["path"] = self.path
        if self.data:
            out["data"] = self.data
            out["media_type"] = self.media_type
        if self.caption:
            out["caption"] = self.caption
        return out


def parse_attachments(raw: object) -> list[ReviewAttachment]:
    """Defensively normalize arbitrary input into a list of attachments.

    Only entries carrying a non-empty ``path`` or ``data`` survive; everything
    else is dropped (never raises). Mirrors the TS plugin's tool-side guard.
    """
    if not isinstance(raw, list):
        return []
    result: list[ReviewAttachment] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        data = entry.get("data")
        if not (isinstance(path, str) and path.strip()) and not (
            isinstance(data, str) and data.strip()
        ):
            continue
        result.append(
            ReviewAttachment(
                path=path if isinstance(path, str) and path.strip() else None,
                data=data if isinstance(data, str) and data.strip() else None,
                media_type=(
                    entry["media_type"]
                    if isinstance(entry.get("media_type"), str) and entry["media_type"]
                    else "image/png"
                ),
                caption=(
                    entry["caption"]
                    if isinstance(entry.get("caption"), str) and entry["caption"]
                    else None
                ),
            )
        )
    return result


@dataclass
class ReviewPlan:
    """Canonical review spec handed to the orchestrator."""

    mode: ReviewMode
    goal: str
    scope: Scope
    dimensions: list[DimensionPlan]
    max_review_rounds: int
    known_intentional: list[KnownIntentional] = field(default_factory=list)
    attachments: list[ReviewAttachment] = field(default_factory=list)


def _resource_prompt_clause(resources: DimensionResources | None) -> str:
    """One instruction line telling the orchestrator how to spawn this dimension."""
    if resources is None or resources.is_empty():
        return ""
    directives: list[str] = []
    if resources.model is not None:
        directives.append(f"model={resources.model}")
    if resources.concurrency is not None:
        directives.append(f"max concurrent reviewer agents={resources.concurrency}")
    if resources.token_budget is not None:
        directives.append(f"token budget={resources.token_budget}")
    return (
        "\nResource plan (applies when spawning this dimension's reviewer agent): "
        + "; ".join(directives)
        + "."
    )


def build_review_plan(
    *,
    config: IterateConfig,
    mode: ReviewMode,
    max_review_rounds: int,
    known_intentional: list[KnownIntentional] | None = None,
    changed_files: list[str] | None = None,
    scope_files: list[str] | None = None,
    attachments: list[ReviewAttachment] | None = None,
    raw_attachments: object = None,
) -> ReviewPlan:
    """Build a review plan: rounds, dimensions, and per-dimension prompts.

    Used by the ``iterate_review`` tool's ``plan`` operation to give the
    orchestrator a canonical spec. When ``changed_files`` is non-empty the
    plan switches to changed-only scope and embeds the file list into every
    reviewer prompt (changed-only quick review). Per-dimension resource
    overrides (model / concurrency / token budget) are attached both to the
    dimension spec and as an explicit clause inside the reviewer prompt.

    Scope batching (coverage enforcement):
    - ``changed-only``: a single batch carrying the full delta — every file
      is injected into ``reviewer_task_prompt`` via ``scope_files``.
    - ``full``: ``scope_files`` (pre-collected by the caller) is split with
      :func:`chunk_files` into ``config.reviewer.scope_chunk_size`` batches;
      every (dimension × batch) pair gets its own reviewer task so each
      reviewer only owns a bounded, complete inventory it must open
      file-by-file. When no inventory is supplied a full-scope plan falls
      back to unbounded prompts (backwards compatible).
    """
    language = "Chinese (中文)" if config.language == "zh" else "English"
    safe_changed = [f for f in (changed_files or []) if isinstance(f, str) and f.strip()]
    scope: Scope = "changed-only" if safe_changed else config.review.scope

    if scope == "changed-only":
        # changed-only: a single reviewer owning the full delta — handled by
        # the prompt's changed_files branch (COVERAGE RULE inventory is for the
        # batch-split full-scope case), so scope_files stays None here.
        batches: list[list[str] | None] = [None]
    elif scope_files:
        batches = [b for b in chunk_files(scope_files, config.reviewer.scope_chunk_size) if b]
    else:
        batches = [None]

    # Prefer the already-parsed list; otherwise defensively parse the raw wire
    # value (tool-layer passes the JSON array through as `raw_attachments`).
    safe_attachments = (
        parse_attachments(raw_attachments)
        if raw_attachments is not None
        else [a for a in (attachments or []) if isinstance(a, ReviewAttachment)]
    )

    dimensions: list[DimensionPlan] = []
    for d in config.dimensions:
        for index, batch in enumerate(batches):
            dimension_id = d if len(batches) == 1 else f"{d}#{index + 1}"
            dimensions.append(
                DimensionPlan(
                    id=dimension_id,
                    reviewer_prompt=reviewer_task_prompt(
                        dimension=d,
                        goal=config.goal,
                        scope=scope,
                        mode=mode,
                        already_known=[],
                        output_language=language,
                        atomic_max_lines=config.atomic.max_lines,
                        changed_files=safe_changed or None,
                        scope_files=batch,
                        attachments=safe_attachments,
                    )
                    + _resource_prompt_clause(config.dimension_resources.get(d)),
                    resources=config.dimension_resources.get(d),
                )
            )

    return ReviewPlan(
        mode=mode,
        goal=config.goal,
        scope=scope,
        dimensions=dimensions,
        max_review_rounds=max_review_rounds,
        known_intentional=known_intentional or [],
        attachments=safe_attachments,
    )


def plan_to_dict(plan: ReviewPlan) -> dict[str, object]:
    """Serialize a ReviewPlan to the tool-layer JSON shape (camelCase keys
    mirror the TS plugin's wire contract)."""
    return {
        "mode": plan.mode,
        "goal": plan.goal,
        "scope": plan.scope,
        "dimensions": [
            {
                "id": d.id,
                "reviewerPrompt": d.reviewer_prompt,
                "findingsSchema": d.findings_schema,
                **(
                    {"resources": resources_to_dict(d.resources)}
                    if d.resources is not None and not d.resources.is_empty()
                    else {}
                ),
            }
            for d in plan.dimensions
        ],
        "maxReviewRounds": plan.max_review_rounds,
        "knownIntentional": [
            {"file": k.file, "line": k.line, "dimension": k.dimension, "reason": k.reason}
            for k in plan.known_intentional
        ],
        "attachments": [a.to_dict() for a in plan.attachments],
    }


def round_to_dict(round_: ReviewRound) -> dict[str, object]:
    """Serialize one review round."""
    return {
        "round": round_.round,
        "findings": [finding_to_dict(f) for f in round_.findings],
    }


def report_to_dict(report: ReviewReport) -> dict[str, object]:
    """Serialize a ReviewReport to the tool-layer JSON shape."""
    return {
        "mode": report.mode,
        "goal": report.goal,
        "dimensions": report.dimensions,
        "maxReviewRounds": report.max_review_rounds,
        "rounds": [round_to_dict(r) for r in report.rounds],
        "findings": [finding_to_dict(f) for f in report.findings],
        "convergence": {
            "totalRounds": report.convergence.total_rounds,
            "findingsByRound": report.convergence.findings_by_round,
            "converged": report.convergence.converged,
            "stoppedReason": report.convergence.stopped_reason,
        },
        "summary": {
            "totalFindings": report.summary.total_findings,
            "critical": report.summary.critical,
            "high": report.summary.high,
            "medium": report.summary.medium,
            "low": report.summary.low,
            "byDimension": dict(report.summary.by_dimension),
        },
    }


def _parse_finding(raw: object, errors: list[str], index: int) -> ReviewFinding | None:
    if not isinstance(raw, dict):
        errors.append(f"findings[{index}] is not an object")
        return None
    required = ("dimension", "file", "severity", "summary", "failure_scenario", "suggested_fix", "is_atomic")
    missing = [key for key in required if key not in raw]
    if missing:
        errors.append(f"findings[{index}] missing required fields: {', '.join(missing)}")
        return None
    severity = raw["severity"]
    if severity not in SEVERITY_RANK:
        errors.append(f"findings[{index}] severity must be one of {sorted(SEVERITY_RANK)}")
        return None
    line = raw.get("line")
    if line is not None and not isinstance(line, int):
        errors.append(f"findings[{index}] line must be an integer or null")
        return None
    return ReviewFinding(
        dimension=str(raw["dimension"]),
        file=str(raw["file"]),
        severity=severity,
        summary=str(raw["summary"]),
        failure_scenario=str(raw["failure_scenario"]),
        suggested_fix=str(raw["suggested_fix"]),
        is_atomic=bool(raw["is_atomic"]),
        line=line,
    )


def report_from_dict(data: object) -> ReviewReport:
    """Parse a tool-layer report JSON back into a ReviewReport.

    Raises ``ValueError`` with every problem listed when the shape is
    invalid — callers surface the message to the model as a tool error.
    """
    if not isinstance(data, dict):
        # ValueError (not TypeError) is the documented tool-layer contract —
        # callers surface it verbatim to the model as a tool error.
        raise ValueError("report must be a JSON object")  # noqa: TRY004
    errors: list[str] = []

    mode = data.get("mode", "dry-run")
    if mode not in ("dry-run", "normal"):
        errors.append("mode must be 'dry-run' or 'normal'")
    dimensions_raw = data.get("dimensions", [])
    dimensions = [str(d) for d in dimensions_raw] if isinstance(dimensions_raw, list) else []
    if not isinstance(dimensions_raw, list):
        errors.append("dimensions must be an array")

    rounds: list[ReviewRound] = []
    rounds_raw = data.get("rounds", [])
    if isinstance(rounds_raw, list):
        for r_index, r_raw in enumerate(rounds_raw):
            if not isinstance(r_raw, dict):
                errors.append(f"rounds[{r_index}] is not an object")
                continue
            findings: list[ReviewFinding] = []
            findings_raw = r_raw.get("findings", [])
            if isinstance(findings_raw, list):
                for f_index, f_raw in enumerate(findings_raw):
                    parsed = _parse_finding(f_raw, errors, f_index)
                    if parsed is not None:
                        findings.append(parsed)
            else:
                errors.append(f"rounds[{r_index}].findings must be an array")
            round_raw = r_raw.get("round", r_index + 1)
            try:
                round_number = int(round_raw)
            except (TypeError, ValueError):
                # Malformed round (null / dict / non-numeric string) must
                # surface via the ValueError tool contract, not a raw
                # TypeError, and must not abort parsing of later rounds.
                errors.append(f"rounds[{r_index}].round must be an integer")
                round_number = r_index + 1
            rounds.append(ReviewRound(round=round_number, findings=findings))

    if errors:
        raise ValueError("invalid report: " + "; ".join(errors))

    return build_review_report(
        mode=mode,
        goal=str(data.get("goal", "")),
        dimensions=dimensions,
        max_review_rounds=int(data.get("maxReviewRounds", len(rounds) or 1)),
        rounds=rounds,
    )
