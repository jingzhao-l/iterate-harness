"""Security primitives for the WebUI management console (design §17.4).

The WebUI is a local single-user control surface, so the security model is
"trusted loopback, guarded everything else":

- :func:`is_loopback_origin` — CORS origin allow-list (only loopback hosts);
- :func:`resolve_within` — path whitelisting: resolve a user-supplied path
  and reject it unless it lands inside the intended directory (traversal);
- :func:`redact_secret` — never echo full credentials back to the client;
- :func:`AuditLog` — append-only, line-based audit journal for every
  mutating operation (restore / save / delete), written under ``.iterate/``.

No credentials are stored here; the audit log keeps operation summaries
only (timestamps + action + parameter summaries), never secrets.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Python 3.10 compatibility — ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc

log = logging.getLogger(__name__)

#: Loopback host names accepted as CORS origins (in addition to IP forms).
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})

#: Audit log file name (inside the project's ``.iterate`` directory).
AUDIT_DIR = ".iterate"
AUDIT_FILE = "web-audit.jsonl"

#: Prefixes a value must not carry for it to be considered a secret.
_SECRET_SUFFIXES = ("key", "token", "secret", "password", "credential")

#: Prefix produced by :func:`redact_secret` for redacted credential values.
#: The config write-back restores the original value whenever it sees this
#: marker, so a save from the (redacted) editor never clobbers real secrets.
REDACTION_PREFIX = "<redacted:"


def is_loopback(host: str) -> bool:
    """Return True when ``host`` is a loopback address (IP or name).

    Handles plain hostnames, IPv4, IPv4-with-port, and IPv6 in both
    bracketed (``[::1]``) and unbracketed (``::1``) forms. Empty / missing
    hosts are rejected: the WebUI must never treat an unparsable host as
    trusted.
    """
    host = (host or "").strip()
    if not host:
        return False
    lowered = host.lower()
    if lowered in _LOOPBACK_HOSTNAMES:
        return True
    # Strip IPv6 brackets: "[::1]" -> "::1".
    if lowered.startswith("[") and lowered.endswith("]"):
        lowered = lowered[1:-1]
    # IPv4-with-port inputs ("127.0.0.1:8080") carry exactly one colon whose
    # left side is a dotted quad; strip the port so the IP parses cleanly.
    # IPv6 hosts contain multiple colons and must be parsed as a whole.
    if lowered.count(":") == 1 and lowered.split(":")[0].replace(".", "").isdigit():
        lowered = lowered.split(":")[0]
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return address.is_loopback


def is_loopback_origin(origin: str | None) -> bool:
    """Return True when an HTTP Origin header refers to a loopback host.

    Accepts ``None`` (same-origin / non-browser clients) and the scheme-less
    ``null`` origin used by sandboxed iframes.
    """
    if origin is None or origin == "null":
        return True
    if not isinstance(origin, str):
        return False
    scheme, sep, rest = origin.partition("://")
    if not sep:
        return False
    if scheme not in ("http", "https"):
        return False
    # Strip any port before validating the host.
    host_port = rest.split("/", 1)[0]
    host = host_port.rsplit(":", 1)[0]
    return is_loopback(host)


def resolve_within(base: str | Path, candidate: str, *parts: str) -> Path:
    """Resolve ``candidate`` (plus optional extra parts) inside ``base``.

    Returns the resolved absolute path when it is strictly inside ``base``.
    Raises :class:`ValueError` when ``candidate`` escapes ``base`` (path
    traversal via ``..``, absolute paths, symlinks pointing outside).
    """
    base_path = Path(base).resolve()
    candidate_path = Path(str(candidate)).expanduser()

    # Reject absolute inputs outright — callers pass names, not full paths.
    if candidate_path.is_absolute():
        raise ValueError(f"absolute path not allowed: {candidate!r}")

    joined = base_path.joinpath(candidate_path, *parts)
    try:
        resolved = joined.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - resolve() rarely raises here
        raise ValueError(f"cannot resolve path {candidate!r}: {exc}") from exc

    # The resolved path must stay under the base directory.
    if resolved != base_path and base_path not in resolved.parents:
        raise ValueError(f"path escapes base directory: {candidate!r}")
    return resolved


def redact_secret(key: str, value: Any) -> Any:
    """Return a safe-to-echo representation of a config value.

    Scalar values whose key suggests a credential are replaced with a
    redaction marker; every other value is returned untouched.
    """
    lowered = (key or "").lower()
    if any(suffix in lowered for suffix in _SECRET_SUFFIXES) and isinstance(value, str):
        if not value:
            return ""
        return f"<redacted:{value[:3]}...{value[-2:] if len(value) > 5 else ''}>"
    return value


def redact_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``mapping`` with credential keys redacted."""
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, dict):
            out[key] = redact_mapping(value)
        elif isinstance(value, list):
            out[key] = [redact_mapping(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = redact_secret(key, value)
    return out


class AuditLog:
    """Append-only audit journal for mutating web operations.

    Each entry is one JSON line under ``.iterate/web-audit.jsonl`` with the
    timestamp, action, target, and an optional summary of parameters. Writes
    are best-effort: a failing audit never aborts the operation that
    triggered it.
    """

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self._path: Path | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            directory = self.project_root / AUDIT_DIR
            directory.mkdir(parents=True, exist_ok=True)
            self._path = directory / AUDIT_FILE
        return self._path

    def record(
        self,
        action: str,
        target: str,
        *,
        summary: dict[str, Any] | None = None,
    ) -> None:
        """Append one audit entry (best-effort, never raises)."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": action,
            "target": str(target),
            "summary": redact_mapping(summary or {}),
        }
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            log.warning("web audit write failed: %s", exc)

    def entries(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return the most recent audit entries (oldest first, capped)."""
        path = self.path
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        parsed: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                parsed.append(entry)
        return parsed


def read_audit_entries(project_root: str | Path, limit: int = 200) -> list[dict[str, Any]]:
    """Convenience reader for the audit journal (used by the API)."""
    return AuditLog(project_root).entries(limit=limit)


__all__ = [
    "AUDIT_DIR",
    "AUDIT_FILE",
    "AuditLog",
    "REDACTION_PREFIX",
    "is_loopback",
    "is_loopback_origin",
    "read_audit_entries",
    "redact_mapping",
    "redact_secret",
    "resolve_within",
]
