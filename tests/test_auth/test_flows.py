"""Tests for the interactive auth flows (ApiKeyFlow)."""

from __future__ import annotations

import pytest

from iterate_harness.auth.flows import ApiKeyFlow

SIGNUP_URL = "https://example.test/signup"


class TestApiKeyFlow:
    """Tests for the API-key prompt flow, including the signup shortcut."""

    def test_returns_entered_key(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-orca-test-key")
        flow = ApiKeyFlow(provider="orcarouter")
        assert flow.run() == "sk-orca-test-key"

    def test_signup_url_prompts_until_key_entered(self, monkeypatch):
        opened: list[str] = []

        def fake_open(url: str, *args: object, **kwargs: object) -> bool:
            opened.append(url)
            return True

        import iterate_harness.auth.flows as flows_module

        monkeypatch.setattr(flows_module.webbrowser, "open", fake_open)
        answers = iter(["", "sk-orca-after-signup"])
        monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))

        flow = ApiKeyFlow(provider="orcarouter", signup_url=SIGNUP_URL)
        assert flow.run() == "sk-orca-after-signup"
        assert opened == [SIGNUP_URL]

    def test_signup_url_reopens_browser_on_repeated_empty_input(self, monkeypatch):
        opened: list[str] = []

        def fake_open(url: str, *args: object, **kwargs: object) -> bool:
            opened.append(url)
            return True

        import iterate_harness.auth.flows as flows_module

        monkeypatch.setattr(flows_module.webbrowser, "open", fake_open)
        answers = iter(["", "", "sk-orca-final"])
        monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))

        flow = ApiKeyFlow(provider="orcarouter", signup_url=SIGNUP_URL)
        assert flow.run() == "sk-orca-final"
        assert opened == [SIGNUP_URL, SIGNUP_URL]

    def test_empty_key_without_signup_url_raises(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda prompt: "  ")
        flow = ApiKeyFlow(provider="anthropic")
        with pytest.raises(ValueError, match="cannot be empty"):
            flow.run()

    def test_prompt_includes_signup_hint(self):
        flow = ApiKeyFlow(provider="orcarouter", signup_url=SIGNUP_URL)
        assert "empty to open the signup page" in flow.prompt_text

    def test_default_prompt_text(self):
        flow = ApiKeyFlow(provider="deepseek")
        assert flow.prompt_text == "Enter your deepseek API key"
