"""Unit tests for the auth manager (auth/manager.py) and flows (auth/flows.py).

Credential reads/writes are pinned to the isolated file backend (real OS
keyring is machine-wide and cannot be isolated per test), and the config dir
is redirected to a temp dir so settings persistence stays off the developer's
real ``~/.iterate-harness``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iterate_harness.auth import storage as storage_module
from iterate_harness.auth.flows import ApiKeyFlow
from iterate_harness.auth.manager import AuthManager
from iterate_harness.auth.storage import load_credential, store_credential
from iterate_harness.config.settings import ProviderProfile, Settings


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch):
    """Point config at a temp dir and pin credential storage to the file backend."""
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(storage_module, "_keyring_available", lambda: False)


def _manager(**settings_kwargs) -> AuthManager:
    settings = Settings(**settings_kwargs).materialize_active_profile()
    return AuthManager(settings)


def _custom_profile() -> ProviderProfile:
    return ProviderProfile(
        label="Custom",
        provider="openai",
        api_format="openai",
        auth_source="openai_api_key",
        default_model="gpt-5.4",
    )


# ---------------------------------------------------------------------------
# Profile / provider queries
# ---------------------------------------------------------------------------


def test_get_active_provider():
    assert _manager(active_profile="deepseek").get_active_provider() == "deepseek"


def test_get_active_profile():
    assert _manager(active_profile="deepseek").get_active_profile() == "deepseek"


def test_list_profiles_contains_builtin_and_custom():
    manager = _manager(active_profile="deepseek", profiles={"my-custom": _custom_profile()})
    profiles = manager.list_profiles()
    assert "claude-api" in profiles
    assert "deepseek" in profiles
    assert "my-custom" in profiles


# ---------------------------------------------------------------------------
# Profile lifecycle
# ---------------------------------------------------------------------------


def test_use_profile_switches_active():
    manager = _manager(active_profile="deepseek")
    manager.use_profile("moonshot")
    assert manager.get_active_profile() == "moonshot"


def test_use_profile_unknown_raises():
    manager = _manager(active_profile="deepseek")
    with pytest.raises(ValueError, match="Unknown provider profile"):
        manager.use_profile("nope")


def test_update_profile_updates_fields():
    manager = _manager(active_profile="deepseek")
    manager.update_profile("deepseek", last_model="deepseek-reasoner")
    assert manager.list_profiles()["deepseek"].last_model == "deepseek-reasoner"


def test_update_profile_unknown_raises():
    manager = _manager(active_profile="deepseek")
    with pytest.raises(ValueError, match="Unknown provider profile"):
        manager.update_profile("nope", last_model="x")


def test_remove_profile_custom():
    manager = _manager(active_profile="deepseek", profiles={"my-custom": _custom_profile()})
    manager.remove_profile("my-custom")
    assert "my-custom" not in manager.list_profiles()


def test_remove_active_profile_raises():
    manager = _manager(active_profile="deepseek")
    with pytest.raises(ValueError, match="Cannot remove the active profile"):
        manager.remove_profile("deepseek")


def test_remove_builtin_profile_raises():
    manager = _manager(active_profile="deepseek")
    with pytest.raises(ValueError, match="Cannot remove built-in profile"):
        manager.remove_profile("moonshot")


def test_remove_unknown_profile_raises():
    manager = _manager(active_profile="deepseek")
    with pytest.raises(ValueError, match="Unknown provider profile"):
        manager.remove_profile("nope")


# ---------------------------------------------------------------------------
# Switching
# ---------------------------------------------------------------------------


def test_switch_auth_source_updates_profile():
    manager = _manager(active_profile="deepseek")
    manager.switch_auth_source("openai_api_key")
    assert manager.list_profiles()["deepseek"].auth_source == "openai_api_key"


def test_switch_auth_source_unknown_raises():
    manager = _manager(active_profile="deepseek")
    with pytest.raises(ValueError, match="Unknown auth source"):
        manager.switch_auth_source("nope")


def test_switch_provider_by_auth_source():
    manager = _manager(active_profile="deepseek")
    manager.switch_provider("anthropic_api_key")
    assert manager.list_profiles()["deepseek"].auth_source == "anthropic_api_key"


def test_switch_provider_by_profile_name():
    manager = _manager(active_profile="deepseek")
    manager.switch_provider("moonshot")
    assert manager.get_active_profile() == "moonshot"


def test_switch_provider_by_provider_name():
    manager = _manager(active_profile="deepseek")
    manager.switch_provider("anthropic")
    assert manager.get_active_profile() == "claude-api"


def test_switch_provider_unknown_raises():
    manager = _manager(active_profile="deepseek")
    with pytest.raises(ValueError, match="Unknown provider or auth source"):
        manager.switch_provider("nope")


# ---------------------------------------------------------------------------
# Credential storage via the manager
# ---------------------------------------------------------------------------


def test_store_credential_syncs_active_settings_api_key():
    manager = _manager(active_profile="deepseek")
    manager.store_credential("deepseek", "api_key", "sk-ds")
    assert load_credential("deepseek", "api_key") == "sk-ds"
    assert manager.settings.api_key == "sk-ds"


def test_store_credential_non_active_provider_does_not_touch_settings():
    manager = _manager(active_profile="deepseek")
    manager.store_credential("openai", "api_key", "sk-oa")
    assert load_credential("openai", "api_key") == "sk-oa"
    assert manager.settings.api_key == ""


def test_store_profile_credential_uses_profile_namespace():
    manager = _manager(active_profile="deepseek")
    manager.store_profile_credential("moonshot", "api_key", "sk-m")
    assert load_credential("moonshot", "api_key") == "sk-m"


def test_store_profile_credential_unknown_raises():
    manager = _manager(active_profile="deepseek")
    with pytest.raises(ValueError, match="Unknown provider profile"):
        manager.store_profile_credential("nope", "api_key", "sk")


def test_clear_credential_clears_file_and_settings():
    manager = _manager(active_profile="deepseek")
    manager.store_credential("deepseek", "api_key", "sk-ds")
    manager.clear_credential("deepseek")
    assert load_credential("deepseek", "api_key") is None
    assert manager.settings.api_key == ""


def test_clear_profile_credential_unknown_raises():
    manager = _manager(active_profile="deepseek")
    with pytest.raises(ValueError, match="Unknown provider profile"):
        manager.clear_profile_credential("nope")


# ---------------------------------------------------------------------------
# Auth status
# ---------------------------------------------------------------------------


def test_get_auth_status_missing():
    manager = _manager(active_profile="deepseek")
    status = manager.get_auth_status()
    assert status["deepseek"]["configured"] is False
    assert status["deepseek"]["source"] == "missing"
    assert status["deepseek"]["active"] is True


def test_get_auth_status_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    manager = _manager(active_profile="deepseek")
    status = manager.get_auth_status()
    assert status["anthropic"]["configured"] is True
    assert status["anthropic"]["source"] == "env"


def test_get_auth_status_from_file():
    store_credential("deepseek", "api_key", "sk-file")
    manager = _manager(active_profile="deepseek")
    status = manager.get_auth_status()
    assert status["deepseek"]["configured"] is True
    assert status["deepseek"]["source"] == "file"


def test_get_profile_statuses_missing_then_configured():
    manager = _manager(active_profile="deepseek")
    assert manager.get_profile_statuses()["deepseek"]["configured"] is False
    assert manager.get_profile_statuses()["deepseek"]["auth_state"] == "missing"
    assert manager.get_profile_statuses()["deepseek"]["active"] is True

    store_credential("deepseek", "api_key", "sk-file")
    assert _manager(active_profile="deepseek").get_profile_statuses()["deepseek"]["configured"] is True


# ---------------------------------------------------------------------------
# Auth flows
# ---------------------------------------------------------------------------


def test_api_key_flow_returns_stripped_key(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt: "  sk-abc  ")
    assert ApiKeyFlow("openai").run() == "sk-abc"


def test_api_key_flow_empty_key_raises(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt: "   ")
    with pytest.raises(ValueError, match="API key cannot be empty"):
        ApiKeyFlow("openai").run()


def test_api_key_flow_default_prompt_text():
    assert ApiKeyFlow("openai").prompt_text == "Enter your openai API key"


def test_api_key_flow_custom_prompt_text():
    assert ApiKeyFlow("openai", "Custom prompt").prompt_text == "Custom prompt"
