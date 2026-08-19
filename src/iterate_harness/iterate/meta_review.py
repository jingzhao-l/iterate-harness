"""Meta-review engine: audit a ReviewReport and build the final verdict.

Python port of ``harness/iterate-plugin/src/meta-review.ts``.

This is the "纯反复审查" closing step: after the review loop converges on
zero new findings, we don't just trust the aggregated report — we audit the
report itself for internal consistency (counts, severity buckets, dimension
sums, sort order, convergence math). The result is a deterministic
:class:`MetaReviewResult` plus a :class:`FinalReviewReport` that pairs the
source report with a verdict.

Like ``review.py``, this module contains NO I/O and NO agent spawning — it
is the pure, testable core. The orchestrator drives the actual
subagent-driven meta-review critique; all deterministic math lives here.
"""

from __future__ import annotations

import json

from .evidence import EvidenceAudit
from .review import ThresholdGateResult, sort_findings
from .review_scope import CoverageResult
from .types import (
    FinalReviewReport,
    FinalReviewSummary,
    MetaReviewIssue,
    MetaReviewResult,
    ReviewReport,
)

#: Number of distinct consistency checks performed by :func:`meta_review_report`.
META_REVIEW_CHECKS = 6

#: How many uncovered scope files are listed in a COVERAGE_GAP hint before the
#: remainder is folded into a "+N more" suffix.
COVERAGE_LIST_TRUNCATE = 10


def _issue(
    code: str,
    severity: str,
    summary: str,
    detail: str,
) -> MetaReviewIssue:
    return MetaReviewIssue(
        code=code, severity=severity, summary=summary, detail=detail  # type: ignore[arg-type]
    )


def meta_review_report(report: ReviewReport | None) -> MetaReviewResult:
    """Audit a ReviewReport for internal consistency.

    Checks (all deterministic, no I/O):
    1. COUNT_MATCH: summary.total_findings == len(findings)
    2. SEVERITY_SUM: severity buckets total to total_findings and match the
       actual per-severity counts.
    3. DIMENSION_SUM: by_dimension sums to total_findings and every
       finding's dimension appears in report.dimensions.
    4. SORT_ORDER: findings are severity-sorted (most severe first).
    5. CONVERGENCE: findings_by_round sums to total_findings and the
       ``converged`` flag matches the last round's new-finding count.
    6. ROUND_SHAPE: every round has a positive round number, no gaps, and
       only the FINAL round may be empty (an empty final round is the
       expected convergence signal, not a defect).

    Returns a :class:`MetaReviewResult`; ``passed`` is true only when no
    issues were found.
    """
    issues: list[MetaReviewIssue] = []

    # Guard: a None report is a hard failure, not a crash.
    if report is None:
        return MetaReviewResult(
            passed=False,
            verdict="revise",
            checks_run=META_REVIEW_CHECKS,
            issues=[
                _issue(
                    "REPORT_UNDEFINED",
                    "critical",
                    "Report is missing or not an object",
                    "meta_review_report received no valid ReviewReport to audit.",
                )
            ],
        )

    findings = report.findings or []
    summary = report.summary
    total = summary.total_findings or 0
    dimensions = report.dimensions or []

    issues.extend(_check_count_match(findings, total))
    issues.extend(_check_severity_sum(findings, summary, total))
    issues.extend(_check_dimension_sum(findings, summary, total, dimensions))
    issues.extend(_check_sort_order(findings))
    issues.extend(_check_convergence(report, total))
    issues.extend(_check_round_shape(report.rounds or []))

    passed = not issues
    return MetaReviewResult(
        passed=passed,
        verdict="approved" if passed else "revise",
        checks_run=META_REVIEW_CHECKS,
        issues=issues,
    )


def _check_count_match(
    findings: list, total: int
) -> list[MetaReviewIssue]:
    if total != len(findings):
        return [
            _issue(
                "COUNT_MATCH",
                "high",
                f"summary.total_findings ({total}) does not match "
                f"findings.length ({len(findings)})",
                f"The report claims {total} findings but lists {len(findings)}.",
            )
        ]
    return []


def _check_severity_sum(
    findings: list, summary, total: int
) -> list[MetaReviewIssue]:
    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        if f.severity in sev_counts:
            sev_counts[f.severity] += 1
    bucket_sum = sum(sev_counts.values())
    declared_sum = (
        (summary.critical or 0)
        + (summary.high or 0)
        + (summary.medium or 0)
        + (summary.low or 0)
    )
    if declared_sum != total or bucket_sum != total:
        return [
            _issue(
                "SEVERITY_SUM",
                "high",
                "Severity bucket counts are inconsistent with total_findings",
                f"declared buckets sum to {declared_sum}, actual buckets sum to "
                f"{bucket_sum}, but total_findings is {total}.",
            )
        ]
    return []


