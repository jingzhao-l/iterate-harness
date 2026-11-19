"""API exports.

The provider SDKs (``anthropic`` / ``openai``) are expensive to import — a
cold ``import iterate_harness.api`` used to spend ~34s in them, which made
every lightweight consumer (the cron daemon's entry point, ``api.usage`` in
``engine.stream_events``) pay that cost before it could do any work. The
daemon's own start-up poll (20s) then timed out even though the process was
healthy.

So the heavy names are re-exported lazily (PEP 562): ``UsageSnapshot`` and the
error types — the two cheap leaves that hot paths import — stay eager, while
the HTTP clients and provider helpers are only imported when someone actually
asks for them. ``from iterate_harness.api import AnthropicApiClient`` keeps
working unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from iterate_harness.api.errors import IterateHarnessApiError
from iterate_harness.api.usage import UsageSnapshot

if TYPE_CHECKING:  # pragma: no cover - typing only
    from iterate_harness.api.client import AnthropicApiClient
    from iterate_harness.api.openai_client import OpenAICompatibleClient
    from iterate_harness.api.provider import ProviderInfo

__all__ = [
    "AnthropicApiClient",
    "OpenAICompatibleClient",
    "IterateHarnessApiError",
    "ProviderInfo",
    "UsageSnapshot",
    "auth_status",
    "detect_provider",
]


def __getattr__(name: str) -> Any:
    """Import the heavy provider clients/helpers on first access."""
    if name == "AnthropicApiClient":
        from iterate_harness.api.client import AnthropicApiClient

        return AnthropicApiClient
    if name == "OpenAICompatibleClient":
        from iterate_harness.api.openai_client import OpenAICompatibleClient

        return OpenAICompatibleClient
    if name in {"ProviderInfo", "auth_status", "detect_provider"}:
        from iterate_harness.api import provider

        return getattr(provider, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
