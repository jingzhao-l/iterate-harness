"""Iterate settings bridging the kernel ``Settings`` with iterate.config.yaml.

The kernel-level :class:`~iterate_harness.config.settings.Settings` gains an
``iterate`` field whose type lives here (design §11.4.1 kernel fix #4), so
iterate harness knobs (protected paths, review rounds, price overrides)
round-trip through the standard settings load/save machinery, while the
*project* semantic config still comes from ``iterate.config.yaml`` via
:mod:`.config_loader` (Master + Overrides).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .config_loader import EffectiveConfig, load_effective_config

DEFAULT_MAX_REVIEW_ROUNDS = 3
DEFAULT_ATOMIC_MAX_LINES = 20


class IterateSettings(BaseModel):
    """Harness-level iterate settings (stored in kernel Settings)."""

    enabled: bool = True
    # Deterministic review-loop caps (project config may lower them).
    max_review_rounds: int = DEFAULT_MAX_REVIEW_ROUNDS
    # Per-fix diff approval: during a normal-mode iterate loop, every mutating
    # file tool call (file_write / file_edit / notebook_edit) requires an
    # interactive confirmation whose reason embeds a clipped unified diff of
    # the payload. Headless runs (no interactive prompt) auto-approve.
    require_fix_approval: bool = False
    # Hard security boundaries enforced by hooks/permissions, not prompts.
    protected_paths: list[str] = Field(
        default_factory=lambda: [".env", "*.key", "*.pem", "credentials*"]
    )
    # Paths where any fix requires explicit approval (risk areas).
    risk_area_paths: list[str] = Field(default_factory=list)
    # Regex patterns; a fix diff matching any pattern is rejected outright.
    forbidden_fix_patterns: list[str] = Field(default_factory=list)
    # Per-MTok USD price overrides keyed by model name (falls back to the
    # built-in table in iterate.cost).
    price_overrides: dict[str, tuple[float, float]] = Field(default_factory=dict)
    # Whole-run monetary budget (USD): the engine-level loop policy hard-stops
    # once the accumulated cost exceeds it. ``None`` disables the cap.
    budget_usd: float | None = None
    # Turn-level rate cap (requests per minute) for long-running loops.
    # ``None`` disables throttling.
    max_turns_per_minute: int | None = None
    # Stall-awareness (design §18.5): pause after this many consecutive
    # rounds with zero new findings (and no explicit converged flag) and ask
    # the user how to continue. ``0`` disables (loop stops on the first zero
    # round, historical behavior).
    stall_pause_rounds: int = 2
    # Budget-headroom pause (design §18.5): pause before a round when the
    # remaining token/USD budget can't cover at least this many rounds at the
    # recent burn rate, asking whether to top up / stop. ``0`` disables.
    budget_pause_min_rounds: int = 1
    # Session workspace isolation (design §11.3.2 finding #7): when True and a
    # normal-mode iterate loop is active, the engine swaps the loop's working
    # directory to a dedicated ``iterate/round-N`` git worktree so concurrent
    # sessions never write the same files. Fixes are merged back on a
    # successful stop and dropped on an abnormal one (auto-rollback).
    worktree_isolation: bool = False
    # Personalization data directory override (default ~/.iterate-harness/iterate).
    data_dir: str | None = None


def project_config(project_root: str | Path) -> EffectiveConfig:
    """Load the effective project iterate config (defaults + overrides).

    Never fails: a project without ``iterate.config.yaml`` runs on the
    built-in defaults with an EMPTY validation command set, so nothing
    untrusted can execute.
    """
    return load_effective_config(project_root)


def effective_review_rounds(settings: IterateSettings, effective: EffectiveConfig) -> int:
    """Resolve the review-round cap: min of harness cap and project max_rounds.

    The harness cap keeps the engine-level loop policy bounded even when a
    project config declares a very large ``max_rounds``.
    """
    project_cap = effective.config.max_rounds
    return max(1, min(settings.max_review_rounds, project_cap))
