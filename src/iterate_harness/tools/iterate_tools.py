"""The six iterate tools registered into the kernel tool registry.

Implements the BaseTool contract (``name`` / ``description`` /
``input_model`` + async ``execute``) over the pure semantic layer in
``iterate_harness.iterate``:

- ``iterate_config`` — effective config read + validation
- ``iterate_validate`` — EXACT-match preconfigured validation runner
- ``iterate_review`` — plan / aggregate / meta-review deterministic engine
- ``iterate_decision_log`` — append-only JSONL decision log
- ``iterate_context`` — SKILL.md / ITERATE.md / personalization context
- ``iterate_triage`` — interactive y/n/a findings triage with
  ``known_intentional`` persistence

Every tool returns a ``ToolResult`` whose ``output`` is JSON; malformed
input is reported as ``is_error=True`` with actionable messages instead of
raising.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from ..iterate import config_loader, decision_log, dimension_ledger, evidence, meta_review, personalization, review, review_scope, run_plan, trend_store
from ..iterate import types as itypes
from ..iterate import validate as validate_mod
from ..iterate.dimension_context import format_dimension_context
from ..iterate.loop_policy import ITERATE_STATE_KEY
from ..iterate.loop_gates import measured_gates
from ..iterate.glasspane_host_transport import bind_host_transport
from ..iterate.reverify_trigger import REVERIFICATION_STATE_KEY, trigger_reverification_async
from .base import BaseTool, ToolExecutionContext, ToolResult

log = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"
PROJECT_KNOWLEDGE_FILENAME = "ITERATE.md"
MAX_CONTEXT_CHARS = 30_000

TriageDecision = Literal["fix", "skip", "ignore"]


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


# --- the kernel's coverage line, shared by the two tools that publish it -----


def _dimension_coverage_line(
    context: ToolExecutionContext, effective: config_loader.EffectiveConfig
) -> str | None:
    """The run's ``dimensionContext`` verdict as one line, or ``None`` when unmeasurable."""
    built = dimension_ledger.project_dimension_context(
        context.cwd, fallback=[str(d) for d in effective.config.dimensions]
    )
    return None if built is None else format_dimension_context(built)


def _refresh_dimension_coverage(context: ToolExecutionContext) -> str | None:
    """Re-publish the coverage line on the state channel the compaction seam reads.

    Called after a decision that names a dimension lands, because the line is a fact about
    the ledger and the ledger just changed: leaving the stale one in place would tell a
    compacted model that a dimension the run has since evidenced is still unverified.

    Two deliberate limits. It refreshes an *existing* snapshot only — this tool does not own
    ``iterate_state``, and publishing a half-populated one would hand the loop policy a round
    count and a mode nobody measured, which is a verdict made of nothing. And an
    unmeasurable verdict stays absent rather than becoming an invented ``0/0``.
    """
    state = context.metadata.get(ITERATE_STATE_KEY)
    if not isinstance(state, dict):
        return None
    coverage = _dimension_coverage_line(
        context, config_loader.load_effective_config(context.cwd)
    )
    if coverage is None:
        return None
    state["dimension_coverage"] = coverage
    return coverage


# --- iterate_config ---------------------------------------------------------


class IterateConfigInput(BaseModel):
    operation: Literal["read", "validate"] = "read"


class IterateConfigTool(BaseTool[BaseModel]):
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
                "reviewer": {
                    "outputSchemaValidation": cfg.reviewer.output_schema_validation,
                    "evidenceValidation": cfg.reviewer.evidence_validation,
                    "coverageValidation": cfg.reviewer.coverage_validation,
                    "scopeChunkSize": cfg.reviewer.scope_chunk_size,
                },
            }
        )


# --- iterate_validate -------------------------------------------------------


class IterateValidateInput(BaseModel):
    command: str = Field(..., description="Exact command from validation.commands (trim-matched).")
    timeout_ms: int = Field(default=validate_mod.DEFAULT_TIMEOUT_MS, ge=100, le=3_600_000)


