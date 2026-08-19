"""Authentication flows for various provider types.

Each flow is a self-contained class with a single ``run()`` method that
performs the interactive authentication and returns the obtained credential.

Iterate-exclusive: only direct API-key flows are supported. Subscription /
OAuth device-code and browser flows are intentionally not supported.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class AuthFlow(ABC):
    """Abstract base for all auth flows."""

    @abstractmethod
    def run(self) -> str:
        """Execute the flow and return the obtained credential value."""


# ---------------------------------------------------------------------------
# ApiKeyFlow — directly prompt for and store an API key
# ---------------------------------------------------------------------------


class ApiKeyFlow(AuthFlow):
    """Prompt the user for an API key and persist it via :mod:`iterate_harness.auth.storage`."""

    def __init__(self, provider: str, prompt_text: str | None = None) -> None:
        self.provider = provider
        self.prompt_text = prompt_text or f"Enter your {provider} API key"

    def run(self) -> str:
        import getpass

        key = getpass.getpass(f"{self.prompt_text}: ").strip()
        if not key:
            raise ValueError("API key cannot be empty.")
        return key
