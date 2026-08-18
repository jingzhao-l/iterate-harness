"""Work secret helpers."""

from __future__ import annotations

import base64
import ipaddress
import json
from urllib.parse import urlsplit

from iterate_harness.bridge.types import WorkSecret


def encode_work_secret(secret: WorkSecret) -> str:
    """Encode a work secret as base64url JSON."""
    data = json.dumps(secret.__dict__, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def decode_work_secret(secret: str) -> WorkSecret:
    """Decode and validate a base64url work secret."""
    padding = "=" * (-len(secret) % 4)
    raw = base64.urlsafe_b64decode((secret + padding).encode("utf-8"))
    data = json.loads(raw.decode("utf-8"))
    if data.get("version") != 1:
        raise ValueError(f"Unsupported work secret version: {data.get('version')}")
    if not data.get("session_ingress_token"):
        raise ValueError("Invalid work secret: missing session_ingress_token")
    if not isinstance(data.get("api_base_url"), str):
        raise ValueError("Invalid work secret: missing api_base_url")
    return WorkSecret(
        version=data["version"],
        session_ingress_token=data["session_ingress_token"],
        api_base_url=data["api_base_url"],
    )


#: Loopback host names treated as "local" for WS vs WSS protocol selection.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback_base(api_base_url: str) -> bool:
    """Return True when ``api_base_url`` targets a loopback host.

    Parses the URL's netloc so only the *host* (not the whole string) is
    matched. This avoids false positives when an enterprise domain merely
    contains ``localhost`` / ``127.0.0.1`` as a substring. Handles schemes,
    ``host:port``, and bracketed IPv6 (``[::1]``) forms.
    """
    try:
        netloc = urlsplit(api_base_url).netloc
    except ValueError:
        return False
    if not netloc:
        # Scheme-less input (e.g. ``localhost:3000``): take the first segment.
        netloc = api_base_url.split("/", 1)[0]
    host = netloc.rsplit(":", 1)[0].strip().lower()
    # Strip IPv6 brackets: "[::1]" -> "::1".
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host in _LOOPBACK_HOSTS:
        return True
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_sdk_url(api_base_url: str, session_id: str) -> str:
    """Build a session ingress WebSocket URL."""
    is_local = _is_loopback_base(api_base_url)
    protocol = "ws" if is_local else "wss"
    version = "v2" if is_local else "v1"
    host = api_base_url.replace("https://", "").replace("http://", "").rstrip("/")
    return f"{protocol}://{host}/{version}/session_ingress/ws/{session_id}"
