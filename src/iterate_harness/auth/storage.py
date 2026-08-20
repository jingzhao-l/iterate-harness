"""Credential storage for IterateHarness.

Default backend: ~/.iterate-harness/credentials.json with mode 600.
Optional backend: system keyring (if the `keyring` package is installed
and a usable backend is present).

Security model
--------------
When no keyring backend is available (common in containers, CI, and WSL),
credentials are stored in ``credentials.json`` **encrypted with Fernet**
(authenticated symmetric encryption from the ``cryptography`` package).
The Fernet key is derived deterministically from a machine secret
(``/etc/machine-id`` on Linux, the macOS hardware UUID on macOS, falling
back to the hostname) combined with the user's home directory and a fixed
application salt, so the file is only decryptable on the same machine under
the same user account.  This protects credentials at rest against casual
disclosure (backups, other users reading files) and is not a substitute for
the OS keyring when that is available.

Values written by versions released before encryption was introduced (plain
JSON) are migrated automatically: the first time a legacy value is read, or
any write happens, the whole file is re-encrypted in place under the
credentials lock.

The ``_obfuscate`` / ``_deobfuscate`` helpers at the bottom of this module
are a lightweight XOR round-trip used for non-secret data; they are **not**
encryption and must not be used to protect secrets.
"""

from __future__ import annotations

import base64
import json
import logging
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from iterate_harness.config.paths import get_config_dir
from iterate_harness.utils.file_lock import exclusive_file_lock
from iterate_harness.utils.fs import atomic_write_text

if TYPE_CHECKING:
    from cryptography.fernet import Fernet as _Fernet

log = logging.getLogger(__name__)

_CREDS_FILE_NAME = "credentials.json"
_KEYRING_SERVICE = "iterate_harness"
# Prefix marking a Fernet-encrypted value in the file backend.
_FERNET_MARKER = "fernet:v1:"
# Candidate files that expose a stable per-machine identifier on Linux.
_MACHINE_ID_CANDIDATES = (
    "/etc/machine-id",
    "/var/lib/dbus/machine-id",
)
# App-specific salt so the derived key cannot be reused by other software.
_KEY_SALT = b"iterate-harness/credentials/v1"


# ---------------------------------------------------------------------------
# Fernet key derivation (machine + user scoped)
# ---------------------------------------------------------------------------


def _machine_secret() -> bytes:
    """Return a stable, machine-scoped secret used to derive the Fernet key.

    Priority order:
      1. ``/etc/machine-id`` (systemd Linux, also present in most containers)
      2. ``/var/lib/dbus/machine-id`` (older Debian/Ubuntu)
      3. macOS hardware UUID (``IOPlatformUUID``, stable across reboots)
      4. hostname (last resort)

    The secret is combined with the per-user home path and a fixed app salt
    in :func:`_derive_fernet_key`, so the derived key differs across machines
    and across user accounts.
    """
    for candidate in _MACHINE_ID_CANDIDATES:
        try:
            content = Path(candidate).read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if content:
            return content.encode("utf-8")

    if sys.platform == "darwin":
        try:
            hardware_uuid = _macos_hardware_uuid()
            if hardware_uuid:
                return hardware_uuid.encode("utf-8")
        except Exception as exc:  # noqa: BLE001 - fall back to hostname
            log.debug("Could not read macOS hardware UUID: %s", exc)

    return socket.gethostname().encode("utf-8")


