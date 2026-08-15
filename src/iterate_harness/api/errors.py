"""API error types for IterateHarness."""

from __future__ import annotations


class IterateHarnessApiError(RuntimeError):
    """Base class for upstream API failures."""


class AuthenticationFailure(IterateHarnessApiError):
    """Raised when the upstream service rejects the provided credentials."""


class RateLimitFailure(IterateHarnessApiError):
    """Raised when the upstream service rejects the request due to rate limits."""


class RequestFailure(IterateHarnessApiError):
    """Raised for generic request or transport failures."""
