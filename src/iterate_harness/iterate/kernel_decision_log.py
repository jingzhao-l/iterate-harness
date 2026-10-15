"""Python half of ``kernel/src/decision-log.ts`` — the append-only hash-chain ledger.

Cross-implementation anchor: ``tests/kernel_fixtures/decision-log-chain.ok-01.json``. Its
``comment`` states the chain rule in prose so a second language reproduces it without reading
the TypeScript; ``test_kernel_decision_log.py`` recomputes those pinned hashes with ``hashlib``
and compares the stored bytes with ``canonical_json``, exactly as the kernel test recomputes
with ``openssl``/``shasum``. A chain only one language can recompute is a claim, not evidence —
which is the entire reason this port exists next to the TypeScript one rather than replacing it.

This is the *capability* the iterate write side needs to emit canonical entries; it deliberately
does not yet replace ``decision_log.py`` (the legacy ``{timestamp, round, type, data}`` JSONL).
That flip is a separate, coordinated change: the legacy reader is consumed by ``last_state``,
``ci_report``, ``html_report``, ``replay`` and four web routes, and it must keep reading
historical lines (see ``kernel_decision_record.is_legacy_decision_record``). Landing the chain
first means the flip changes only what a producer writes, not whether the ledger is verifiable.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .canonical_json import canonical_json
from .kernel_decision_record import (
    DECISION_KIND_PATTERN,
    DIMENSION_ID_MAX_LENGTH,
    DIMENSION_ID_PATTERN,
    OPERATION_ID_PATTERN,
    PAYLOAD_MAX_CANONICAL_LENGTH,
    SUMMARY_MAX_LENGTH,
)

DECISION_ENTRY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^dl_[0-9A-HJKMNP-TV-Z]{26}$")
PREV_ENTRY_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"^([0-9a-f]{64})?$")
ISO_MILLIS_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

#: The refusal codes the TypeScript side emits, verbatim.
DECISION_LOG_CODES: Final[dict[str, str]] = {
    "path": "KERNEL_E_DECISION_LOG_PATH",
    "corrupt": "KERNEL_E_DECISION_LOG_CORRUPT",
    "chain": "KERNEL_E_DECISION_LOG_CHAIN",
    "location": "KERNEL_E_DECISION_LOG_LOCATION",
    "write": "KERNEL_E_DECISION_LOG_WRITE",
    "draft": "KERNEL_E_DECISION_LOG_DRAFT",
}

OUTCOMES: Final[frozenset[str]] = frozenset({"pass", "fail", "blocked", "inconclusive"})

#: Draft keys the ledger will accept. ``sequence``/``prevEntryHash`` are absent on purpose: the
#: ledger owns them, and accepting them here is how a caller forges continuity.
_DRAFT_KEYS: Final[frozenset[str]] = frozenset(
    {"entryId", "summary", "outcome", "createdAt", "operationId", "kind", "round", "dimension", "payload"}
)
_ENTRY_KEYS: Final[frozenset[str]] = _DRAFT_KEYS | {"sequence", "prevEntryHash"}

_CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class KernelDecisionLogError(ValueError):
    """A ledger operation that must not proceed, carrying code + message + remedy."""

    def __init__(self, code: str, path: str, message: str, remedy: str, line: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.path = path
        self.remedy = remedy
        self.line = line


@dataclass(frozen=True)
class DecisionLogEntry:
    """One verified line of the ledger (the canonical shape, 0.1.6)."""

    entry_id: str
    sequence: int
    prev_entry_hash: str
    summary: str
    outcome: str
    created_at: str
    operation_id: str | None = None
    kind: str | None = None
    round_number: int | None = None
    dimension: str | None = None
    payload: Mapping[str, object] | None = None

    def to_json_obj(self) -> dict[str, object]:
        """The canonical entry object. Optional fields are ABSENT, never null — the stored bytes
        are what gets hashed, so writing null would change the digest of every line."""
        out: dict[str, object] = {
            "entryId": self.entry_id,
            "sequence": self.sequence,
            "prevEntryHash": self.prev_entry_hash,
            "summary": self.summary,
            "outcome": self.outcome,
            "createdAt": self.created_at,
        }
        if self.operation_id is not None:
            out["operationId"] = self.operation_id
        if self.kind is not None:
            out["kind"] = self.kind
        if self.round_number is not None:
            out["round"] = self.round_number
        if self.dimension is not None:
            out["dimension"] = self.dimension
        if self.payload is not None:
            out["payload"] = dict(self.payload)
        return out


@dataclass
class DecisionLogVerification:
    ok: bool
    entries: list[DecisionLogEntry] = field(default_factory=list)
    first_broken_line: int | None = None
    broken_kind: str | None = None  # "corrupt" | "chain"
    reason: str | None = None


def serialize_decision_log_entry(entry: DecisionLogEntry | Mapping[str, object]) -> str:
    obj = entry.to_json_obj() if isinstance(entry, DecisionLogEntry) else dict(entry)
    return canonical_json(obj)


def decision_log_entry_hash(entry: DecisionLogEntry | Mapping[str, object]) -> str:
    return hashlib.sha256(serialize_decision_log_entry(entry).encode("utf-8")).hexdigest()


def new_decision_entry_id(timestamp_ms: int | None = None) -> str:
    """Crockford base32 ULID (48-bit ms time + 80-bit random) with the ``dl_`` prefix."""
    if timestamp_ms is None:
        timestamp_ms = int(__import__("time").time() * 1000)
    if not isinstance(timestamp_ms, int) or timestamp_ms < 0 or timestamp_ms > 0xFFFFFFFFFFFF:
        raise ValueError(f"entryId timestamp must be an integer in [0, 2^48): got {timestamp_ms}")
    out = ""
    value = timestamp_ms
    for _ in range(10):
        out = _CROCKFORD[value & 0x1F] + out
        value //= 32
    for _ in range(10):
        byte = secrets.randbelow(256)
        out += _CROCKFORD[(byte & 0xE0) >> 5] + _CROCKFORD[byte & 0x1F]
    return f"dl_{out[:26]}"


def _require_absolute_path(log_path: str) -> None:
    if not isinstance(log_path, str) or not log_path:
        _fail(DECISION_LOG_CODES["path"], str(log_path), "the ledger path must be an explicit non-empty string",
              "pass the resolved absolute path the caller was configured with; this API deliberately has no default location")
    if not log_path.startswith("/"):
        _fail(DECISION_LOG_CODES["path"], log_path, "the ledger path must be absolute",
              f"resolve '{log_path}' against a configured data directory first — a CWD-relative ledger silently lands inside "
              "whatever repository the agent happened to run from")


def _fail(code: str, path: str, message: str, remedy: str, line: int | None = None) -> None:
    raise KernelDecisionLogError(code, path, message, remedy, line)


def _validate_entry_fields(obj: Mapping[str, object]) -> DecisionLogEntry:
    """Strict: every key must be known, every field must hold, patterns must match.

    Mirrors the TypeScript ``strictObject`` + regex guards. A half-valid entry raises with the
    offending field named — so a producer that misspelled a key learns about it here rather than
    losing that information into a ledger whose purpose is to record what was decided.
    """
    for key in obj:
        if key not in _ENTRY_KEYS:
            raise ValueError(f"unrecognised entry key `{key}`")
    missing = _ENTRY_KEYS - {"operationId", "kind", "round", "dimension", "payload"} - set(obj.keys())
    if missing:
        raise ValueError(f"missing required entry field(s): {sorted(missing)}")

    entry_id = obj["entryId"]
    if not isinstance(entry_id, str) or not DECISION_ENTRY_ID_PATTERN.match(entry_id):
        raise ValueError("entryId must match ^dl_[0-9A-HJKMNP-TV-Z]{26}$")
    sequence = obj["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ValueError("sequence must be an integer >= 0")
    prev = obj["prevEntryHash"]
    if not isinstance(prev, str) or not PREV_ENTRY_HASH_PATTERN.match(prev):
        raise ValueError("prevEntryHash must be empty or 64 lowercase hex")
    summary = obj["summary"]
    if not isinstance(summary, str) or len(summary) > SUMMARY_MAX_LENGTH:
        raise ValueError(f"summary must be a string of at most {SUMMARY_MAX_LENGTH} chars")
    outcome = obj["outcome"]
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be one of pass|fail|blocked|inconclusive")
    created = obj["createdAt"]
    if not isinstance(created, str) or not ISO_MILLIS_PATTERN.match(created):
        raise ValueError("createdAt must be ISO-8601 millisecond UTC")

    operation_id = obj.get("operationId")
    if operation_id is not None and (not isinstance(operation_id, str) or not OPERATION_ID_PATTERN.match(operation_id)):
        raise ValueError("operationId must match ^op_[0-9A-HJKMNP-TV-Z]{26}$")
    kind = obj.get("kind")
    if kind is not None and (not isinstance(kind, str) or not DECISION_KIND_PATTERN.match(kind)):
        raise ValueError("kind must match the snake_case pattern")
    round_number = obj.get("round")
    if round_number is not None and (not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 0):
        raise ValueError("round must be an integer >= 0")
    dimension = obj.get("dimension")
    if dimension is not None and (
        not isinstance(dimension, str) or not DIMENSION_ID_PATTERN.match(dimension) or len(dimension) > DIMENSION_ID_MAX_LENGTH
    ):
        raise ValueError("dimension must be a kernel dimension id")
    payload = obj.get("payload")
    if payload is not None:
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be an object when present")
        if len(canonical_json(dict(payload))) > PAYLOAD_MAX_CANONICAL_LENGTH:
            raise ValueError(f"payload exceeds {PAYLOAD_MAX_CANONICAL_LENGTH} chars of canonical JSON")

    return DecisionLogEntry(
        entry_id=entry_id,
        sequence=sequence,
        prev_entry_hash=prev,
        summary=summary,
        outcome=str(outcome),
        created_at=created,
        operation_id=operation_id,
        kind=kind,
        round_number=round_number,
        dimension=dimension,
        payload=dict(payload) if payload is not None else None,
    )


def _parse_line(raw: str) -> DecisionLogEntry:
    import json

    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("line is not a JSON object")
    return _validate_entry_fields(obj)


def verify_decision_log_text(text: str) -> DecisionLogVerification:
    """Parse + verify ledger text. Never raises: a broken chain is a *result*."""
    body = text[:-1] if text.endswith("\n") else text
    if body == "":
        return DecisionLogVerification(ok=True, entries=[])
    entries: list[DecisionLogEntry] = []
    lines = body.split("\n")
    for i, raw in enumerate(lines):
        line_number = i + 1
        try:
            entry = _parse_line(raw)
        except (ValueError, __import__("json").JSONDecodeError) as error:
            return DecisionLogVerification(ok=False, entries=entries, first_broken_line=line_number,
                                            broken_kind="corrupt", reason=f"line {line_number} is not a valid entry: {error}")
        if entry.sequence != i:
            return DecisionLogVerification(ok=False, entries=entries, first_broken_line=line_number, broken_kind="chain",
                                           reason=f"line {line_number} declares sequence {entry.sequence}, expected {i}")
        if canonical_json(entry.to_json_obj()) != raw:
            return DecisionLogVerification(ok=False, entries=entries, first_broken_line=line_number, broken_kind="corrupt",
                                           reason=f"line {line_number} is not stored in canonical JSON form, so its hash "
                                                  "cannot be recomputed from the bytes on disk")
        expected_prev = "" if i == 0 else decision_log_entry_hash(entries[i - 1])
        if entry.prev_entry_hash != expected_prev:
            return DecisionLogVerification(ok=False, entries=entries, first_broken_line=line_number, broken_kind="chain",
                                           reason=f"line {line_number} links to {entry.prev_entry_hash}, but the previous "
                                                  f"entry hashes to {expected_prev}")
        entries.append(entry)
    return DecisionLogVerification(ok=True, entries=entries)


def read_decision_log(log_path: str) -> DecisionLogVerification:
    _require_absolute_path(log_path)
    try:
        text = Path(log_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return DecisionLogVerification(ok=True, entries=[])
    except (OSError, UnicodeDecodeError) as error:
        _fail(DECISION_LOG_CODES["location"], log_path, f"cannot read the ledger ({error})",
              f"fix the permissions or ownership on {log_path} — this is evidence, so repair access rather than deleting the file")
        raise AssertionError  # unreachable
    return verify_decision_log_text(text)


def decision_log_head(log_path: str) -> tuple[int, str]:
    """``(sequence, prevEntryHash)`` the next append must carry, taken from the ledger."""
    verified = read_decision_log(log_path)
    if not verified.ok:
        _fail(
            DECISION_LOG_CODES["chain"] if verified.broken_kind == "chain" else DECISION_LOG_CODES["corrupt"],
            log_path,
            verified.reason or "the ledger is not verifiable",
            f"the ledger stops being trustworthy at line {verified.first_broken_line}; preserve the file and investigate "
            "— appending on top of a broken link would forge its continuity",
            verified.first_broken_line,
        )
        raise AssertionError  # unreachable
    tail = verified.entries[-1] if verified.entries else None
    return len(verified.entries), "" if tail is None else decision_log_entry_hash(tail)


def build_decision_log_entry(draft: Mapping[str, object], sequence: int, prev_entry_hash: str) -> DecisionLogEntry:
    """Validate a draft + chain fields into an entry, before any disk access."""
    for key in draft:
        if key not in _DRAFT_KEYS:
            _fail(DECISION_LOG_CODES["draft"], "", f"unrecognised draft key `{key}`",
                  "fix the producer — `sequence` and `prevEntryHash` are derived from the ledger and must not be sent, "
                  "and an unrecognised key means the field name is wrong, not that it is optional")
    obj = dict(draft)
    obj["sequence"] = sequence
    obj["prevEntryHash"] = prev_entry_hash
    try:
        return _validate_entry_fields(obj)
    except ValueError as error:
        _fail(DECISION_LOG_CODES["draft"], "", f"refusing to log a decision that is not schema-valid: {error}",
              "fix the entry at the producer — a ledger line that fails the schema can never be verified again")
        raise AssertionError  # unreachable


def append_decision_log_entry(log_path: str, draft: Mapping[str, object]) -> DecisionLogEntry:
    """Append one entry; chain fields come from the ledger so the caller cannot choose them.

    Single-writer assumption, stated plainly: concurrent appends can interleave. This detects it
    (read-back + verify) and reports it; it does not pretend to lock.
    """
    _require_absolute_path(log_path)
    sequence, prev = decision_log_head(log_path)
    entry = build_decision_log_entry(draft, sequence, prev)

    parent = os.path.dirname(log_path)
    try:
        dir_mode = os.stat(parent).st_mode
    except OSError:
        _fail(DECISION_LOG_CODES["location"], log_path, f"the ledger's directory {parent} does not exist",
              "create it (0700) or point the ledger at a directory you control; this API will not create state "
              "directories behind the caller's back")
        raise AssertionError  # unreachable
    if dir_mode & 0o022:
        _fail(DECISION_LOG_CODES["location"], log_path,
              f"{parent} is writable by group or other (mode {oct(dir_mode & 0o777)})",
              "an audit ledger in a group/other-writable directory can be replaced wholesale; move it, or chmod 0700 the directory")

    line = serialize_decision_log_entry(entry)
    payload = (line + "\n").encode("utf-8")
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        written = os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    if written != len(payload):
        _fail(DECISION_LOG_CODES["write"], log_path, f"short write: {written} of {len(payload)} bytes reached the fd",
              f"the tail may be torn — do not delete {log_path}; call read_decision_log() to see where verification stops")

    stored = Path(log_path).read_text(encoding="utf-8").splitlines()
    if not stored or stored[-1] != line:
        _fail(DECISION_LOG_CODES["write"], log_path,
              f"read-back mismatch at line {len(stored)}: the bytes on disk are not what this call wrote",
              f"another writer may have interleaved — preserve {log_path} and re-verify the chain from line {len(stored)}",
              len(stored))
    verified = verify_decision_log_text("\n".join(stored) + "\n")
    if not verified.ok:
        _fail(DECISION_LOG_CODES["write"], log_path,
              f"the ledger fails its own verification immediately after a successful append (line {verified.first_broken_line}): {verified.reason}",
              "preserve the file; check the file mode and the raw tail before anything else", verified.first_broken_line)
    return entry
