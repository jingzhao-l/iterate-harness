"""Iterate semantic layer (Python port of the dsh iterate-plugin core).

Pure, deterministic modules ported from ``harness/iterate-plugin/src``:

- :mod:`.types` — dataclasses mirroring the plugin's TS types
- :mod:`.config_loader` — Master+Overrides config loading and merging
- :mod:`.review` — dedupe / sort / convergence / report assembly
- :mod:`.meta_review` — deterministic audit of a review report
- :mod:`.validate` — exact-match validation command runner
- :mod:`.decision_log` — append-only JSONL decision log
- :mod:`.settings` — kernel Settings bridge (IterateSettings)
- :mod:`.loop_policy` — engine-level convergence enforcement
- :mod:`.cost` — USD money layer over token usage
- :mod:`.personalization` — 9-category per-project personalization
- :mod:`.onboarding` / :mod:`.onboard_cmd` — ITERATE.md knowledge base + fingerprints
- :mod:`.personalize_cmd` — skill-parity 9-category personalize wizard
- :mod:`.personalize_tui` — directional-key personalize wizard for the TUI
- :mod:`.dimension_check` — skill↔harness dimension-system consistency doctor
- :mod:`.prompts` — canonical workflow prompt templates
- :mod:`.trend_store` — finding fingerprint trend library (new/fixed/stubborn)
- :mod:`.worktree_flow` — fix-round git isolation orchestration

This ``__init__`` uses PEP 562 lazy attribute resolution: the kernel's
``config.settings`` imports ``iterate.settings`` at module load, so eager
re-exports here would create an import cycle
(config.settings → iterate → cost → api → auth → config.settings).
"""

from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, str] = {
    # config_loader
    "EffectiveConfig": ".config_loader",
    "default_config": ".config_loader",
    "flatten_commands": ".config_loader",
    "is_command_allowed": ".config_loader",
    "load_config": ".config_loader",
    "load_effective_config": ".config_loader",
    "merge_config": ".config_loader",
    "validate_config": ".config_loader",
    # cost
    "CostMeter": ".cost",
    "ModelUsage": ".cost",
    "price_for": ".cost",
    # checkpoint
    "clear_checkpoint": ".checkpoint",
    "load_checkpoint": ".checkpoint",
    "save_checkpoint": ".checkpoint",
    # decision_log
    "append_entry": ".decision_log",
    "log_path": ".decision_log",
    "make_entry": ".decision_log",
    "read_entries": ".decision_log",
    # personalize_tui
    "project_root_guard": ".personalize_tui",
    "run_tui_personalize": ".personalize_tui",
    "summarize_changes": ".personalize_tui",
    # dimension_check
    "DimensionDoctorReport": ".dimension_check",
    "load_canonical_dimensions": ".dimension_check",
    "render_doctor_report": ".dimension_check",
    "run_dimension_doctor": ".dimension_check",
    # loop_policy
    "AggregateSnapshot": ".loop_policy",
    "ITERATE_STATE_KEY": ".loop_policy",
    "IterateLoopPolicy": ".loop_policy",
    "LoopDecision": ".loop_policy",
    # meta_review
    "META_REVIEW_CHECKS": ".meta_review",
    "build_final_review_report": ".meta_review",
    "meta_review_report": ".meta_review",
    # pr_comment
    "PR_COMMENT_MARKER": ".pr_comment",
    "PostResult": ".pr_comment",
    "post_pr_comment": ".pr_comment",
    "render_markdown": ".pr_comment",
    # prompts
    "ITERATE_SKILL_PROMPT": ".prompts",
    "TEMPLATE_PRESETS": ".prompts",
    "convergence_stop_notice": ".prompts",
    "dry_run_kickoff": ".prompts",
    "list_templates": ".prompts",
    "next_round_instruction": ".prompts",
    "normal_kickoff": ".prompts",
    "personalization_constraints": ".prompts",
    # review
    "SEVERITY_RANK": ".review",
    "aggregate_rounds": ".review",
    "build_review_plan": ".review",
    "build_review_report": ".review",
    "compute_convergence": ".review",
    "dedupe_findings": ".review",
    "filter_known_intentional": ".review",
    "finding_key": ".review",
    "findings_schema": ".review",
    "normalize_summary": ".review",
    "parse_attachments": ".review",
    "plan_to_dict": ".review",
    "report_from_dict": ".review",
    "report_to_dict": ".review",
    "reviewer_task_prompt": ".review",
    "ReviewAttachment": ".review",
    "sort_findings": ".review",
    # settings
    "IterateSettings": ".settings",
    "effective_review_rounds": ".settings",
    "project_config": ".settings",
    # trend_store
    "RunDiff": ".trend_store",
    "TrendDelta": ".trend_store",
    "TrendRecord": ".trend_store",
    "finding_fingerprint": ".trend_store",
    "load_library": ".trend_store",
    "diff_runs": ".trend_store",
    "record_run": ".trend_store",
    "render_diff": ".trend_store",
    "render_trend_summary": ".trend_store",
    "summarize": ".trend_store",
    # types
    "IterateConfig": ".types",
    "KnownIntentional": ".types",
    "ReviewFinding": ".types",
    "ReviewReport": ".types",
    "ReviewRound": ".types",
    "ValidationResult": ".types",
    # validate
    "run_command": ".validate",
    "run_validation": ".validate",
}

__all__ = [
    "ITERATE_SKILL_PROMPT",
    "ITERATE_STATE_KEY",
    "TEMPLATE_PRESETS",
    "META_REVIEW_CHECKS",
    "PR_COMMENT_MARKER",
    "SEVERITY_RANK",
    "AggregateSnapshot",
    "CostMeter",
    "DimensionDoctorReport",
    "EffectiveConfig",
    "IterateConfig",
    "IterateLoopPolicy",
    "IterateSettings",
    "KnownIntentional",
    "LoopDecision",
    "ModelUsage",
    "PostResult",
    "ReviewFinding",
    "ReviewReport",
    "ReviewRound",
    "RunDiff",
    "TrendDelta",
    "TrendRecord",
    "ValidationResult",
    "ReviewAttachment",
    "aggregate_rounds",
    "append_entry",
    "build_final_review_report",
    "build_review_plan",
    "build_review_report",
    "clear_checkpoint",
    "compute_convergence",
    "convergence_stop_notice",
    "dedupe_findings",
    "default_config",
    "diff_runs",
    "dry_run_kickoff",
    "effective_review_rounds",
    "filter_known_intentional",
    "finding_fingerprint",
    "finding_key",
    "findings_schema",
    "flatten_commands",
    "is_command_allowed",
    "load_canonical_dimensions",
    "load_config",
    "load_effective_config",
    "list_templates",
    "load_library",
    "log_path",
    "load_checkpoint",
    "make_entry",
    "merge_config",
    "meta_review_report",
    "next_round_instruction",
    "normal_kickoff",
    "normalize_summary",
    "personalization_constraints",
    "parse_attachments",
    "plan_to_dict",
    "post_pr_comment",
    "price_for",
    "project_config",
    "project_root_guard",
    "read_entries",
    "record_run",
    "render_doctor_report",
    "render_diff",
    "render_markdown",
    "render_trend_summary",
    "report_from_dict",
    "report_to_dict",
    "reviewer_task_prompt",
    "run_command",
    "run_dimension_doctor",
    "run_tui_personalize",
    "run_validation",
    "save_checkpoint",
    "sort_findings",
    "summarize",
    "summarize_changes",
    "validate_config",
]


def __getattr__(name: str) -> Any:
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_path, __package__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