class IterateValidateTool(BaseTool[BaseModel]):
    name = "iterate_validate"
    description = (
        "Run a validation command PRECONFIGURED in iterate.config.yaml "
        "validation.commands. The command must match EXACTLY (prefixes are "
        "rejected). Returns exitCode/stdout/stderr/timedOut/durationMs."
    )
    input_model = IterateValidateInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        import asyncio

        if not isinstance(arguments, IterateValidateInput):
            return _json_output({"error": "invalid arguments"}, error=True)
        # ``run_validation`` shells out to subprocess.run (up to timeout_ms /
        # 1000s) — synchronous. Awaited directly it would block the event
        # loop, stalling heartbeats, mailbox forwarding and SSE for the whole
        # command. Offload to a worker thread.
        result = await asyncio.to_thread(
            validate_mod.run_validation,
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


def _clean_dimension_usage(raw: dict[str, int] | None) -> dict[str, int]:
    """Defensively normalize a per-dimension token-total report."""
    if not raw:
        return {}
    return {
        str(dim): max(0, int(tokens))
        for dim, tokens in raw.items()
        if isinstance(tokens, int) and not isinstance(tokens, bool)
    }


def _clean_dimension_usage_io(raw: dict[str, dict[str, int]] | None) -> dict[str, dict[str, int]]:
    """Defensively normalize a per-dimension input/output split report.

    Only entries with at least one valid non-negative token count survive;
    malformed shapes are dropped (never raise — reporting must not break
    aggregation).
    """
    if not raw:
        return {}
    cleaned: dict[str, dict[str, int]] = {}
    for dim, split in raw.items():
        if not isinstance(split, dict):
            continue
        input_tokens = split.get("input")
        output_tokens = split.get("output")
        valid_input = input_tokens if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) else None
        valid_output = output_tokens if isinstance(output_tokens, int) and not isinstance(output_tokens, bool) else None
        if valid_input is None and valid_output is None:
            continue
        clamped_input = max(0, valid_input or 0)
        clamped_output = max(0, valid_output or 0)
        if clamped_input == 0 and clamped_output == 0:
            continue
        cleaned[str(dim)] = {"input": clamped_input, "output": clamped_output}
    return cleaned


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
    changed_files: list[str] | None = Field(
        default=None,
        description="Changed-only quick review: repo-relative delta files to pin the review scope",
    )
    attachments: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Optional (plan only): image/visual attachments to thread into the "
            "review, e.g. [{\"path\": \"screens/hits.png\", \"caption\": "
            "\"reproduced layout bug\"}]. Each entry: {path?, data?, "
            "media_type?, caption?} — path resolves relative to the project "
            "root, data is a base64 payload (media_type e.g. image/png), "
            "caption gives human context. Injected as a mandatory clause into "
            "every dimension reviewer prompt so screenshots/mockups/failure "
            "repros are weighed alongside the code."
        ),
    )
    dimension_usage: dict[str, int] | None = Field(
        default=None,
        description=(
            "Optional per-dimension token usage (e.g. from reviewer agents' "
            "reported totals) audited against dimension_resources token budgets"
        ),
    )
    dimension_usage_io: dict[str, dict[str, int]] | None = Field(
        default=None,
        description=(
            "Optional per-dimension input/output token split, e.g. "
            '{"security": {"input": 1000, "output": 500}} — enables exact '
            "per-dimension USD billing instead of the blended-price estimate"
        ),
    )


