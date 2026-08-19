"""Unified authentication management for IterateHarness."""

from iterate_harness.auth.flows import ApiKeyFlow
from iterate_harness.auth.manager import AuthManager
from iterate_harness.auth.storage import (
    clear_provider_credentials,
    decrypt,
    encrypt,
    load_credential,
    store_credential,
)

__all__ = [
    "AuthManager",
    "ApiKeyFlow",
    "store_credential",
    "load_credential",
    "clear_provider_credentials",
    # Deprecated — use _obfuscate/_deobfuscate directly if needed.
    # Kept for backward compatibility; will be removed in a future version.
    "encrypt",
    "decrypt",
]
