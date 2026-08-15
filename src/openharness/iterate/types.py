"""Core data types for the iterate semantic layer.

Python port of ``harness/iterate-plugin/src/types.ts`` (the dsh plugin that
mirrors the original iterate SKILL.md semantics). Field names follow the
skill's findings schema (snake_case); the report structures are internal to
the harness and use idiomatic snake_case as well.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["critical", "high", "medium", "low"]
ReviewMode = Literal["dry-run", "normal"]
Scope = Literal["full", "changed-only"]
Language = Literal["zh", "en"]
StoppedReason = Literal["converged", "max_rounds_reached"]
Verdict = Literal["approved", "revise"]
FinalVerdict = Literal["approved", "needs_revision"]

DecisionEntryType = Literal[
    "round_start",
    "review_result",
    "atomic_fix",
    "architectural_fix",
    "revert",
    "validation",
    "decision",
    "report",
]


@dataclass
class ReviewFinding:
    """A single finding from a dimension review (skill findings schema)."""

    dimension: str
    file: str
    severity: Severity
    summary: str
    failure_scenario: str
    suggested_fix: str
    is_atomic: bool
    line: int | None = None


@dataclass
class ReviewRound:
    """One round of review: a numbered batch of findings."""

    round: int
    findings: list[ReviewFinding] = field(default_factory=list)


@dataclass
class KnownIntentional:
    """A known-intentional entry; matching findings are filtered out."""

    file: str
    dimension: str
    reason: str
    line: int | None = None  # None or 0 means the whole file


@dataclass
class ConvergenceInfo:
    """Multi-round convergence statistics for a review."""

    total_rounds: int
    findings_by_round: list[int]
    converged: bool
    stopped_reason: StoppedReason


@dataclass
class ReportSummary:
    """Severity/dimension breakdown of a review report."""

    total_findings: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    by_dimension: dict[str, int] = field(default_factory=dict)


@dataclass
class ReviewReport:
    """Review report (dry-run or normal mode)."""

    mode: ReviewMode
    goal: str
    dimensions: list[str]
    max_review_rounds: int
    rounds: list[ReviewRound]
    findings: list[ReviewFinding]
    convergence: ConvergenceInfo
    summary: ReportSummary


@dataclass
class ReviewScopeConfig:
    scope: Scope = "full"


@dataclass
class DimensionResources:
    """Per-dimension resource overrides (model / concurrency / token budget).

    Unset fields (``None``) mean "inherit the session default" — the plan
    only carries what the project explicitly configured.
    """

    model: str | None = None
    concurrency: int | None = None
    token_budget: int | None = None

    def is_empty(self) -> bool:
        return self.model is None and self.concurrency is None and self.token_budget is None


@dataclass
class AtomicConfig:
    max_lines: int = 20
    max_adjacent_methods: int = 3


@dataclass
class GitConfig:
    target_branch: str = "main"
    use_worktree: bool = False
    push_per_round: bool = False
    auto_merge: bool = False


@dataclass
class ValidationConfig:
    command_whitelist: list[str] = field(default_factory=list)
    commands: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ReviewerConfig:
    output_schema_validation: bool = True


@dataclass
class IterateConfig:
    """Parsed iterate.config.yaml (effective = defaults + overrides)."""

    goal: str = "Improve code quality and maintainability"
    max_rounds: int = 7
    language: Language = "en"
    dimensions: list[str] = field(default_factory=lambda: [
        "correctness",
        "security",
        "performance",
        "architecture",
        "style-tests",
        "tech-debt",
        "spec-compliance",
        "frontend-backend",
        "ui-ux",
    ])
    review: ReviewScopeConfig = field(default_factory=ReviewScopeConfig)
    atomic: AtomicConfig = field(default_factory=AtomicConfig)
    git: GitConfig = field(default_factory=GitConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    reviewer: ReviewerConfig = field(default_factory=ReviewerConfig)
    dimension_resources: dict[str, DimensionResources] = field(default_factory=dict)
    onboarding: dict[str, object] | None = None
    personalization: dict[str, object] | None = None


@dataclass
class DecisionLogEntry:
    """One entry in the append-only decision log."""

    timestamp: str
    round: int
    type: str
    data: dict[str, object] = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Result of running a validation command."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


@dataclass
class MetaReviewIssue:
    """A single defect found while auditing a review report."""

    code: str
    severity: Severity
    summary: str
    detail: str


@dataclass
class MetaReviewResult:
    """Deterministic audit result for a ReviewReport."""

    passed: bool
    verdict: Verdict
    checks_run: int
    issues: list[MetaReviewIssue] = field(default_factory=list)


@dataclass
class FinalReviewSummary:
    """Rolled-up summary that mirrors the source report plus the verdict."""

    total_findings: int
    critical: int
    high: int
    medium: int
    low: int
    converged: bool
    total_rounds: int
    report_issues: int
    verdict: FinalVerdict


@dataclass
class FinalReviewReport:
    """The final deliverable: the audited report plus its meta-review."""

    verdict: FinalVerdict
    source: ReviewReport
    meta_review: MetaReviewResult
    summary: FinalReviewSummary


def finding_to_dict(finding: ReviewFinding) -> dict[str, object]:
    """Serialize a finding to the skill's findings-schema key names.

    ``line`` is omitted when unset, mirroring JSON.stringify dropping
    ``undefined`` in the TS plugin.
    """
    out: dict[str, object] = {
        "dimension": finding.dimension,
        "file": finding.file,
        "severity": finding.severity,
        "summary": finding.summary,
        "failure_scenario": finding.failure_scenario,
        "suggested_fix": finding.suggested_fix,
        "is_atomic": finding.is_atomic,
    }
    if finding.line is not None:
        out["line"] = finding.line
    return out
