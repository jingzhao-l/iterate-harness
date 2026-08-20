"""Tests for Fernet-encrypted credential storage (auth/storage.py).

Covers the normal round-trip, encryption-at-rest, legacy plaintext
migration, keyring routing, wrong-machine-key handling, provider listing,
and credential clearing.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from iterate_harness.auth import storage as storage_module
from iterate_harness.auth.storage import (
    _FERNET_MARKER,
    clear_provider_credentials,
    list_stored_providers,
    load_credential,
    store_credential,
)


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch):
    """Point the config dir at a temp dir and reset module-level caches."""
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(storage_module, "_fernet_instance", None)
    monkeypatch.setattr(storage_module, "_fernet_broken", False)
    monkeypatch.setattr(storage_module, "_legacy_migration_attempted", False)
    monkeypatch.setattr(storage_module, "_keyring_checked", False)
    monkeypatch.setattr(storage_module, "_keyring_usable", False)
    yield


def _creds_path(tmp_path: Path) -> Path:
    return tmp_path / "credentials.json"


def _read_file_store(tmp_path: Path) -> dict:
    return json.loads(_creds_path(tmp_path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Normal path
# ---------------------------------------------------------------------------


def test_store_and_load_round_trip(tmp_path: Path):
    store_credential("openai", "api_key", "sk-roundtrip", use_keyring=False)

    assert load_credential("openai", "api_key", use_keyring=False) == "sk-roundtrip"
    # The provider can also be resolved without opting into the keyring.
    assert load_credential("openai", "api_key") == "sk-roundtrip"


def test_stored_values_are_encrypted_at_rest(tmp_path: Path):
    store_credential("anthropic", "api_key", "sk-super-secret", use_keyring=False)

    raw = _creds_path(tmp_path).read_text(encoding="utf-8")
    assert "sk-super-secret" not in raw

    data = _read_file_store(tmp_path)
    stored = data["anthropic"]["api_key"]
    assert isinstance(stored, str)
    assert stored.startswith(_FERNET_MARKER)
    assert stored != "sk-super-secret"


def test_credentials_file_mode_is_private(tmp_path: Path):
    store_credential("openai", "api_key", "sk-mode", use_keyring=False)
    mode = _creds_path(tmp_path).stat().st_mode & 0o777
    assert mode == 0o600


def test_load_missing_credential_returns_none(tmp_path: Path):
    assert load_credential("nonexistent", "api_key", use_keyring=False) is None


def test_multiple_providers_round_trip_independently(tmp_path: Path):
    store_credential("openai", "api_key", "sk-a", use_keyring=False)
    store_credential("claude-api", "api_key", "sk-b", use_keyring=False)

    assert load_credential("openai", "api_key", use_keyring=False) == "sk-a"
    assert load_credential("claude-api", "api_key", use_keyring=False) == "sk-b"


def test_store_overwrites_existing_value(tmp_path: Path):
    store_credential("openai", "api_key", "old", use_keyring=False)
    store_credential("openai", "api_key", "new", use_keyring=False)

    assert load_credential("openai", "api_key", use_keyring=False) == "new"


def test_list_stored_providers(tmp_path: Path):
    assert list_stored_providers() == []

    store_credential("openai", "api_key", "sk-a", use_keyring=False)
    store_credential("anthropic", "api_key", "sk-b", use_keyring=False)

    assert sorted(list_stored_providers()) == ["anthropic", "openai"]


def test_clear_provider_credentials(tmp_path: Path):
    store_credential("openai", "api_key", "sk-a", use_keyring=False)
    store_credential("anthropic", "api_key", "sk-b", use_keyring=False)

    clear_provider_credentials("openai", use_keyring=False)

    assert load_credential("openai", "api_key", use_keyring=False) is None
    assert load_credential("anthropic", "api_key", use_keyring=False) == "sk-b"
    assert "openai" not in _read_file_store(tmp_path)


# ---------------------------------------------------------------------------
# Keyring routing
# ---------------------------------------------------------------------------


def _install_fake_keyring(monkeypatch):
    """Route the optional keyring backend to an in-memory fake."""
    fake = types.SimpleNamespace(store={})

    fake.set_password = lambda service, user, password: fake.store.__setitem__((service, user), password)
    fake.get_password = lambda service, user: fake.store.get((service, user))
    fake.delete_password = lambda service, user: fake.store.pop((service, user), None)

    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setattr(storage_module, "_keyring_available", lambda: True)
    return fake


def test_keyring_is_used_when_available(tmp_path: Path, monkeypatch):
    fake = _install_fake_keyring(monkeypatch)

    store_credential("openai", "api_key", "sk-keyring", use_keyring=True)

    assert not _creds_path(tmp_path).exists()
    assert fake.store[("iterate_harness", "openai:api_key")] == "sk-keyring"
    assert load_credential("openai", "api_key", use_keyring=True) == "sk-keyring"


def test_keyring_store_failure_falls_back_to_encrypted_file(tmp_path: Path, monkeypatch):
    _install_fake_keyring(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("keyring backend unavailable")

    monkeypatch.setattr(sys.modules["keyring"], "set_password", boom)

    store_credential("openai", "api_key", "sk-fallback", use_keyring=True)

    assert load_credential("openai", "api_key", use_keyring=True) == "sk-fallback"
    data = _read_file_store(tmp_path)
    assert data["openai"]["api_key"].startswith(_FERNET_MARKER)


# ---------------------------------------------------------------------------
# Legacy plaintext migration
# ---------------------------------------------------------------------------


def test_legacy_plaintext_is_migrated_on_load(tmp_path: Path):
    _creds_path(tmp_path).write_text(
        json.dumps({"openai": {"api_key": "sk-legacy-plaintext"}}, indent=2) + "\n",
        encoding="utf-8",
    )

    # Legacy value is still readable…
    assert load_credential("openai", "api_key", use_keyring=False) == "sk-legacy-plaintext"

    # …and the file has been rewritten with Fernet-encrypted values.
    data = _read_file_store(tmp_path)
    stored = data["openai"]["api_key"]
    assert stored.startswith(_FERNET_MARKER)
    assert "sk-legacy-plaintext" not in _creds_path(tmp_path).read_text(encoding="utf-8")

    # A second read still resolves through the encrypted representation.
    assert load_credential("openai", "api_key", use_keyring=False) == "sk-legacy-plaintext"


def test_legacy_plaintext_is_migrated_on_store(tmp_path: Path):
    _creds_path(tmp_path).write_text(
        json.dumps({"openai": {"api_key": "sk-legacy-a"}, "anthropic": {"api_key": "sk-legacy-b"}})
        + "\n",
        encoding="utf-8",
    )

    # Storing a new value rewrites the whole file encrypted.
    store_credential("deepseek", "api_key", "sk-new", use_keyring=False)

    data = _read_file_store(tmp_path)
    assert data["openai"]["api_key"].startswith(_FERNET_MARKER)
    assert data["anthropic"]["api_key"].startswith(_FERNET_MARKER)
    assert data["deepseek"]["api_key"].startswith(_FERNET_MARKER)
    assert load_credential("openai", "api_key", use_keyring=False) == "sk-legacy-a"
    assert load_credential("deepseek", "api_key", use_keyring=False) == "sk-new"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_corrupted_or_foreign_token_returns_none_without_raising(tmp_path: Path, monkeypatch):
    store_credential("openai", "api_key", "sk-here", use_keyring=False)

    # Simulate the file having been produced on another machine: derive the
    # Fernet key from a different machine secret and reset the cached cipher.
    monkeypatch.setattr(storage_module, "_machine_secret", lambda: b"some-other-machine")
    monkeypatch.setattr(storage_module, "_fernet_instance", None)

    assert load_credential("openai", "api_key", use_keyring=False) is None

    # A garbled token must also be handled gracefully.
    _creds_path(tmp_path).write_text(
        json.dumps({"openai": {"api_key": _FERNET_MARKER + "!!!not-base64!!!"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(storage_module, "_machine_secret", lambda: b"machine-secret-for-this-test")
    monkeypatch.setattr(storage_module, "_fernet_instance", None)
    assert load_credential("openai", "api_key", use_keyring=False) is None


def test_corrupted_credentials_file_degrades_gracefully(tmp_path: Path):
    _creds_path(tmp_path).write_text("{ not valid json ", encoding="utf-8")

    assert load_credential("openai", "api_key", use_keyring=False) is None
    assert list_stored_providers() == []

    # A subsequent store recovers by rewriting the file.
    store_credential("openai", "api_key", "sk-recovered", use_keyring=False)
    assert load_credential("openai", "api_key", use_keyring=False) == "sk-recovered"
