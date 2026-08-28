"""Tests for the LLM provider registry (provider auto-detection)."""

from __future__ import annotations

from iterate_harness.api.registry import (
    detect_provider_from_registry,
    find_by_name,
)


class TestOrcaRouterRegistry:
    """Tests for the OrcaRouter ProviderSpec and auto-detection."""

    def test_find_by_name_orcarouter(self):
        spec = find_by_name("orcarouter")
        assert spec is not None
        assert spec.name == "orcarouter"
        assert spec.env_key == "ORCA_KEY"
        assert spec.backend_type == "openai_compat"
        assert spec.default_base_url == "https://api.orcarouter.ai/v1"
        assert spec.is_gateway is True
        assert spec.is_local is False

    def test_detect_by_key_prefix_sk_orca(self):
        spec = detect_provider_from_registry(
            model="",
            api_key="sk-orca-abc123",
            base_url=None,
        )
        assert spec is not None
        assert spec.name == "orcarouter"

    def test_detect_by_base_url_keyword(self):
        spec = detect_provider_from_registry(
            model="",
            api_key=None,
            base_url="https://api.orcarouter.ai/v1",
        )
        assert spec is not None
        assert spec.name == "orcarouter"

    def test_detect_by_model_keyword(self):
        spec = detect_provider_from_registry(
            model="orcarouter/auto",
            api_key=None,
            base_url=None,
        )
        assert spec is not None
        assert spec.name == "orcarouter"

    def test_detect_prefers_key_prefix_over_base_url(self):
        # OpenRouter key prefix (sk-or-) takes priority over an OrcaRouter
        # base URL keyword only when both are present on the same settings.
        spec = detect_provider_from_registry(
            model="",
            api_key="sk-or-some-openrouter-key",
            base_url="https://api.orcarouter.ai/v1",
        )
        assert spec is not None
        assert spec.name == "openrouter"


class TestOpenRouterRegistry:
    """Sanity checks that the existing gateway detection still works."""

    def test_detect_by_key_prefix_sk_or(self):
        spec = detect_provider_from_registry(
            model="",
            api_key="sk-or-xyz",
            base_url=None,
        )
        assert spec is not None
        assert spec.name == "openrouter"

    def test_detect_by_base_url_keyword(self):
        spec = detect_provider_from_registry(
            model="",
            api_key=None,
            base_url="https://openrouter.ai/api/v1",
        )
        assert spec is not None
        assert spec.name == "openrouter"
