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
StoppedReason = Literal["converged", "max_rounds_reached", "no_rounds"]
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


#: Severity metrics supported by threshold gates (field prefix ``max_``).
SEVERITY_METRICS: tuple[str, ...] = ("critical", "high", "medium", "low")


@dataclass
class DimensionThresholds:
    """Per-dimension severity caps (unset = inherit the global threshold)."""

    max_critical: int | None = None
    max_high: int | None = None
    max_medium: int | None = None
    max_low: int | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, f"max_{metric}") is None for metric in SEVERITY_METRICS)


@dataclass
class ThresholdsConfig:
    """Project threshold gates evaluated on the final report.

    ``max_critical`` / ``max_high`` / ``max_medium`` / ``max_low`` cap the
    number of findings at each severity; ``dimensions`` caps specific
    dimensions. ``None`` means "no gate for this metric". Violations flip
    the final verdict to ``needs_revision`` and fail the CI exit-code gate.
    """

    max_critical: int | None = None
    max_high: int | None = None
    max_medium: int | None = None
    max_low: int | None = None
    dimensions: dict[str, DimensionThresholds] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return (
            all(getattr(self, f"max_{metric}") is None for metric in SEVERITY_METRICS)
            and not self.dimensions
        )


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
class InvariantConfig:
    """Project-level invariants for code-mode defensive guarding (§20.3.2).

    Mirrors the skill's ``config.invariants`` section (v3.0): ``ensure`` file
    assertions that must hold plus per-module command lists that must exit 0.
    When the project config declares no ``invariants`` section, code-mode
    invariant checking falls back to ``validation.commands`` so pre-v3.0
    projects still get post-edit validation.
    """

    ensure: list[str] = field(default_factory=list)
    commands: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ReviewerConfig:
    output_schema_validation: bool = True
    #: Hard evidence gate: every finding's file/line is validated against real
    #: files on disk; a fabricated or out-of-range location flips the audit.
    evidence_validation: bool = True
    #: Prompt-informative scope coverage check (default on): meta-review emits
    #: a medium COVERAGE_GAP hint when a reviewer's self-reported readFiles do
    #: not cover its assigned inventory. Never flips the final verdict.
    coverage_validation: bool = True
    #: Batch size for a `full`-scope review (files per reviewer chunk).
    scope_chunk_size: int = 25


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
    invariants: InvariantConfig | None = None
    reviewer: ReviewerConfig = field(default_factory=ReviewerConfig)
    dimension_resources: dict[str, DimensionResources] = field(default_factory=dict)
    # Whole-run token budget enforced by the engine-level loop policy: the
    # iterate loop hard-stops once the main-loop usage exceeds it.
    token_budget: int | None = None
    # Whole-run monetary budget (USD) enforced by the engine-level loop
    # policy: the loop hard-stops once the accumulated cost exceeds it.
    # ``None`` disables the cap.
    budget_usd: float | None = None
    # Turn-level rate cap (requests per minute) for long-running loops:
    # when exceeded, the policy injects a backoff message instead of
    # hammering the endpoint. ``None`` disables throttling.
    max_turns_per_minute: int | None = None
    # LLM reasoning effort for review passes ('low' | 'medium' | 'high').
    # None = follow the provider default. Threaded into the OpenAI-compatible
    # request body so quick rounds can save tokens and critical rounds can
    # deepen analysis (dsh 0.1.1-rc.7+ exposes the same 'low' effort).
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Session workspace isolation (design §11.3.2 finding #7): when True, a
    # normal-mode iterate loop runs its fix rounds inside a dedicated git
    # worktree (``iterate/round-N``) so concurrent sessions never write the
    # same files. Fixes merge back on a successful stop and are dropped on an
    # abnormal one (auto-rollback). May also be enabled via the harness-level
    # ``IterateSettings.worktree_isolation``.
    worktree_isolation: bool = False
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
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
    # Project threshold-gate outcome (None when no thresholds configured or
    # the gate passed cleanly with nothing to fold into the meta-review).
    threshold_gate: object | None = None
    # Prompt-informative scope coverage result (None when coverage validation
    # is disabled or there is nothing to compare). Never flips the verdict.
    coverage: object | None = None


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
