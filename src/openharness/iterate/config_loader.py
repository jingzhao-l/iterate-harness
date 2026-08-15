"""Configuration loading for the iterate semantic layer.

Python port of ``harness/iterate-plugin/src/config-loader.ts``.

Semantics preserved from the TS plugin:

- ``default_config()`` is the "Master" config: every missing key of a
  project's ``iterate.config.yaml`` is filled from here, so the harness is
  usable out of the box while never inventing trusted validation commands
  (they must be configured explicitly).
- ``merge_config()`` deep-merges plain mappings recursively; arrays and
  scalars are replaced wholesale by the override (arrays are NOT
  concatenated). Neither input is mutated.
- ``is_command_allowed()`` requires an EXACT match (after trim) against the
  predefined ``validation.commands`` entries — prefixes are never enough.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import yaml

from .types import (
    SEVERITY_METRICS,
    AtomicConfig,
    DimensionResources,
    DimensionThresholds,
    GitConfig,
    IterateConfig,
    ReviewerConfig,
    ReviewScopeConfig,
    ThresholdsConfig,
    ValidationConfig,
)

CONFIG_FILENAME = "iterate.config.yaml"

#: Bounds for per-dimension concurrency overrides (validated at load time).
MIN_DIMENSION_CONCURRENCY = 1
MAX_DIMENSION_CONCURRENCY = 8


@dataclass
class EffectiveConfig:
    """Result of :func:`load_effective_config`."""

    config: IterateConfig
    source: str  # "defaults" | "override"
    override: dict[str, object] | None


def load_config(project_root: str | Path) -> dict[str, object] | None:
    """Load and parse ``iterate.config.yaml`` from the project root.

    Returns ``None`` if the file is missing or does not parse to a mapping.
    """
    path = Path(project_root) / CONFIG_FILENAME
    try:
        content = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def default_config() -> IterateConfig:
    """Return a fresh ``IterateConfig`` with every field at its default.

    Security: the defaults configure NO trusted validation commands —
    ``command_whitelist`` is empty and ``commands`` is empty, so nothing
    untrusted can execute until a project opts in.
    """
    return IterateConfig()


def _default_config_dict() -> dict[str, object]:
    """Dict form of :func:`default_config` (the merge substrate)."""
    cfg = default_config()
    return {
        "goal": cfg.goal,
        "max_rounds": cfg.max_rounds,
        "language": cfg.language,
        "dimensions": list(cfg.dimensions),
        "review": {"scope": cfg.review.scope},
        "atomic": {
            "max_lines": cfg.atomic.max_lines,
            "max_adjacent_methods": cfg.atomic.max_adjacent_methods,
        },
        "git": {
            "target_branch": cfg.git.target_branch,
            "use_worktree": cfg.git.use_worktree,
            "push_per_round": cfg.git.push_per_round,
            "auto_merge": cfg.git.auto_merge,
        },
        "validation": {
            "command_whitelist": list(cfg.validation.command_whitelist),
            "commands": dict(cfg.validation.commands),
        },
        "reviewer": {"output_schema_validation": cfg.reviewer.output_schema_validation},
        "dimension_resources": {
            name: resources_to_dict(res) for name, res in cfg.dimension_resources.items()
        },
        "token_budget": cfg.token_budget,
        "thresholds": thresholds_to_dict(cfg.thresholds),
    }


def parse_dimension_resources(
    raw: object,
) -> tuple[dict[str, DimensionResources], list[str]]:
    """Parse the ``dimension_resources`` mapping defensively.

    Returns ``(resources, errors)``: unknown-key/invalid-value entries are
    reported as errors and skipped, never raise — a typo in the yaml must
    not kill the whole loop.
    """
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["dimension_resources must be a mapping"]

    resources: dict[str, DimensionResources] = {}
    errors: list[str] = []
    for name, value in raw.items():
        dim = str(name)
        if not isinstance(value, dict):
            errors.append(f"dimension_resources.{dim} must be a mapping")
            continue
        model = value.get("model")
        if model is not None and not isinstance(model, str):
            errors.append(f"dimension_resources.{dim}.model must be a string")
            model = None
        concurrency = value.get("concurrency")
        if concurrency is not None:
            if not isinstance(concurrency, int) or isinstance(concurrency, bool):
                errors.append(f"dimension_resources.{dim}.concurrency must be an integer")
                concurrency = None
            else:
                concurrency = max(
                    MIN_DIMENSION_CONCURRENCY, min(MAX_DIMENSION_CONCURRENCY, concurrency)
                )
        token_budget = value.get("token_budget")
        if token_budget is not None and (
            not isinstance(token_budget, int)
            or isinstance(token_budget, bool)
            or token_budget < 0
        ):
            errors.append(
                f"dimension_resources.{dim}.token_budget must be a non-negative integer"
            )
            token_budget = None
        resources[dim] = DimensionResources(
            model=model or None,
            concurrency=concurrency,
            token_budget=token_budget,
        )
    return resources, errors


def resources_to_dict(resources: DimensionResources) -> dict[str, object]:
    """Serialize DimensionResources back to its yaml shape (set fields only)."""
    out: dict[str, object] = {}
    if resources.model is not None:
        out["model"] = resources.model
    if resources.concurrency is not None:
        out["concurrency"] = resources.concurrency
    if resources.token_budget is not None:
        out["token_budget"] = resources.token_budget
    return out


def parse_token_budget(raw: object) -> tuple[int | None, list[str]]:
    """Parse the whole-run ``token_budget`` (positive integer, ``None``=off)."""
    if raw is None:
        return None, []
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        return None, ["token_budget must be a positive integer"]
    return raw, []


def _parse_threshold_metric(
    raw: object,
    field_name: str,
    errors: list[str],
) -> int | None:
    """Parse one ``max_<metric>`` threshold (non-negative int, defensive)."""
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        errors.append(f"{field_name} must be a non-negative integer")
        return None
    return raw


def _parse_dimension_thresholds(dim: str, raw: object, errors: list[str]) -> DimensionThresholds:
    """Parse one ``thresholds.dimensions.<dim>`` mapping defensively."""
    if not isinstance(raw, dict):
        errors.append(f"thresholds.dimensions.{dim} must be a mapping")
        return DimensionThresholds()
    values = {
        f"max_{metric}": _parse_threshold_metric(
            raw.get(f"max_{metric}"), f"thresholds.dimensions.{dim}.max_{metric}", errors
        )
        for metric in SEVERITY_METRICS
    }
    return DimensionThresholds(**values)


def parse_thresholds(raw: object) -> tuple[ThresholdsConfig, list[str]]:
    """Parse the ``thresholds`` mapping defensively.

    Returns ``(thresholds, errors)``: invalid entries are reported as errors
    and skipped, never raised — a typo in the yaml must not kill the loop.
    """
    if raw is None:
        return ThresholdsConfig(), []
    if not isinstance(raw, dict):
        return ThresholdsConfig(), ["thresholds must be a mapping"]

    errors: list[str] = []
    global_values = {
        f"max_{metric}": _parse_threshold_metric(
            raw.get(f"max_{metric}"), f"thresholds.max_{metric}", errors
        )
        for metric in SEVERITY_METRICS
    }

    dimensions: dict[str, DimensionThresholds] = {}
    dims_raw = raw.get("dimensions")
    if dims_raw is not None:
        if not isinstance(dims_raw, dict):
            errors.append("thresholds.dimensions must be a mapping")
        else:
            for name, value in dims_raw.items():
                dimensions[str(name)] = _parse_dimension_thresholds(str(name), value, errors)

    return (
        ThresholdsConfig(
            dimensions=dimensions,
            **global_values,
        ),
        errors,
    )


def thresholds_to_dict(thresholds: ThresholdsConfig) -> dict[str, object]:
    """Serialize ThresholdsConfig back to its yaml shape (set fields only)."""
    out: dict[str, object] = {}
    for metric in SEVERITY_METRICS:
        value = getattr(thresholds, f"max_{metric}")
        if value is not None:
            out[f"max_{metric}"] = value
    if thresholds.dimensions:
        out["dimensions"] = {
            name: {
                f"max_{metric}": value
                for metric in SEVERITY_METRICS
                if (value := getattr(dim, f"max_{metric}")) is not None
            }
            for name, dim in thresholds.dimensions.items()
        }
    return out


def merge_config(
    base: dict[str, object],
    override: dict[str, object] | None,
) -> dict[str, object]:
    """Recursively merge ``override`` on top of ``base``.

    - Missing keys in ``base`` are added from ``override``.
    - Present keys in ``override`` win.
    - Plain mappings are merged recursively; arrays and scalars are replaced
      wholesale by the override (arrays are NOT concatenated).
    - ``None`` values in the override are treated as "not set" and skipped.

    Returns a NEW dict; neither input is mutated.
    """
    if not isinstance(override, dict):
        return copy.deepcopy(base)
    out: dict[str, object] = copy.deepcopy(base)
    for key, value in override.items():
        if value is None:
            continue
        base_value = out.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            out[key] = merge_config(base_value, value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def config_from_dict(data: dict[str, object] | None) -> IterateConfig:
    """Build a typed :class:`IterateConfig` from a (merged) raw dict.

    Missing or ``None`` keys fall back to the defaults; this mirrors the TS
    side where the merged object is trusted to satisfy the config shape.
    """
    defaults = default_config()
    if not isinstance(data, dict):
        return defaults

    review_raw = data.get("review")
    review = defaults.review
    if isinstance(review_raw, dict) and isinstance(review_raw.get("scope"), str):
        review = ReviewScopeConfig(scope=review_raw["scope"])

    atomic_raw = data.get("atomic")
    if isinstance(atomic_raw, dict):
        atomic = AtomicConfig(
            max_lines=atomic_raw.get("max_lines", defaults.atomic.max_lines),
            max_adjacent_methods=atomic_raw.get(
                "max_adjacent_methods", defaults.atomic.max_adjacent_methods
            ),
        )
    else:
        atomic = defaults.atomic

    git_raw = data.get("git")
    if isinstance(git_raw, dict):
        git = GitConfig(
            target_branch=git_raw.get("target_branch", defaults.git.target_branch),
            use_worktree=git_raw.get("use_worktree", defaults.git.use_worktree),
            push_per_round=git_raw.get("push_per_round", defaults.git.push_per_round),
            auto_merge=git_raw.get("auto_merge", defaults.git.auto_merge),
        )
    else:
        git = defaults.git

    validation_raw = data.get("validation")
    if isinstance(validation_raw, dict):
        whitelist = validation_raw.get("command_whitelist") or []
        commands = validation_raw.get("commands") or {}
        validation = ValidationConfig(
            command_whitelist=list(whitelist) if isinstance(whitelist, list) else [],
            commands=dict(commands) if isinstance(commands, dict) else {},
        )
    else:
        validation = defaults.validation

    reviewer_raw = data.get("reviewer")
    if isinstance(reviewer_raw, dict):
        reviewer = ReviewerConfig(
            output_schema_validation=reviewer_raw.get(
                "output_schema_validation", defaults.reviewer.output_schema_validation
            )
        )
    else:
        reviewer = defaults.reviewer

    dimensions = data.get("dimensions")
    onboarding = data.get("onboarding")
    personalization = data.get("personalization")
    dimension_resources, _resource_errors = parse_dimension_resources(
        data.get("dimension_resources")
    )
    token_budget, _budget_errors = parse_token_budget(data.get("token_budget"))
    thresholds, _threshold_errors = parse_thresholds(data.get("thresholds"))

    return IterateConfig(
        goal=data.get("goal", defaults.goal),
        max_rounds=data.get("max_rounds", defaults.max_rounds),
        language=data.get("language", defaults.language),
        dimensions=list(dimensions) if isinstance(dimensions, list) else list(defaults.dimensions),
        review=review,
        atomic=atomic,
        git=git,
        validation=validation,
        reviewer=reviewer,
        dimension_resources=dimension_resources,
        token_budget=token_budget,
        thresholds=thresholds,
        onboarding=dict(onboarding) if isinstance(onboarding, dict) else None,
        personalization=dict(personalization) if isinstance(personalization, dict) else None,
    )


def load_effective_config(project_root: str | Path) -> EffectiveConfig:
    """Load the EFFECTIVE config for a project.

    Project-root overrides are merged on top of the built-in defaults
    ("Master + Overrides"). Never fails — a project without a config file
    simply runs on the defaults (with an empty validation command set, so
    nothing untrusted can ever execute).
    """
    override = load_config(project_root)
    if override is None:
        return EffectiveConfig(config=default_config(), source="defaults", override=None)
    merged = merge_config(_default_config_dict(), override)
    return EffectiveConfig(
        config=config_from_dict(merged),
        source="override",
        override=override,
    )


def is_command_allowed(command: str, predefined_commands: list[str]) -> bool:
    """Check whether a command is in the predefined commands list.

    A command is allowed only if it is EXACTLY (after trim) listed. This
    replaces the old prefix-based whitelist at runtime —
    ``command_whitelist`` is still used for config-time validation only.
    """
    if not isinstance(command, str):
        return False
    return command.strip() in predefined_commands


def flatten_commands(commands: dict[str, list[str]] | None) -> list[str]:
    """Flatten all commands from ``validation.commands`` into one array.

    Malformed entries (values that are not arrays) are ignored rather than
    raising, mirroring the TS plugin's defensive behavior.
    """
    if not isinstance(commands, dict):
        return []
    out: list[str] = []
    for value in commands.values():
        if isinstance(value, list):
            out.extend(str(cmd) for cmd in value)
    return out


def validate_config(config: object) -> list[str]:
    """Validate that the config has all required fields.

    Returns a list of missing field paths (empty list means valid).
    """
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["root"]

    if not config.get("goal"):
        errors.append("goal")
    if not isinstance(config.get("dimensions"), list):
        errors.append("dimensions")

    validation = config.get("validation")
    if not isinstance(validation, dict):
        errors.append("validation")
    else:
        if not isinstance(validation.get("command_whitelist"), list):
            errors.append("validation.command_whitelist")
        if not isinstance(validation.get("commands"), dict):
            errors.append("validation.commands")

    raw_resources = config.get("dimension_resources")
    if raw_resources is not None:
        _, resource_errors = parse_dimension_resources(raw_resources)
        errors.extend(resource_errors)
    _, budget_errors = parse_token_budget(config.get("token_budget"))
    errors.extend(budget_errors)
    raw_thresholds = config.get("thresholds")
    if raw_thresholds is not None:
        _, threshold_errors = parse_thresholds(raw_thresholds)
        errors.extend(threshold_errors)
    return errors