class IterateReviewTool(BaseTool[BaseModel]):
    name = "iterate_review"
    description = (
        "Deterministic review engine. plan: build the review plan "
        "(dimensions × reviewer prompts × findings schema). aggregate: dedupe/"
        "filter/sort rounds + compute convergence. meta-review: audit a report "
        "for internal consistency and return the final verdict."
    )
    input_model = IterateReviewInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        if isinstance(arguments, IterateReviewInput):
            return arguments.operation != "aggregate"
        return True

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        if not isinstance(arguments, IterateReviewInput):
            return _json_output({"error": "invalid arguments"}, error=True)
        try:
            if arguments.operation == "plan":
                return self._plan(arguments, context)
            if arguments.operation == "aggregate":
                return await self._aggregate(arguments, context)
            return self._meta_review(arguments, context)
        except ValueError as exc:
            return _json_output({"error": str(exc)}, error=True)

    def _plan(self, args: IterateReviewInput, context: ToolExecutionContext) -> ToolResult:
        effective = config_loader.load_effective_config(context.cwd)
        root = Path(context.cwd)
        # Full-codebase review: pre-collect the source inventory so
        # build_review_plan can batch it into per-chunk reviewer tasks.
        changed_only = bool(args.changed_files)
        scope_files: list[str] | None = None
        if not changed_only and effective.config.review.scope == "full":
            scope_files = review_scope.collect_scope_files(root, scope="full")
        plan = review.build_review_plan(
            config=effective.config,
            mode=args.mode,
            max_review_rounds=min(args.max_review_rounds, effective.config.max_rounds),
            known_intentional=_load_known_intentional(context),
            changed_files=args.changed_files,
            scope_files=scope_files,
            raw_attachments=args.attachments,
        )
        # Scenario ②'s producing half: the same call that hands the reviewer its plan
        # publishes the kernel run plan the GlassPane side reads through
        # GLASSPANE_RUN_PLAN. A failure there is REPORTED in the payload next to a
        # perfectly usable review plan — a silent omission would leave the other
        # harness compacting against a stale contract with nobody noticing.
        return _json_output({
            "plan": review.plan_to_dict(plan),
            "runPlan": self._publish_run_plan(context),
        })

    def _publish_run_plan(self, context: ToolExecutionContext) -> dict[str, Any]:
        """Build + write the kernel run plan, and cite where and what digest.

        Built from the project's RAW config mapping (``load_config``), never from the
        effective config: the published contract must be the dimensions a human wrote,
        not the built-in defaults this harness would otherwise invent for a project
        that declared none. ``load_config`` returns ``None`` for a missing or
        non-mapping file; that is refused as an error rather than passed into
        ``build_run_plan``.
        """
        raw = config_loader.load_config(context.cwd)
        if raw is None:
            return {
                "error": (
                    "RUN_PLAN_E_NO_CONFIG: the project carries no readable "
                    "iterate.config.yaml mapping to plan from"
                ),
                "remedy": (
                    f"create {config_loader.CONFIG_FILENAME} with a dimensions list at the "
                    "project root; the run plan is published from the config a human wrote, "
                    "never from the built-in defaults"
                ),
            }
        try:
            plan = run_plan.build_run_plan(raw, source_name=config_loader.CONFIG_FILENAME)
            written = run_plan.write_run_plan(plan, run_plan.plan_path(context.cwd))
        except run_plan.RunPlanError as error:
            return {"error": str(error), "remedy": error.remedy}
        except OSError as error:
            return {
                "error": f"RUN_PLAN_E_WRITE: cannot write the run plan ({error})",
                "remedy": "make .iterate/run-plan.json writable, then plan again",
            }
        # build_run_plan publishes "dimensions" as a list of dimension dicts, but its
        # `dict[str, object]` return type cannot say so, so iterating that value would
        # be unchecked. State the shape once at the read site.
        dimension_ids = [
            str(item["id"]) for item in cast(list[dict[str, object]], plan["dimensions"])
        ]
        payload: dict[str, Any] = {
            "path": str(written.path),
            "digest": written.digest,
            "dimensions": dimension_ids,
        }
        # The digest must be checkable from the audit chain, not only from the tool
        # output an agent may compact away. A ledger refusal never cancels the
        # publication — the file is written and the other side will read it — but the
        # failure is named in the payload (same reporting rule as above).
        try:
            decision_log.append_entry(
                context.cwd,
                decision_log.make_entry(
                    entry_type="decision",
                    round_number=1,
                    data={
                        "kind": "run-plan",
                        "runPlanDigest": written.digest,
                        "dimensions": payload["dimensions"],
                    },
                ),
            )
        except (OSError, *decision_log.DECISION_LOG_REFUSALS) as error:
            log.warning("iterate run-plan decision could not be logged: %s", error, exc_info=True)
            payload["ledgerError"] = f"{getattr(error, 'code', type(error).__name__)}: {error}"
        return payload

    async def _aggregate(self, args: IterateReviewInput, context: ToolExecutionContext) -> ToolResult:
        if not args.rounds:
            return _json_output(
                {"error": "aggregate requires rounds: [{round, findings}]"}, error=True
            )
        # ``report_from_dict`` derives ``dimensions`` from the payload, and an
        # omitted list becomes []. Meta-review then flags every finding as
        # DIMENSION_UNKNOWN (the aggregate was always "needs_revision"), so
        # feed the project's real dimension set and goal.
        effective = config_loader.load_effective_config(context.cwd)
        report = review.report_from_dict(
            {
                "mode": args.mode,
                "rounds": args.rounds,
                "maxReviewRounds": args.max_review_rounds,
                "dimensions": list(effective.config.dimensions),
                "goal": effective.config.goal,
            }
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
        usage_totals = _clean_dimension_usage(args.dimension_usage)
        usage_io = _clean_dimension_usage_io(args.dimension_usage_io)
        # Split reports also count toward budget audits (input + output).
        for dimension, split in usage_io.items():
            split_total = split["input"] + split["output"]
            if split_total > usage_totals.get(dimension, 0):
                usage_totals[dimension] = split_total
        budget_audit = self._audit_budgets(usage_totals, context)
        # Publish loop-policy state (single source of truth for convergence).
        state: dict[str, Any] = {
            "mode": report.mode,
            "rounds_seen": report.convergence.total_rounds,
            "total_findings": report.summary.total_findings,
            "findings_by_round": report.convergence.findings_by_round,
            "converged": report.convergence.converged,
            "by_dimension": dict(report.summary.by_dimension),
        }
        # Scenario ②'s consuming half: the kernel's dimensionContext verdict rides the
        # same state channel the compaction attachment already reads, so a compacted
        # session still carries "which planned dimensions were never verified".
        coverage = self._dimension_coverage(context, effective)
        if coverage is not None:
            state["dimension_coverage"] = coverage
        # Only the gates this round really measured stay in the state; see
        # iterate/loop_gates.py for why an absent gate beats a default.
        gates = measured_gates(
            evidence_validation=config_loader.load_effective_config(
                context.cwd
            ).config.reviewer.evidence_validation,
            findings=report.findings,
            cwd=context.cwd,
            metadata=context.metadata,
        )
        state.update(gates)
        payload: dict[str, Any] = {"report": review.report_to_dict(report)}
        # Scenario ①'s trigger half: an invariant verdict the kernel *measured* as failed earns a
        # re-verification through the engine channel, and the answer is relayed, not judged here.
        # This is the one site that holds all four inputs — the measured gate, the invariant report
        # behind its digest, the run's ledger root, and the state object about to be published — so
        # the wiring lives in one call. See iterate/reverify_trigger.py for the rules it is bound
        # by, including the one that keeps a missing verdict silent instead of satisfied.
        # Scenario ①'s host half: the transport this trigger needs is the run's own MCP client,
        # read-only. bind_host_transport hands over both shapes of it, or says why the run
        # cannot (no mcp plane / no GlassPane server / a server that never connected), so an
        # absent channel reads as "not attempted, because …" rather than as silence. This site
        # awaits: engine/query.py runs tools with ``await tool.execute(...)`` and ui/runtime.py
        # connects the MCP manager on that same loop, so the awaitable transport is the only one
        # that can reach those sessions — a synchronous send here could only ever refuse. The
        # additions go on a merged copy — a callable must never land in the durable
        # tool_metadata the engine folds across turns.
        transport_metadata = {**context.metadata, **bind_host_transport(context.metadata)}
        reverification = await trigger_reverification_async(
            project_root=context.cwd,
            metadata=transport_metadata,
            gates=gates,
            round_number=report.convergence.total_rounds,
            planned_dimensions=list(report.dimensions),
        )
        if reverification is not None:
            state[REVERIFICATION_STATE_KEY] = reverification
            payload[REVERIFICATION_STATE_KEY] = reverification
        if usage_totals:
            # Engine-level per-dimension usage passthrough (v1.2-c): the
            # loop policy bills these reported totals into its CostMeter.
            state["dimension_usage"] = usage_totals
        if usage_io:
            # Exact in/out split passthrough (v1.26): billed at precise
            # prices instead of the blended average where available.
            state["dimension_usage_io"] = usage_io
        if budget_audit is not None:
            state["exhausted_dimensions"] = budget_audit.exceeded_dimensions
            state["all_dimensions_exhausted"] = budget_audit.all_budgeted_exhausted
            payload["budgetAudit"] = budget_audit.to_dict()
        context.metadata[ITERATE_STATE_KEY] = state
        return _json_output(payload)

    def _dimension_coverage(
        self, context: ToolExecutionContext, effective: config_loader.EffectiveConfig
    ) -> str | None:
        """The kernel's dimensionContext verdict as one line, or ``None`` when unmeasurable.

        PLANNED ids come from the published run-plan file when one exists — that file is
        the contract the GlassPane side actually reads — and from the EFFECTIVE config's
        ``dimensions`` list otherwise. The two can differ (the config was edited after
        ``plan`` published, or no plan was ever written), and the difference is the point:
        the verdict must be computed against what this harness hands over, not against
        what somebody last typed into a YAML file.

        RECORDED counts come from the ledger. An entry whose data carries no valid
        ``dimension`` belongs to no dimension and is not guessed at — the same rule
        ``dimension_context_from_evidence`` applies to evidence packs without the key.
        Returning ``None`` (no line, no invented 0/0) when either half cannot be built
        keeps the compaction attachment honest.

        The assembly lives in :mod:`iterate.dimension_ledger` because the compaction seam
        asks the same question of the same two files; one accounting, two readers.
        """
        return _dimension_coverage_line(context, effective)

    @staticmethod
    def _audit_budgets(usage_totals: dict[str, int], context: ToolExecutionContext) -> review.BudgetAudit | None:
        """Audit reported per-dimension usage against configured budgets.

        Returns ``None`` when no budgets are configured or no usage was
        reported; never raises — budget auditing must not break aggregation.
        """
        budgets = {
            name: res.token_budget
            for name, res in config_loader.load_effective_config(context.cwd)
            .config.dimension_resources.items()
            if res.token_budget is not None
        }
        if not budgets or not usage_totals:
            return None
        return review.audit_dimension_budgets(budgets, usage_totals)

    def _meta_review(self, args: IterateReviewInput, context: ToolExecutionContext) -> ToolResult:
        if not args.report:
            return _json_output({"error": "meta-review requires report"}, error=True)
        parsed = review.report_from_dict(args.report)
        effective = config_loader.load_effective_config(context.cwd)
        thresholds = effective.config.thresholds
        gate = review.evaluate_threshold_gates(thresholds, parsed.findings)
        evidence_audit = None
        if effective.config.reviewer.evidence_validation:
            evidence_audit = evidence.verify_findings(
                context.cwd,
                findings=cast("list[object]", parsed.findings),
                read_set=evidence.read_set_from_metadata(context.metadata),
            )
        # Prompt-informative coverage: when the aggregate report carries the
        # reviewers' merged readFiles, compare the self-reported reads against
        # the assigned scope inventory. Never flips the verdict.
        coverage_result = None
        raw_read_files = (args.report or {}).get("readFiles")
        if (
            effective.config.reviewer.coverage_validation
            and isinstance(raw_read_files, list)
            and raw_read_files
        ):
            assigned = review_scope.collect_scope_files(
                Path(context.cwd), scope=effective.config.review.scope
            )
            coverage_result = review_scope.compute_coverage(assigned, raw_read_files)
        final = meta_review.build_final_review_report(
            parsed,
            threshold_result=None if gate.passed else gate,
            evidence=evidence_audit,
            coverage=coverage_result,
        )
        final_payload: dict[str, Any] = {
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
        if evidence_audit is not None:
            final_payload["evidence"] = evidence_audit.to_dict()
        if coverage_result is not None:
            final_payload["coverage"] = coverage_result.to_dict()
        if not thresholds.is_empty():
            final_payload["thresholdGate"] = gate.to_dict()
        return _json_output({"finalReport": final_payload})


# --- iterate_decision_log ---------------------------------------------------


class IterateDecisionLogInput(BaseModel):
    operation: Literal["append", "read"]
    type: str | None = Field(default=None, description="Entry type (append)")
    round: int | None = Field(default=None, ge=1, description="Round number (append)")
    data: dict[str, Any] | None = Field(default=None, description="Entry payload (append)")
    dimension: str | None = Field(
        default=None,
        description=(
            "Kebab id of the review dimension this decision served, from the run's dimension "
            "config (append). Omit it when the call served no declared dimension — an omitted "
            "id reads as unattributed, never as a zero for some other dimension."
        ),
    )
    operation_id: str | None = Field(
        default=None,
        description=(
            "The engine operation this decision belongs to, copied from the evidence the "
            "engine returned (append). Omit it when the decision is not tied to one operation."
        ),
    )


def _attributed_data(args: IterateDecisionLogInput) -> dict[str, Any] | None:
    """The entry payload with the validated attribution merged into the keys the kernel reads.

    ``dimension``/``operationId`` are the two names ``kernel_decision_record`` transcribes into
    the ledger draft; writing them anywhere else buys an entry that looks attributed to the
    caller and counts as unattributed everywhere else.

    A call with no payload and no attribution stays ``None`` rather than becoming ``{}``, so
    entries that were always written without a payload keep being written without one.
    """
    data = dict(args.data) if isinstance(args.data, dict) else {}
    if args.dimension is not None:
        data[dimension_ledger.DIMENSION_DATA_KEY] = args.dimension
    if args.operation_id is not None:
        data[dimension_ledger.OPERATION_ID_DATA_KEY] = args.operation_id
    return data or None


class IterateDecisionLogTool(BaseTool[BaseModel]):
    name = "iterate_decision_log"
    description = (
        "Append-only decision log (.iterate/decision-log.jsonl). append: "
        "record round_start/review_result/atomic_fix/architectural_fix/revert/"
        "validation/decision/report. read: retrieve all entries. "
        "A decision that served one of the run's review dimensions says which one "
        "(dimension: the kebab id from the dimension config) and, when it settled one "
        "engine operation, which operation (operation_id, copied from the engine's "
        "evidence). Those two names are what lets a later compaction tell a verified "
        "dimension's history from an open one; leave them out when the call served no "
        "declared dimension, and never invent one — a malformed id is refused, not "
        "dropped."
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
        if not decision_log.is_decision_kind(args.type):
            return _json_output(
                {
                    "error": f"'{args.type}' is not a decision kind the shared ledger can carry",
                    "code": "KERNEL_E_DECISION_KIND",
                    "remedy": "a kind is lowercase snake_case, at most 32 characters "
                              "(`^[a-z][a-z0-9_]{0,31}$`). It is no longer checked against a fixed "
                              "list — that list was this shell's opinion and the TypeScript plugin's "
                              "was wider, which was the divergence, not a rule.",
                },
                error=True,
            )
        # The attribution is checked here, before a byte is written, for the reason the
        # transcription is not enough: ``kernel_decision_record`` keeps a valid
        # ``dimension``/``operationId`` and drops an invalid one without a word, so a typo
        # would buy the caller a success and a ledger line that reports its dimension as
        # never verified. A refusal is loud; a dropped attribution is invisible.
        refusal = dimension_ledger.attribution_refusal(
            dimension=args.dimension, operation_id=args.operation_id
        )
        if refusal is not None:
            return _json_output(refusal, error=True)
        # The witnessed-call cross-check, one step later and on the same side of the write. The
        # shell said what the call that produced this operation was declared for; this is the
        # agent's later claim about the same operation. Two independent witnesses disagreeing is
        # not something this harness may adjudicate, and it is not something to keep quietly —
        # so the entry is refused, nothing is written, and both ways forward are named. No
        # observation is no objection: an operation nobody fetched through this shell, or a
        # shell too old to echo, leaves the claim standing exactly as it was before this check.
        contest = dimension_ledger.echo_contestation(
            metadata=context.metadata, dimension=args.dimension, operation_id=args.operation_id
        )
        if contest is not None:
            return _json_output(contest, error=True)
        entry = decision_log.make_entry(
            entry_type=args.type,
            round_number=args.round,
            data=_attributed_data(args),
        )
        try:
            count, path = decision_log.append_entry(context.cwd, entry)
        except decision_log.DECISION_LOG_REFUSALS as error:
            # A decision that cannot be recorded is reported, not swallowed and not raised: the
            # agent is the one who can fix the ledger (permissions, an interleaved writer, a
            # timestamp this kernel cannot date).
            return _json_output(
                {
                    "error": str(error),
                    "code": getattr(error, "code", "KERNEL_E_DECISION_LOG_WRITE"),
                    "remedy": getattr(error, "remedy", ""),
                    "success": False,
                },
                error=True,
            )
        payload: dict[str, Any] = {
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
        if args.dimension is not None:
            # The ledger just gained an attributed decision, so the coverage line — a fact
            # about the ledger — went stale in this same call.
            coverage = _refresh_dimension_coverage(context)
            if coverage is not None:
                payload["dimensionCoverage"] = coverage
        if args.type == "report":
            # The canonical loops append exactly one report entry per run:
            # that is the deterministic hook for the fingerprint trend library.
            payload["trend"] = self._record_trend(context, args.data)
        return _json_output(payload)

    @staticmethod
    def _record_trend(context: ToolExecutionContext, data: dict[str, Any] | None) -> dict[str, Any]:
        """Record the finished run into the trend library (best effort)."""
        raw = data.get("findings") if isinstance(data, dict) else None
        findings = [f for f in raw if isinstance(f, dict)] if isinstance(raw, list) else []
        # A run that only reviewed a subset of dimensions must not erase trend
        # history for the rest: honor the loop-policy state published by the
        # aggregate step (``by_dimension`` lists the dimensions actually seen).
        # When that state is absent (e.g. a resumed legacy session) fall back
        # to the historical full-sweep attribution.
        loop_state = context.metadata.get(ITERATE_STATE_KEY)
        covered: list[str] | None = None
        if isinstance(loop_state, dict):
            by_dim = loop_state.get("by_dimension")
            if isinstance(by_dim, dict):
                # Conservative direction: an empty ``by_dimension`` (a run that
                # surfaced no findings) cannot prove full-surface coverage, so
                # it must not auto-fix lingering open records either.
                covered = [str(d) for d in by_dim.keys() if str(d).strip()]
        try:
            delta = trend_store.record_run(
                context.cwd, findings, covered_dimensions=covered
            )
        except Exception:  # noqa: BLE001 - trend tracking must never break the loop
            return {"error": "trend library update failed"}
        return {
            "new": len(delta.new_findings),
            "fixed": len(delta.fixed_findings),
            "regressed": len(delta.regressed_findings),
            "stubborn": len(delta.stubborn_findings),
        }


# --- record_assumption (defensive kernel, design §20.3.2) -------------------


class IterateAssumptionInput(BaseModel):
    """Arguments for the assumption declaration tool."""

    statement: str = Field(
        min_length=1,
        max_length=500,
        description="The assumption that must hold before the next action "
        "(e.g. 'src/api.py exists', 'git status is clean', 'mypy is installed').",
    )
    status: Literal["declared", "holds", "falsified"] = Field(
        default="declared",
        description="declared: record a new assumption. holds/falsified: "
        "record the verification outcome of a previously declared assumption.",
    )
    detail: str = Field(
        default="",
        max_length=1000,
        description="Optional supporting detail (e.g. the command/check that "
        "confirmed or falsified the assumption).",
    )


class IterateAssumptionTool(BaseTool[BaseModel]):
    name = "record_assumption"
    description = (
        "Declare or verify an assumption (defensive programming, code mode). "
        "Call with status='declared' before an action to make the premise "
        "explicit; call again with status='holds'/'falsified' after checking "
        "it. Every call is appended to the .iterate decision log for a full "
        "audit trail; a falsified assumption means fail-fast: stop and "
        "re-plan, never keep building on a broken premise."
    )
    input_model = IterateAssumptionInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        if not isinstance(arguments, IterateAssumptionInput):
            return _json_output({"error": "invalid arguments"}, error=True)
        if not arguments.statement.strip():
            return _json_output(
                {"error": "statement must not be empty"}, error=True
            )
        try:
            from iterate_harness.defensive.assumptions import (
                record_assumption,
                record_assumption_checked,
            )

            if arguments.status == "declared":
                count, path = record_assumption(
                    context.cwd, arguments.statement, status="declared", detail=arguments.detail
                )
            else:
                count, path = record_assumption_checked(
                    context.cwd,
                    arguments.statement,
                    holds=arguments.status == "holds",
                    detail=arguments.detail,
                )
        except (OSError, *decision_log.DECISION_LOG_REFUSALS) as exc:
            return _json_output(
                {
                    "error": f"could not write decision log: {exc}",
                    "code": getattr(exc, "code", "OSError"),
                    "remedy": getattr(exc, "remedy", "fix the file permissions or ownership"),
                },
                error=True,
            )
        return _json_output(
            {
                "operation": "record_assumption",
                "success": True,
                "statement": arguments.statement,
                "status": arguments.status,
                "entryCount": count,
                "logPath": str(path),
            }
        )


# --- iterate_context --------------------------------------------------------


class IterateContextInput(BaseModel):
    operation: Literal["read"] = "read"


class IterateContextTool(BaseTool[BaseModel]):
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


# --- iterate_triage -----------------------------------------------------------

#: Interactive triage cap: bounds the per-finding ask loop so a runaway
#: report cannot lock the session in an endless prompt sequence.
MAX_TRIAGE_FINDINGS = 50

TRIAGE_ANSWER_MAP: dict[str, TriageDecision] = {
    "y": "fix",
    "yes": "fix",
    "fix": "fix",
    "n": "skip",
    "no": "skip",
    "skip": "skip",
    "a": "ignore",
    "always": "ignore",
    "ignore": "ignore",
}

TRIAGE_IGNORE_REASON = "triaged as intentional via iterate_triage"


class TriageFindingItem(BaseModel):
    """One aggregated finding presented for triage."""

    file: str = Field(..., min_length=1)
    dimension: str = Field(..., min_length=1)
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    summary: str = Field(..., min_length=1)
    line: int | None = Field(default=None, ge=1)


class IterateTriageInput(BaseModel):
    findings: list[TriageFindingItem] = Field(..., min_length=1)
    default: Literal["fix", "skip", "ignore"] = Field(
        default="fix",
        description="Decision applied when the user is unavailable (headless) or skips.",
    )
    round: int = Field(default=1, ge=1, description="Decision-log round number.")
    note: str = Field(default="", description="Reason recorded for 'ignore' entries.")


def _parse_triage_answer(raw: str) -> TriageDecision | None:
    """Map a raw user answer to fix/skip/ignore; None when unrecognized."""
    token = raw.strip().lower()
    if not token:
        return None
    if token in TRIAGE_ANSWER_MAP:
        return TRIAGE_ANSWER_MAP[token]
    # Accept decorated single letters ("y.", "a)", "n - lint noise").
    head = token.split(None, 1)[0].rstrip(".,);:")
    if head in TRIAGE_ANSWER_MAP:
        return TRIAGE_ANSWER_MAP[head]
    return None


def _triage_question(index: int, total: int, finding: TriageFindingItem) -> str:
    location = f"{finding.file}:{finding.line}" if finding.line else finding.file
    return (
        f"Triage finding {index}/{total} — [y=fix n=skip a=always-ignore] "
        f"{finding.severity} {finding.dimension} {location}: {finding.summary}"
    )


class IterateTriageTool(BaseTool[BaseModel]):
    name = "iterate_triage"
    description = (
        "Interactive findings triage after a review: walk each finding with "
        "y (fix) / n (skip) / a (always-ignore). 'a' persists the finding to "
        "known_intentional personalization so future review rounds filter it "
        "out automatically; every decision is recorded in the decision log. "
        "Headless sessions apply the 'default' decision to all findings."
    )
    input_model = IterateTriageInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return False

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        if not isinstance(arguments, IterateTriageInput):
            return _json_output({"error": "invalid arguments"}, error=True)
        if len(arguments.findings) > MAX_TRIAGE_FINDINGS:
            return _json_output(
                {
                    "error": f"too many findings ({len(arguments.findings)}); "
                    f"triage at most {MAX_TRIAGE_FINDINGS} per call"
                },
                error=True,
            )

        ask_user = context.metadata.get("ask_user_prompt")
        interactive = callable(ask_user)
        decisions: list[dict[str, Any]] = []
        by_decision: dict[str, list[int]] = {"fix": [], "skip": [], "ignore": []}

        for index, finding in enumerate(arguments.findings, start=1):
            decision = arguments.default
            if interactive:
                answer = str(
                    await ask_user(  # type: ignore[misc]
                        _triage_question(index, len(arguments.findings), finding)
                    )
                )
                decision = _parse_triage_answer(answer) or arguments.default
            by_decision[decision].append(index)
            decisions.append(
                {
                    "index": index,
                    "file": finding.file,
                    "dimension": finding.dimension,
                    "severity": finding.severity,
                    "line": finding.line,
                    "summary": finding.summary,
                    "decision": decision,
                }
            )

        ignored = [arguments.findings[i - 1] for i in by_decision["ignore"]]
        persisted = self._persist_ignores(context, ignored, arguments.note)

        entry = decision_log.make_entry(
            entry_type="decision",
            round_number=arguments.round,
            data={
                "kind": "triage",
                "interactive": interactive,
                "default": arguments.default,
                "counts": {name: len(ids) for name, ids in by_decision.items()},
                "persistedKnownIntentional": persisted,
                "decisions": decisions,
            },
        )
        count, log_path = decision_log.append_entry(context.cwd, entry)

        return _json_output(
            {
                "triage": {
                    "interactive": interactive,
                    "total": len(arguments.findings),
                    "counts": {name: len(ids) for name, ids in by_decision.items()},
                    "fixIndexes": by_decision["fix"],
                    "skipIndexes": by_decision["skip"],
                    "ignoreIndexes": by_decision["ignore"],
                    "decisions": decisions,
                },
                "persistedKnownIntentional": persisted,
                "decisionLogEntryCount": count,
                "logPath": str(log_path),
            }
        )

    def _persist_ignores(
        self,
        context: ToolExecutionContext,
        ignored: list[TriageFindingItem],
        note: str,
    ) -> int:
        """Append new known_intentional entries (deduped); return added count."""
        if not ignored:
            return 0
        data = personalization.load(None, context.cwd)
        existing = {(k.file, k.dimension, k.line) for k in data.known_intentional}
        added = 0
        for finding in ignored:
            key = (finding.file, finding.dimension, finding.line)
            if key in existing:
                continue
            data.known_intentional.append(
                itypes.KnownIntentional(
                    file=finding.file,
                    dimension=finding.dimension,
                    reason=note.strip() or TRIAGE_IGNORE_REASON,
                    line=finding.line,
                )
            )
            existing.add(key)
            added += 1
        if added:
            personalization.save(None, context.cwd, data)
            # Config bridge (#8): sync triaged entries into iterate.config.yaml
            sync_result = personalization.sync_known_intentional_to_config(
                context.cwd, data.known_intentional
            )
            config_note = sync_result.get("config") or sync_result.get("reason", "?")
            added_entries = sync_result.get("added")
            if isinstance(added_entries, list) and added_entries:
                log.info(
                    "config-bridge: %d new known_intentional entries synced to %s",
                    len(added_entries),
                    config_note,
                )
        return added


__all__ = [
    "IterateAssumptionTool",
    "IterateConfigTool",
    "IterateContextTool",
    "IterateDecisionLogTool",
    "IterateReviewTool",
    "IterateTriageTool",
    "IterateValidateTool",
]