def _macos_hardware_uuid() -> str | None:
    """Read the stable macOS hardware UUID (``IOPlatformUUID``) via ``ioreg``."""
    try:
        import subprocess

        result = subprocess.run(
            ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("ioreg unavailable: %s", exc)
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if '"IOPlatformUUID"' not in line or "=" not in line:
            continue
        hardware_uuid = line.split("=", 1)[1].strip().strip('"')
        if hardware_uuid:
            return hardware_uuid
    return None


def _derive_fernet_key() -> bytes:
    """Derive the 32-byte Fernet key for the file backend.

    Key material: ``machine secret | user home | fixed app salt`` hashed with
    SHA-256.  The result is stable across runs and reboots for the same
    machine + user, so stored credentials are decryptable without a master
    password while remaining opaque on any other machine or account.
    """
    import hashlib

    material = b"|".join(
        (
            _machine_secret(),
            str(Path.home()).encode("utf-8"),
            _KEY_SALT,
        )
    )
    return base64.urlsafe_b64encode(hashlib.sha256(material).digest())


_fernet_instance: _Fernet | None = None
_fernet_broken: bool = False


def _fernet() -> _Fernet | None:
    """Return the lazily-created Fernet cipher, or None if unavailable.

    ``cryptography`` is a declared dependency; this only returns None when it
    cannot be imported (broken install).  Callers then fall back to the
    legacy plaintext behaviour with a logged error instead of crashing.
    """
    global _fernet_instance, _fernet_broken  # noqa: PLW0603
    if _fernet_instance is None and not _fernet_broken:
        try:
            from cryptography.fernet import Fernet as _FernetCls

            _fernet_instance = _FernetCls(_derive_fernet_key())
        except Exception as exc:  # noqa: BLE001 - degrade to plaintext
            _fernet_broken = True
            log.error("cryptography unavailable; credentials at rest will not be encrypted: %s", exc)
    return _fernet_instance


def _encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext credential value for the file backend."""
    cipher = _fernet()
    if cipher is None:
        log.error(
            "cryptography unavailable; storing credential in plaintext with mode 600 "
            "(install cryptography to encrypt credentials at rest)"
        )
        return plaintext
    token = cipher.encrypt(plaintext.encode("utf-8"))
    return _FERNET_MARKER + token.decode("ascii")


def _encrypt_if_needed(value: Any) -> Any:
    """Encrypt *value* unless it is already a Fernet-encrypted string."""
    if isinstance(value, str) and not value.startswith(_FERNET_MARKER):
        return _encrypt_value(value)
    return value


def _decrypt_value(stored: Any) -> Any:
    """Decrypt a stored value, transparently handling legacy plaintext.

    Returns the plaintext string, the original value when it was written by
    an older version (no marker), or ``None`` when decryption fails.
    """
    if not isinstance(stored, str) or not stored.startswith(_FERNET_MARKER):
        return stored
    cipher = _fernet()
    if cipher is None:
        log.error("cryptography unavailable; cannot decrypt stored credential")
        return None
    token = stored[len(_FERNET_MARKER):].encode("ascii")
    try:
        return cipher.decrypt(token).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - file data is untrusted input
        log.error("Failed to decrypt stored credential (wrong machine key or corrupted file): %s", exc)
        return None


# ---------------------------------------------------------------------------
# File-based backend (always available)
# ---------------------------------------------------------------------------


def _creds_path() -> Path:
    return get_config_dir() / _CREDS_FILE_NAME


def _creds_lock_path() -> Path:
    return _creds_path().with_suffix(".json.lock")


def _load_creds_file() -> dict[str, Any]:
    path = _creds_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to read credentials file: %s", exc)
        return {}


def _save_creds_file(data: dict[str, Any]) -> None:
    """Persist *data*, encrypting every not-yet-encrypted string value."""
    encrypted = {
        provider: {key: _encrypt_if_needed(value) for key, value in credentials.items()}
        for provider, credentials in data.items()
    }
    path = _creds_path()
    atomic_write_text(
        path,
        json.dumps(encrypted, indent=2) + "\n",
        mode=0o600,
    )


_legacy_migration_attempted: bool = False


def _migrate_legacy_credentials() -> None:
    """Re-encrypt plaintext values left by versions before Fernet was added.

    Triggered lazily the first time a legacy value is read.  The rewrite
    happens under the credentials lock so a concurrent writer cannot race
    it.  No-op when the file is already fully encrypted or when encryption
    is unavailable.
    """
    global _legacy_migration_attempted  # noqa: PLW0603
    if _legacy_migration_attempted or _fernet() is None:
        return
    _legacy_migration_attempted = True
    with exclusive_file_lock(_creds_lock_path()):
        data = _load_creds_file()
        if not data:
            return
        has_legacy = any(
            isinstance(value, str) and not value.startswith(_FERNET_MARKER)
            for credentials in data.values()
            for value in credentials.values()
        )
        if has_legacy:
            _save_creds_file(data)
            log.info("Migrated legacy plaintext credentials to Fernet-encrypted storage")


# ---------------------------------------------------------------------------
# Keyring backend (optional)
# ---------------------------------------------------------------------------


_keyring_checked: bool = False
_keyring_usable: bool = False


def _keyring_available() -> bool:
    """Return True when a usable system keyring backend is present.

    The check is cached after the first call so the "Keyring load failed"
    warning is emitted at most once per process.
    """
    global _keyring_checked, _keyring_usable  # noqa: PLW0603
    if _keyring_checked:
        return _keyring_usable
    _keyring_checked = True
    try:
        import keyring

        # Probe the backend — merely importing keyring is not enough because
        # the package may be installed without a functioning backend (e.g. on
        # headless Linux / WSL / containers).
        keyring.get_password(_KEYRING_SERVICE, "__probe__")
        _keyring_usable = True
    except ImportError:
        _keyring_usable = False
    except Exception as exc:
        log.info("System keyring unavailable, using file backend: %s", exc)
        _keyring_usable = False
    return _keyring_usable


def _keyring_key(provider: str, key: str) -> str:
    return f"{provider}:{key}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def store_credential(provider: str, key: str, value: str, *, use_keyring: bool | None = None) -> None:
    """Persist a credential for *provider* under *key*.

    If *use_keyring* is not set, keyring is used when available.
    """
    if use_keyring is None:
        use_keyring = _keyring_available()

    if use_keyring:
        try:
            import keyring

            keyring.set_password(_KEYRING_SERVICE, _keyring_key(provider, key), value)
            log.debug("Stored %s/%s in keyring", provider, key)
            return
        except Exception as exc:
            log.warning("Keyring store failed, falling back to file: %s", exc)

    with exclusive_file_lock(_creds_lock_path()):
        data = _load_creds_file()
        data.setdefault(provider, {})[key] = value
        _save_creds_file(data)
    log.debug("Stored %s/%s in credentials file", provider, key)


def load_credential(provider: str, key: str, *, use_keyring: bool | None = None) -> str | None:
    """Return the stored credential, or None if not found."""
    if use_keyring is None:
        use_keyring = _keyring_available()

    if use_keyring:
        try:
            import keyring

            value = keyring.get_password(_KEYRING_SERVICE, _keyring_key(provider, key))
            if value is not None:
                return value
        except Exception as exc:
            log.warning("Keyring load failed, falling back to file: %s", exc)

    data = _load_creds_file()
    stored = data.get(provider, {}).get(key)
    if stored is None:
        return None
    if isinstance(stored, str) and not stored.startswith(_FERNET_MARKER):
        # Legacy plaintext written by an older version — migrate the file.
        _migrate_legacy_credentials()
    return _decrypt_value(stored)


def clear_provider_credentials(provider: str, *, use_keyring: bool | None = None) -> None:
    """Remove all stored credentials for *provider*."""
    if use_keyring is None:
        use_keyring = _keyring_available()

    if use_keyring:
        try:
            import keyring
            from keyring.errors import PasswordDeleteError

            # Try common keys; silently ignore missing ones.
            for key in ("api_key", "token", "github_token"):
                try:
                    keyring.delete_password(_KEYRING_SERVICE, _keyring_key(provider, key))
                except (PasswordDeleteError, Exception) as exc:  # noqa: BLE001 - best-effort cleanup
                    log.debug("Could not delete keyring password for %s/%s: %s", provider, key, exc)
        except ImportError:
            log.debug("keyring not installed; skipping keyring credential deletion")

    with exclusive_file_lock(_creds_lock_path()):
        data = _load_creds_file()
        if provider in data:
            del data[provider]
            _save_creds_file(data)
    log.debug("Cleared credentials for provider: %s", provider)


def list_stored_providers() -> list[str]:
    """Return the list of providers that have credentials in the file store."""
    return list(_load_creds_file().keys())


# ---------------------------------------------------------------------------
# Obfuscation helpers (XOR round-trip — NOT encryption)
# ---------------------------------------------------------------------------
# These exist for lightweight obfuscation of non-secret data (e.g. session
# tokens where the goal is to prevent casual reading, not resist attack).
# Do NOT use for API keys or passwords — those belong in the keyring or in
# the Fernet-encrypted file backend above.
# ---------------------------------------------------------------------------


def _obfuscation_key() -> bytes:
    """Return a per-user obfuscation key derived from the home directory path."""
    seed = str(Path.home()).encode() + b"iterate_harness-v1"
    import hashlib

    return hashlib.sha256(seed).digest()


def _obfuscate(plaintext: str) -> str:
    """Lightly obfuscate *plaintext* (base64-encoded XOR).  **Not cryptographic.**"""
    import base64

    key = _obfuscation_key()
    data = plaintext.encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(xored).decode("ascii")


def _deobfuscate(ciphertext: str) -> str:
    """Reverse of :func:`_obfuscate`."""
    import base64

    key = _obfuscation_key()
    data = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return xored.decode("utf-8")


# Backward compatibility — deprecated, will be removed in a future version.
encrypt = _obfuscate
decrypt = _deobfuscate