def _check_dimension_sum(
    findings: list, summary, total: int, dimensions: list[str]
) -> list[MetaReviewIssue]:
    issues: list[MetaReviewIssue] = []
    dim_sum = sum(summary.by_dimension.values())
    if dim_sum != total:
        issues.append(
            _issue(
                "DIMENSION_SUM",
                "high",
                "by_dimension counts do not sum to total_findings",
                f"by_dimension sums to {dim_sum}, but total_findings is {total}.",
            )
        )
    for f in findings:
        if f.dimension not in dimensions:
            issues.append(
                _issue(
                    "DIMENSION_UNKNOWN",
                    "medium",
                    f'Finding references unknown dimension "{f.dimension}"',
                    f'dimension "{f.dimension}" is not in report.dimensions '
                    f"({', '.join(dimensions) if dimensions else 'none'}).",
                )
            )
            break
    return issues


def _check_sort_order(findings: list) -> list[MetaReviewIssue]:
    if sort_findings(findings) != findings:
        return [
            _issue(
                "SORT_ORDER",
                "low",
                "Findings are not severity-sorted",
                "findings should be ordered most-severe first "
                "(critical > high > medium > low).",
            )
        ]
    return []


def _check_convergence(report: ReviewReport, total: int) -> list[MetaReviewIssue]:
    issues: list[MetaReviewIssue] = []
    findings_by_round = (
        report.convergence.findings_by_round if report.convergence else []
    )
    conv_sum = sum(findings_by_round)
    if conv_sum != total:
        issues.append(
            _issue(
                "CONVERGENCE_SUM",
                "high",
                "convergence.findings_by_round does not sum to total_findings",
                f"findings_by_round {json.dumps(findings_by_round)} sums to "
                f"{conv_sum}, but total_findings is {total}.",
            )
        )
    last_round_new = findings_by_round[-1] if findings_by_round else None
    expected_converged = last_round_new == 0
    actual_converged = bool(report.convergence.converged) if report.convergence else None
    if actual_converged != expected_converged:
        issues.append(
            _issue(
                "CONVERGENCE_FLAG",
                "medium",
                "convergence.converged flag is inconsistent with the last round",
                f"last round reported {last_round_new} new findings, so converged "
                f"should be {expected_converged}, but it is {actual_converged}.",
            )
        )
    return issues


def _check_round_shape(rounds: list) -> list[MetaReviewIssue]:
    issues: list[MetaReviewIssue] = []
    seen_rounds: set[int] = set()
    for index, r in enumerate(rounds):
        if not isinstance(r.round, int) or r.round < 1:
            issues.append(
                _issue(
                    "ROUND_NUMBER",
                    "medium",
                    "A round has a missing or non-positive round number",
                    f"round: {r!r}",
                )
            )
            continue
        seen_rounds.add(r.round)
        is_last_round = index == len(rounds) - 1
        if not r.findings and not is_last_round:
            issues.append(
                _issue(
                    "ROUND_EMPTY",
                    "low",
                    f"Round {r.round} has no findings",
                    "A recorded round should contain at least one finding — "
                    "except a final converged round, which finding nothing new "
                    "is the expected success signal.",
                )
            )
    for i in range(1, len(rounds) + 1):
        if i not in seen_rounds:
            issues.append(
                _issue(
                    "ROUND_GAP",
                    "medium",
                    f"Round {i} is missing from the round sequence",
                    f"rounds present: "
                    f"{', '.join(str(x) for x in sorted(seen_rounds)) or 'none'}.",
                )
            )
    return issues


