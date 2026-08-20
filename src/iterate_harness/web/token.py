"""WebUI access-token management (design §17.4).

The WebUI is a local management console that binds to ``127.0.0.1`` by
default, but any other local process or user on the same machine can still
reach the loopback port. To keep the API protected against such local
access, ``ih web serve`` issues a random access token, persists it under the
config directory (``webui-token``, mode 600) so it stays stable across
server restarts, prints it on startup, and appends it to the browser URL.
The frontend sends it back on every API call (``Authorization`` header for
fetch, ``?token=`` query parameter for the EventSource stream).
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from iterate_harness.config.paths import get_config_dir
from iterate_harness.utils.fs import atomic_write_text

log = logging.getLogger(__name__)

_TOKEN_FILE_NAME = "webui-token"
#: Entropy of the generated token (urlsafe base64 of 32 random bytes).
_TOKEN_LENGTH_BYTES = 32


def webui_token_path() -> Path:
    """Return the path of the persisted WebUI access token."""
    return get_config_dir() / _TOKEN_FILE_NAME


def get_or_create_webui_token() -> str:
    """Return the persisted WebUI token, generating and storing one if needed.

    The token is random (``secrets.token_urlsafe``) and survives restarts so a
    browser session that cached it keeps working across server runs. The file
    is written atomically with mode 600.
    """
    path = webui_token_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    except OSError as exc:
        log.warning("Could not read webui token file (%s): %s", path, exc)
        existing = ""
    if existing:
        return existing

    token = secrets.token_urlsafe(_TOKEN_LENGTH_BYTES)
    atomic_write_text(path, token + "\n", mode=0o600)
    log.debug("Created new WebUI access token at %s", path)
    return token


__all__ = ["get_or_create_webui_token", "webui_token_path"]
