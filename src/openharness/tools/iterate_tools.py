"""The five iterate tools registered into the kernel tool registry.

Implements the BaseTool contract (``name`` / ``description`` /
``input_model`` + async ``execute``) over the pure semantic layer in
``openharness.iterate``:

- ``iterate_config`` — effective config read + validation
- ``iterate_validate`` — EXACT-match preconfigured validation runner
- ``iterate_review`` — plan / aggregate / meta-review deterministic engine
- ``iterate_decision_log`` — append-only JSONL decision log
- ``iterate_context`` — SKILL.md / ITERATE.md / personalization context

Every tool returns a ``ToolResult`` whose ``output`` is JSON; malformed
input is reported as ``is_error=True`` with actionable messages instead of
raising.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..iterate import config_loader, decision_log, meta_review, personalization, review
from ..iterate import types as itypes
from ..iterate import validate as validate_mod
from ..iterate.loop_policy import ITERATE_STATE_KEY
from .base import BaseTool, ToolExecutionContext, ToolResult

SKILL_FILENAME = "SKILL.md"
PROJECT_KNOWLEDGE_FILENAME = "ITERATE.md"
MAX_CONTEXT_CHARS = 30_000


def _json_output(payload: Any, *, error: bool = False) -> ToolResult:
    return ToolResult(
        output=json.dumps(payload, ensure_ascii=False, default=str),
        is_error=error,
    )


def _load_known_intentional(context: ToolExecutionContext) -> list[itypes.KnownIntentional]:
    """Merge personalization known_intentional with project config."""
    effective = config_loader.load_effective_config(context.cwd)
    known: list[itypes.KnownIntentional] = []
    if effective.config.personalization and isinstance(effective.config.personalization, dict):
        raw = effective.config.personalization.get("known_intentional")
        if isinstance(raw, list):
            known.extend(
                itypes.KnownIntentional(
                    file=str(item.get("file", "")),
                    dimension=str(item.get("dimension", "")),
                    reason=str(item.get("reason", "")),
                    line=item.get("line") if isinstance(item.get("line"), int) else None,
                )
                for item in raw
                if isinstance(item, dict)
            )
    known.extend(personalization.known_intentional_of(None, context.cwd))
    return known


# --- iterate_config ---------------------------------------------------------


class IterateConfigInput(BaseModel):
    operation: Literal["read", "validate"] = "read"


class IterateConfigTool(BaseTool):
    name = "iterate_config"
    description = (
        "Read the effective iterate config (project iterate.config.yaml merged "
        "over built-in defaults) or validate it (operation=validate returns "
        "missing-field paths). Read-only."
    )
    input_model = IterateConfigInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        args = arguments if isinstance(arguments, IterateConfigInput) else IterateConfigInput()
        effective = config_loader.load_effective_config(context.cwd)
        cfg = effective.config
        if args.operation == "validate":
            errors = config_loader.validate_config(effective.override if effective.override else {})
            return _json_output({"valid": not errors, "missingFields": errors})
        return _json_output(
            {
                "source": effective.source,
                "goal": cfg.goal,
                "maxRounds": cfg.max_rounds,
                "language": cfg.language,
                "dimensions": cfg.dimensions,
                "review": {"scope": cfg.review.scope},
                "atomic": {
                    "maxLines": cfg.atomic.max_lines,
                    "maxAdjacentMethods": cfg.atomic.max_adjacent_methods,
                },
                "git": {
                    "targetBranch": cfg.git.target_branch,
                    "useWorktree": cfg.git.use_worktree,
                    "pushPerRound": cfg.git.push_per_round,
                    "autoMerge": cfg.git.auto_merge,
                },
                "validation": {
                    "commands": cfg.validation.commands,
                    "commandWhitelist": cfg.validation.command_whitelist,
                },
                "reviewer": {"outputSchemaValidation": cfg.reviewer.output_schema_validation},
            }
        )


# --- iterate_validate -------------------------------------------------------


class IterateValidateInput(BaseModel):
    command: str = Field(..., description="Exact command from validation.commands (trim-matched).")
    timeout_ms: int = Field(default=validate_mod.DEFAULT_TIMEOUT_MS, ge=100, le=3_600_000)


class IterateValidateTool(BaseTool):
    name = "iterate_validate"
    description = (
        "Run a validation command PRECONFIGURED in iterate.config.yaml "
        "validation.commands. The command must match EXACTLY (prefixes are "
        "rejected). Returns exitCode/stdout/stderr/timedOut/durationMs."
    )
    input_model = IterateValidateInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        if not isinstance(arguments, IterateValidateInput):
            return _json_output({"error": "invalid arguments"}, error=True)
        result = validate_mod.run_validation(
            arguments.command,
            project_root=context.cwd,
            timeout_ms=arguments.timeout_ms,
        )
        return _json_output(
            {
                "allowed": result.allowed,
                "command": result.command,
                "exitCode": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timedOut": result.timed_out,
                "durationMs": result.duration_ms,
                "rejectReason": result.reject_reason,
            }
        )


# --- iterate_review ---------------------------------------------------------


class IterateReviewInput(BaseModel):
    operation: Literal["plan", "aggregate", "meta-review"]
    mode: Literal["dry-run", "normal"] = "dry-run"
    max_review_rounds: int = Field(default=3, ge=1, le=20)
    rounds: list[dict[str, Any]] | None = Field(
        default=None, description='[{"round": 1, "findings": [...]}] for aggregate'
    )
    report: dict[str, Any] | None = Field(
        default=None, description="The report JSON for meta-review"
    )


class IterateReviewTool(BaseTool):
    name = "iterate_review"
    description = (
        "Deterministic review engine. plan: build the review plan "
        "(dimensions × reviewer prompts × findings schema). aggregate: dedupe/"
        "filter/sort rounds + compute convergence. meta-review: audit a report "
        "for internal consistency and return the final verdict."
    )
    input_model = IterateReviewInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        if not isinstance(arguments, IterateReviewInput):
            return _json_output({"error": "invalid arguments"}, error=True)
        try:
            if arguments.operation == "plan":
                return self._plan(arguments, context)
            if arguments.operation == "aggregate":
                return self._aggregate(arguments, context)
            return self._meta_review(arguments)
        except ValueError as exc:
            return _json_output({"error": str(exc)}, error=True)

    def _plan(self, args: IterateReviewInput, context: ToolExecutionContext) -> ToolResult:
        effective = config_loader.load_effective_config(context.cwd)
        plan = review.build_review_plan(
            config=effective.config,
            mode=args.mode,  # type: ignore[arg-type]
            max_review_rounds=min(args.max_review_rounds, effective.config.max_rounds),
            known_intentional=_load_known_intentional(context),
        )
        return _json_output({"plan": review.plan_to_dict(plan)})

    def _aggregate(self, args: IterateReviewInput, context: ToolExecutionContext) -> ToolResult:
        if not args.rounds:
            return _json_output(
                {"error": "aggregate requires rounds: [{round, findings}]"}, error=True
            )
        report = review.report_from_dict(
            {"mode": args.mode, "rounds": args.rounds, "maxReviewRounds": args.max_review_rounds}
        )
        known = _load_known_intentional(context)
        if known:
            # Re-run with project known_intentional filtering applied.
            report = review.build_review_report(
                mode=report.mode,
                goal=report.goal,
                dimensions=report.dimensions,
                max_review_rounds=report.max_review_rounds,
                rounds=report.rounds,
                known_intentional=known,
            )
        # Publish loop-policy state (single source of truth for convergence).
        context.metadata[ITERATE_STATE_KEY] = {
            "mode": report.mode,
            "rounds_seen": report.convergence.total_rounds,
            "total_findings": report.summary.total_findings,
            "findings_by_round": report.convergence.findings_by_round,
            "converged": report.convergence.converged,
            "by_dimension": dict(report.summary.by_dimension),
        }
        return _json_output({"report": review.report_to_dict(report)})

    def _meta_review(self, args: IterateReviewInput) -> ToolResult:
        if not args.report:
            return _json_output({"error": "meta-review requires report"}, error=True)
        parsed = review.report_from_dict(args.report)
        final = meta_review.build_final_review_report(parsed)
        return _json_output(
            {
                "finalReport": {
                    "verdict": final.verdict,
                    "summary": {
                        "totalFindings": final.summary.total_findings,
                        "critical": final.summary.critical,
                        "high": final.summary.high,
                        "medium": final.summary.medium,
                        "low": final.summary.low,
                        "converged": final.summary.converged,
                        "totalRounds": final.summary.total_rounds,
                        "reportIssues": final.summary.report_issues,
                        "verdict": final.summary.verdict,
                    },
                    "metaReview": {
                        "passed": final.meta_review.passed,
                        "verdict": final.meta_review.verdict,
                        "checksRun": final.meta_review.checks_run,
                        "issues": [
                            {
                                "code": i.code,
                                "severity": i.severity,
                                "summary": i.summary,
                                "detail": i.detail,
                            }
                            for i in final.meta_review.issues
                        ],
                    },
                    "report": review.report_to_dict(final.source),
                }
            }
        )


# --- iterate_decision_log ---------------------------------------------------


class IterateDecisionLogInput(BaseModel):
    operation: Literal["append", "read"]
    type: str | None = Field(default=None, description="Entry type (append)")
    round: int | None = Field(default=None, ge=1, description="Round number (append)")
    data: dict[str, Any] | None = Field(default=None, description="Entry payload (append)")


class IterateDecisionLogTool(BaseTool):
    name = "iterate_decision_log"
    description = (
        "Append-only decision log (.iterate/decision-log.jsonl). append: "
        "record round_start/review_result/atomic_fix/architectural_fix/revert/"
        "validation/decision/report. read: retrieve all entries."
    )
    input_model = IterateDecisionLogInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        if not isinstance(arguments, IterateDecisionLogInput):
            return _json_output({"error": "invalid arguments"}, error=True)
        if arguments.operation == "read":
            entries = decision_log.read_entries(context.cwd)
            return _json_output(
                {
                    "operation": "read",
                    "entryCount": len(entries),
                    "logPath": str(decision_log.log_path(context.cwd)),
                    "entries": [
                        {
                            "timestamp": e.timestamp,
                            "round": e.round,
                            "type": e.type,
                            "data": e.data,
                        }
                        for e in entries
                    ],
                }
            )
        return self._append(arguments, context)

    def _append(self, args: IterateDecisionLogInput, context: ToolExecutionContext) -> ToolResult:
        if not args.type or args.round is None:
            return _json_output(
                {"error": "type and round are required for append"}, error=True
            )
        if args.type not in decision_log.VALID_ENTRY_TYPES:
            return _json_output(
                {"error": f"unknown type '{args.type}'; valid: {sorted(decision_log.VALID_ENTRY_TYPES)}"},
                error=True,
            )
        entry = decision_log.make_entry(
            entry_type=args.type, round_number=args.round, data=args.data
        )
        count, path = decision_log.append_entry(context.cwd, entry)
        return _json_output(
            {
                "operation": "append",
                "success": True,
                "entryCount": count,
                "logPath": str(path),
                "entry": {
                    "timestamp": entry.timestamp,
                    "round": entry.round,
                    "type": entry.type,
                    "data": entry.data,
                },
            }
        )


# --- iterate_context --------------------------------------------------------


class IterateContextInput(BaseModel):
    operation: Literal["read"] = "read"


class IterateContextTool(BaseTool):
    name = "iterate_context"
    description = (
        "Read project iterate context: SKILL.md (walked up from cwd), "
        "ITERATE.md project knowledge, and the personalization summary. "
        "Read-only."
    )
    input_model = IterateContextInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        del arguments  # only "read" exists today
        payload: dict[str, Any] = {}
        skill = _find_up(context.cwd, SKILL_FILENAME)
        if skill is not None:
            payload["skill"] = _clip(skill.read_text(encoding="utf-8", errors="replace"))
        knowledge = _find_up(context.cwd, PROJECT_KNOWLEDGE_FILENAME)
        if knowledge is not None:
            payload["projectKnowledge"] = _clip(
                knowledge.read_text(encoding="utf-8", errors="replace")
            )
        persona = personalization.load(None, context.cwd)
        payload["personalization"] = {
            "reviewFocusAreas": persona.review_focus_areas,
            "codeStylePreferences": persona.code_style_preferences,
            "projectQuirks": persona.project_quirks,
            "communicationPreferences": persona.communication_preferences,
        }
        if not payload:
            return _json_output({"context": "no iterate context found for this project"})
        return _json_output(payload)


def _find_up(start: Path, filename: str) -> Path | None:
    """Locate filename walking up from start; None when absent."""
    current = Path(start).resolve()
    for directory in (current, *current.parents):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
        if directory.parent == directory:
            return None
    return None


def _clip(text: str) -> str:
    """Bound context payload size to protect the conversation window."""
    if len(text) <= MAX_CONTEXT_CHARS:
        return text
    return text[:MAX_CONTEXT_CHARS] + "\n...[truncated]..."


__all__ = [
    "IterateConfigTool",
    "IterateContextTool",
    "IterateDecisionLogTool",
    "IterateReviewTool",
    "IterateValidateTool",
]