def build_final_review_report(
    report: ReviewReport | None,
    *,
    threshold_result: ThresholdGateResult | None = None,
    evidence: EvidenceAudit | None = None,
    coverage: CoverageResult | None = None,
) -> FinalReviewReport:
    """Pair the source report with its meta-review verdict and summary.

    ``threshold_result`` (from project ``thresholds`` config) is folded in
    as one ``THRESHOLD_EXCEEDED`` issue per violation; a failed gate flips
    the verdict to ``needs_revision`` regardless of the consistency checks.

    ``evidence`` (an :class:`EvidenceAudit` produced against the real repo) is
    the hard code-evidence gate: every finding whose ``file``/``line`` does not
    resolve to existing code is emitted as a critical ``EVIDENCE_VIOLATION``
    and flips the verdict to ``needs_revision``. The audit itself reads the
    filesystem; this function only folds the (pure, precomputed) result in.
    Pure and deterministic.

    ``coverage`` (a :class:`CoverageResult`) is a *prompt-informative* check: a
    scope whose reviewer never reported reading a meaningful share of its
    assigned files surfaces a medium ``COVERAGE_GAP`` hint (it does NOT flip
    the verdict — the subagent's actual tool-call trace is not aggregated
    here, so coverage can only advise, never adjudicate).
    """
    meta = meta_review_report(report)
    if coverage is not None:
        meta.checks_run += 1
        if coverage.uncovered:
            meta.issues.append(
                _issue(
                    "COVERAGE_GAP",
                    "medium",
                    f"{len(coverage.uncovered)} of {len(coverage.assigned)} scope files "
                    "were not (self-)reported as read",
                    "The reviewer reported reading "
                    f"{len(coverage.covered)}/{len(coverage.assigned)} assigned files. "
                    "Uncovered: " + ", ".join(coverage.uncovered[:COVERAGE_LIST_TRUNCATE])
                    + (
                        f" (+{len(coverage.uncovered) - COVERAGE_LIST_TRUNCATE} more)"
                        if len(coverage.uncovered) > COVERAGE_LIST_TRUNCATE
                        else ""
                    )
                    + ". Best-effort coverage hint — verify these files were actually opened.",
                )
            )
    if threshold_result is not None and not threshold_result.passed:
        for violation in threshold_result.violations:
            meta.issues.append(
                _issue(
                    "THRESHOLD_EXCEEDED",
                    "high",
                    f"Threshold gate violated: {violation.get('scope')} "
                    f"{violation.get('metric')} {violation.get('actual')} > "
                    f"limit {violation.get('limit')}",
                    f"Project thresholds (iterate.config.yaml) cap "
                    f"{violation.get('scope')} {violation.get('metric')} findings at "
                    f"{violation.get('limit')}; the report carries "
                    f"{violation.get('actual')}.",
                )
            )
        meta.passed = False
        meta.verdict = "revise"
    if evidence is not None:
        meta.checks_run += 1
        if not evidence.passed:
            for violation in evidence.results:
                if violation.error is None:
                    continue
                detail = (
                    (
                        f"{violation.line} is beyond this file's {violation.line_total} lines"
                        if violation.line_total is not None
                        else f"{violation.file} is a binary/unreadable file not line-addressable"
                    )
                    if violation.error == "line_out_of_range"
                    else f"{violation.file} does not exist at all (verifiable read required)"
                )
                round_hint = ""
                if report is not None and violation.file is not None:
                    # Try to attribute the poisoned finding to the round that
                    # first surfaced it (best-effort; report rounds carry it).
                    for r in report.rounds or []:
                        matched = any(
                            fnd.file == violation.file and fnd.line == violation.line
                            for fnd in r.findings
                        )
                        if matched:
                            round_hint = f" (round {r.round})"
                            break
                summary = (
                    f"Finding references non-existent code: "
                    f"{violation.file}" + (f":{violation.line}" if violation.line else "") + round_hint
                )
                meta.issues.append(
                    _issue("EVIDENCE_VIOLATION", "critical", summary,
                           detail + ". Review results must anchor to real, read code.",)
                )
            meta.passed = False
            meta.verdict = "revise"
    summary = report.summary if report is not None else None
    convergence = report.convergence if report is not None else None
    verdict: str = "approved" if meta.passed else "needs_revision"
    final_summary = FinalReviewSummary(
        total_findings=summary.total_findings if summary else 0,
        critical=summary.critical if summary else 0,
        high=summary.high if summary else 0,
        medium=summary.medium if summary else 0,
        low=summary.low if summary else 0,
        converged=bool(convergence.converged) if convergence else False,
        total_rounds=convergence.total_rounds if convergence else 0,
        report_issues=len(meta.issues),
        verdict=verdict,  # type: ignore[arg-type]
    )
    return FinalReviewReport(
        verdict=verdict,  # type: ignore[arg-type]
        source=report,  # type: ignore[arg-type]
        meta_review=meta,
        summary=final_summary,
        threshold_gate=threshold_result,
        coverage=coverage,
    )
