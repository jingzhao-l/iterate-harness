"""Iterate semantic layer (Python port of the dsh iterate-plugin core).

Pure, deterministic modules ported from ``harness/iterate-plugin/src``:

- :mod:`.types` — dataclasses mirroring the plugin's TS types
- :mod:`.config_loader` — Master+Overrides config loading and merging
- :mod:`.review` — dedupe / sort / convergence / report assembly
- :mod:`.meta_review` — deterministic audit of a review report
- :mod:`.validate` — exact-match validation command runner
- :mod:`.decision_log` — append-only JSONL decision log
"""

from .config_loader import (
    EffectiveConfig,
    default_config,
    flatten_commands,
    is_command_allowed,
    load_config,
    load_effective_config,
    merge_config,
    validate_config,
)
from .decision_log import append_entry, log_path, make_entry, read_entries
from .meta_review import META_REVIEW_CHECKS, build_final_review_report, meta_review_report
from .review import (
    SEVERITY_RANK,
    aggregate_rounds,
    build_review_plan,
    build_review_report,
    compute_convergence,
    dedupe_findings,
    filter_known_intentional,
    finding_key,
    findings_schema,
    normalize_summary,
    reviewer_task_prompt,
    sort_findings,
)
from .types import (
    IterateConfig,
    KnownIntentional,
    ReviewFinding,
    ReviewReport,
    ReviewRound,
    ValidationResult,
)
from .validate import run_command, run_validation

__all__ = [
    "META_REVIEW_CHECKS",
    "SEVERITY_RANK",
    "EffectiveConfig",
    "IterateConfig",
    "KnownIntentional",
    "ReviewFinding",
    "ReviewReport",
    "ReviewRound",
    "ValidationResult",
    "aggregate_rounds",
    "append_entry",
    "build_final_review_report",
    "build_review_plan",
    "build_review_report",
    "compute_convergence",
    "dedupe_findings",
    "default_config",
    "filter_known_intentional",
    "finding_key",
    "findings_schema",
    "flatten_commands",
    "is_command_allowed",
    "load_config",
    "load_effective_config",
    "log_path",
    "make_entry",
    "merge_config",
    "meta_review_report",
    "normalize_summary",
    "read_entries",
    "reviewer_task_prompt",
    "run_command",
    "run_validation",
    "sort_findings",
    "validate_config",
]
