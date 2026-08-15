"""Money layer on top of the kernel's token-only usage tracking.

The upstream ``CostTracker`` accumulates tokens but never converts them to
money (design §11.3.2 finding #6). :class:`CostMeter` fills that gap: it
consumes the same :class:`~openharness.api.usage.UsageSnapshot` stream and
maintains per-model token totals plus a running USD cost using a built-in
price table (USD per million tokens, ``(input, output)``).

Prices are compile-time constants for well-known model families; unknown
models fall back to a conservative default and can be overridden via
``IterateSettings.price_overrides``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..api.usage import UsageSnapshot

#: USD per 1M tokens: {model pattern: (input_price, output_price)}.
#: Ordered; the FIRST matching pattern wins (most specific first).
DEFAULT_PRICE_TABLE: tuple[tuple[str, tuple[float, float]], ...] = (
    ("claude-opus-4", (15.0, 75.0)),
    ("claude-sonnet-4", (3.0, 15.0)),
    ("claude-haiku-4", (0.8, 4.0)),
    ("claude-3-5-haiku", (0.8, 4.0)),
    ("claude-3-5-sonnet", (3.0, 15.0)),
    ("claude-3-opus", (15.0, 75.0)),
    ("gpt-5", (5.0, 15.0)),
    ("gpt-4.1", (2.5, 10.0)),
    ("gpt-4o", (2.5, 10.0)),
    ("gpt-4", (10.0, 30.0)),
    ("deepseek-chat", (0.27, 1.1)),
    ("deepseek-reasoner", (0.55, 2.19)),
    ("glm-4", (0.6, 2.2)),
    ("kimi-k2", (0.6, 2.5)),
    ("moonshot", (0.9, 3.0)),
    ("minimax", (0.5, 1.8)),
    ("qwen", (0.5, 1.5)),
    ("llama", (0.3, 0.9)),
)

#: Fallback price when no pattern matches (conservative mid-tier).
FALLBACK_PRICE: tuple[float, float] = (3.0, 15.0)

TOKENS_PER_MILLION = 1_000_000


def price_for(
    model: str,
    overrides: dict[str, tuple[float, float]] | None = None,
) -> tuple[float, float]:
    """Return the ``(input, output)`` USD price per 1M tokens for a model.

    Exact overrides win, then the first matching built-in pattern, then the
    conservative fallback.
    """
    if overrides:
        exact = overrides.get(model)
        if exact is not None:
            return (float(exact[0]), float(exact[1]))
    lowered = (model or "").lower()
    for pattern, price in DEFAULT_PRICE_TABLE:
        if pattern in lowered:
            return price
    return FALLBACK_PRICE


@dataclass
class ModelUsage:
    """Token and cost totals for one model."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class CostMeter:
    """Accumulate usage snapshots into per-model token totals and USD cost."""

    price_overrides: dict[str, tuple[float, float]] = field(default_factory=dict)
    _by_model: dict[str, ModelUsage] = field(default_factory=dict)
    # Per-reviewer-dimension cumulative token totals (monotonic max — the
    # aggregate tool reports each dimension's RUNNING total every round, so
    # plain addition would double-count). Reviewer subagents bill outside the
    # main API stream, so these are reported figures, not metered ones.
    _dimension_tokens: dict[str, int] = field(default_factory=dict)

    def accumulate(self, usage: UsageSnapshot, model: str) -> None:
        """Add one usage snapshot billed against ``model``."""
        entry = self._by_model.setdefault(model, ModelUsage(model=model))
        entry.input_tokens += usage.input_tokens
        entry.output_tokens += usage.output_tokens
        in_price, out_price = price_for(model, self.price_overrides)
        entry.cost_usd += (
            usage.input_tokens * in_price / TOKENS_PER_MILLION
            + usage.output_tokens * out_price / TOKENS_PER_MILLION
        )

    def record_dimension_total(self, dimension: str, total_tokens: int) -> None:
        """Record one dimension's reported cumulative token total (monotonic)."""
        clamped = max(0, int(total_tokens))
        self._dimension_tokens[dimension] = max(self._dimension_tokens.get(dimension, 0), clamped)

    def dimension_tokens(self) -> dict[str, int]:
        """Return a copy of the per-dimension cumulative token totals."""
        return dict(self._dimension_tokens)

    def dimension_cost_usd(self, model: str) -> dict[str, float]:
        """Estimate per-dimension USD from the reported token totals.

        Reviewer subagents report a single running token total per dimension
        (no input/output split), so the estimate bills the whole total at
        the average of ``model``'s input and output prices. The result is
        informational and deliberately NOT folded into
        :attr:`total_cost_usd` (which stays on the metered main-loop
        accounting).
        """
        in_price, out_price = price_for(model, self.price_overrides)
        blended_price = (in_price + out_price) / 2
        return {
            dimension: round(tokens * blended_price / TOKENS_PER_MILLION, 6)
            for dimension, tokens in self._dimension_tokens.items()
        }

    @property
    def total_cost_usd(self) -> float:
        """Sum of every model's accumulated cost."""
        return round(sum(entry.cost_usd for entry in self._by_model.values()), 6)

    @property
    def total_tokens(self) -> int:
        """Sum of input + output tokens across all models."""
        return sum(
            entry.input_tokens + entry.output_tokens for entry in self._by_model.values()
        )

    def by_model(self) -> dict[str, ModelUsage]:
        """Return a copy of per-model totals keyed by model name."""
        return dict(self._by_model)

    def format_summary(self, dimension_model: str | None = None) -> str:
        """Human-readable one-line cost summary (for TUI / slash commands).

        ``dimension_model`` optionally names the reviewer model used to
        estimate per-dimension USD (``dimension_cost_usd``); when omitted
        the dimension lines stay token-only.
        """
        parts = [
            f"{entry.model}: {entry.input_tokens:,} in / {entry.output_tokens:,} out "
            f"(${entry.cost_usd:.4f})"
            for entry in self._by_model.values()
        ]
        dimension_usd = self.dimension_cost_usd(dimension_model) if dimension_model else {}
        for dimension, tokens in sorted(self._dimension_tokens.items()):
            line = f"dimension {dimension}: {tokens:,} tokens (reviewer-reported)"
            if dimension in dimension_usd:
                line += f" (~${dimension_usd[dimension]:.4f})"
            parts.append(line)
        header = f"Total: {self.total_tokens:,} tokens, ${self.total_cost_usd:.4f}"
        return header + ("\n" + "\n".join(parts) if parts else "")
