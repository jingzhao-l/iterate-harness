"""Authentication flows for various provider types.

Each flow is a self-contained class with a single ``run()`` method that
performs the interactive authentication and returns the obtained credential.

Iterate-exclusive: only direct API-key flows are supported. Subscription /
OAuth device-code and browser flows are intentionally not supported.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
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
    """Prompt the user for an API key and persist it via :mod:`iterate_harness.auth.storage`.

    When ``signup_url`` is set, an empty answer opens the signup page in the
    default browser and re-prompts, so users without a key can register first
    and paste the key afterwards.
    """

    def __init__(
        self,
        provider: str,
        prompt_text: str | None = None,
        signup_url: str | None = None,
    ) -> None:
        self.provider = provider
        self.signup_url = signup_url
        if prompt_text is None:
            prompt_text = f"Enter your {provider} API key"
        if signup_url and "empty to open the signup page" not in prompt_text:
            prompt_text += " (empty to open the signup page)"
        self.prompt_text = prompt_text

    def run(self) -> str:
        import getpass

        prompt = self.prompt_text
        while True:
            key = getpass.getpass(f"{prompt}: ").strip()
            if key:
                return key
            if self.signup_url:
                print(
                    f"No key entered — opening the {self.provider} signup page "
                    "in your browser. Register, copy the API key, then paste "
                    "it here.",
                    flush=True,
                )
                threading.Thread(
                    target=webbrowser.open,
                    args=(self.signup_url,),
                    daemon=True,
                ).start()
                continue
            raise ValueError("API key cannot be empty.")
