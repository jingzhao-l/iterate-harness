"""Python half of ``kernel/src/decision-log-transcribe.ts`` (iterate-kernel 0.1.6).

Cross-implementation anchor: ``tests/kernel_fixtures/decision-record-transcription.ok-01.json``.
The rules are read from that file's ``comment``, not from the TypeScript — which is the whole
point of the fixture: two independent implementations that agree on the mapping are evidence
the mapping is the contract, while a second file copied from the first is only a mirror.

WHY THIS EXISTS AT ALL (scenario 3, §8.5 / R40). Both ecosystems already keep decision logs,
and they are not the same shape. The iterate loop appends ``{timestamp, round, type, data}``
JSONL from two places (this harness and the plugin's TypeScript), and the plugin's history
carries kinds the Python validator refuses. Mapping rules therefore need exactly one owner,
and it has to be the layer both shells already depend on. Direction stays shell → kernel:
nothing here imports an iterate module's vocabulary; ``kind`` arrives as data.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

#: The refusal codes the TypeScript side emits, verbatim. A refusal whose code differs per
#: language cannot be reported by a shell that serves both ecosystems.
DECISION_TRANSCRIBE_CODES: Final[dict[str, str]] = {
    "record": "KERNEL_E_DECISION_RECORD",
    "kind": "KERNEL_E_DECISION_KIND",
    "round": "KERNEL_E_DECISION_ROUND",
    "timestamp": "KERNEL_E_DECISION_TIMESTAMP",
    "summary": "KERNEL_E_DECISION_SUMMARY",
    "payload": "KERNEL_E_DECISION_PAYLOAD",
}

#: Same pattern as ``DECISION_KIND_PATTERN`` in the kernel. Deliberately not an enum: each
#: ecosystem owns its own vocabulary of decision kinds (see the kernel module's docstring).
DECISION_KIND_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

#: Same id rule as ``DIMENSION_ID_PATTERN`` / ``DIMENSION_ID_MAX_LENGTH`` in dimension-context.
DIMENSION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DIMENSION_ID_MAX_LENGTH: Final[int] = 64

OPERATION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^op_[0-9A-HJKMNP-TV-Z]{26}$")

#: Rule 4: ISO-8601 date-time with an EXPLICIT zone, 1-9 fractional digits.
#:
#: WHY A PATTERN AND NOT ``datetime.fromisoformat``. The host parser is not a single rule:
#: 3.10 accepts only 3 or 6 fractional digits where 3.11+ accepts any number, and TypeScript's
#: ``Date.parse`` happily guesses US month/day order for a spelling like ``11/10/2026 02:28``
#: that Python refuses outright. A ledger whose dates depend on which runtime converted it is
#: not evidence, so both implementations match this pattern first and reject anything else.
ISO_8601_WITH_ZONE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<hms>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d{1,9}))?(?P<zone>Z|[+-]\d{2}:\d{2})$"
)

#: Boundaries mirrored from the kernel entry contract.
PAYLOAD_MAX_CANONICAL_LENGTH: Final[int] = 8192
SUMMARY_MAX_LENGTH: Final[int] = 2048

_RECORD_KEYS: Final[tuple[str, ...]] = ("timestamp", "round", "type", "data")
_OUTCOMES: Final[frozenset[str]] = frozenset({"pass", "fail", "blocked", "inconclusive"})
_SUMMARY_FIELDS: Final[tuple[str, ...]] = ("summary", "message", "reason")


class KernelDecisionTranscribeError(ValueError):
    """A record this module cannot transcribe, with a remedy the caller can act on."""

    def __init__(self, code: str, message: str, remedy: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.remedy = remedy


def _refuse(code: str, message: str, remedy: str) -> None:
    raise KernelDecisionTranscribeError(code, message, remedy)


def _normalize_to_utc_millis(raw: str) -> str:
    """Rule 4, implemented without a calendar library so both runtimes round the same way.

    Fractional seconds TRUNCATE toward zero (the tail is dropped, never rounded up), and every
    offset converts to UTC. Result is always ``YYYY-MM-DDTHH:MM:SS.mmmZ``.
    """
    match = ISO_8601_WITH_ZONE.match(raw)
    if match is None:
        _refuse(
            DECISION_TRANSCRIBE_CODES["timestamp"],
            f"`{raw}` is not ISO-8601 with an explicit zone (Z or ±HH:MM)",
            "emit the offset the producer actually has; a spelling the host parser has to guess "
            "at (11/10/2026, a bare date, local time without an offset) parses differently in "
            "each runtime, and a ledger dated by guesswork cannot be audited",
        )
        raise AssertionError  # unreachable

    date_parts = [int(part) for part in match.group("date").split("-")]
    hour, minute, second = (int(part) for part in match.group("hms").split(":"))
    millis = (match.group("frac") or "")[:3].ljust(3, "0")

    year, month, day = date_parts
    zone = match.group("zone")
    offset_minutes = 0
    if zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        offset_minutes = sign * (int(zone[1:3]) * 60 + int(zone[4:6]))

    # Days-in-month aware normalization, no external calendar dependency.
    from datetime import datetime, timedelta, timezone

    try:
        stamp = datetime(year, month, day, hour, minute, second, int(millis) * 1000, timezone.utc)
    except ValueError:
        _refuse(
            DECISION_TRANSCRIBE_CODES["timestamp"],
            f"`{raw}` is not a real instant",
            "check the producer's clock formatting: an ISO-shaped string that is not a valid "
            "date (month 13, day 32) is a bug worth stopping the migration for",
        )
        raise AssertionError  # unreachable
    return (stamp - timedelta(minutes=offset_minutes)).strftime("%Y-%m-%dT%H:%M:%S.") + millis + "Z"


def _read_outcome(data: Mapping[str, object]) -> str:
    """Rule 5: the outcome is read, never inferred from tone."""
    for field in ("outcome", "status"):
        value = data.get(field)
        if isinstance(value, str) and value in _OUTCOMES:
            return value
    if data.get("passed") is True:
        return "pass"
    if data.get("passed") is False:
        return "fail"
    return "inconclusive"


def _read_summary(data: Mapping[str, object], kind: str, round_number: int | None) -> str:
    """Rule 6: the record's own sentence, else a label — and never a truncated sentence."""
    for field in _SUMMARY_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return kind if round_number is None else f"{kind} (round {round_number})"


def decision_log_draft_from_record(record: object, entry_id: str) -> dict[str, object]:
    """Transcribe one legacy record into the canonical ledger draft.

    ``entry_id`` comes from the caller because it is ledger-owned (a ULID's random tail is not
    derivable from the record). A function that minted ids could not be compared across
    implementations against the same fixture, which is the only reason the fixture pins one
    per case.
    """
    from .canonical_json import canonical_json

    if not isinstance(record, Mapping):
        _refuse(
            DECISION_TRANSCRIBE_CODES["record"],
            "the record is not an object",
            "pass the decoded JSONL line as a mapping",
        )
    for key in record:
        if key not in _RECORD_KEYS:
            _refuse(
                DECISION_TRANSCRIBE_CODES["record"],
                f"unrecognised key `{key}` on the record",
                "a field this module does not know would be silently dropped, and the point of "
                "the ledger is that nothing about a decision is dropped — add it to the "
                "record's own payload instead",
            )

    type_value = record.get("type")
    if not isinstance(type_value, str) or not DECISION_KIND_PATTERN.match(type_value):
        _refuse(
            DECISION_TRANSCRIBE_CODES["kind"],
            f"`type` must match the snake_case kind pattern, got `{type_value!r}`",
            "the kind is how a reader from the other ecosystem recognizes what happened; a kind "
            "that needs rewriting to be comparable is not comparable",
        )
        raise AssertionError  # unreachable

    round_number: int | None
    if "round" not in record or record["round"] is None:
        round_number = None
    else:
        raw_round = record["round"]
        if not isinstance(raw_round, int) or isinstance(raw_round, bool) or raw_round < 0:
            _refuse(
                DECISION_TRANSCRIBE_CODES["round"],
                f"`round` must be an integer >= 0, got `{raw_round!r}`",
                "fix the producer's round counter; transcription will not clamp or renumber, "
                "because the round is how a reader locates the decision in the run",
            )
            raise AssertionError  # unreachable
        round_number = raw_round

    data: Mapping[str, object]
    if "data" not in record or record["data"] is None:
        data = {}
    elif isinstance(record["data"], Mapping):
        data = record["data"]
    else:
        _refuse(
            DECISION_TRANSCRIBE_CODES["record"],
            "`data` must be an object when present",
            "pass the decoded payload mapping, or omit the key",
        )
        raise AssertionError  # unreachable

    payload = dict(data)
    payload_length = len(canonical_json(payload))
    if payload_length > PAYLOAD_MAX_CANONICAL_LENGTH:
        _refuse(
            DECISION_TRANSCRIBE_CODES["payload"],
            f"`data` is {payload_length} characters of canonical JSON, over the "
            f"{PAYLOAD_MAX_CANONICAL_LENGTH} bound",
            "keep the evidence where it belongs (an evidence pack) and record the decision that "
            "referenced it; a ledger line that carries a whole report cannot be hashed at a "
            "sane size",
        )

    summary = _read_summary(data, type_value, round_number)
    if len(summary) > SUMMARY_MAX_LENGTH:
        _refuse(
            DECISION_TRANSCRIBE_CODES["summary"],
            f"the record's own sentence is {len(summary)} characters, over SUMMARY_MAX_LENGTH",
            "shorten what the producer writes as `summary`; transcription will not truncate a "
            "decision to fit a line",
        )

    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        _refuse(
            DECISION_TRANSCRIBE_CODES["timestamp"],
            "the record's `timestamp` is not a non-empty string",
            "the producer must stamp the record itself; transcription will not substitute the "
            "current time, because that would date a decision to when someone converted the file",
        )

    draft: dict[str, object] = {
        "entryId": entry_id,
        "summary": summary,
        "outcome": _read_outcome(data),
        "createdAt": _normalize_to_utc_millis(timestamp),
        "kind": type_value,
        "payload": payload,
    }
    if round_number is not None:
        draft["round"] = round_number

    # Rules 7 and 8: absent, never guessed and never null.
    dimension = data.get("dimension")
    if (
        isinstance(dimension, str)
        and DIMENSION_ID_PATTERN.match(dimension)
        and len(dimension) <= DIMENSION_ID_MAX_LENGTH
    ):
        draft["dimension"] = dimension
    operation_id = data.get("operationId")
    if isinstance(operation_id, str) and OPERATION_ID_PATTERN.match(operation_id):
        draft["operationId"] = operation_id
    return draft


def is_legacy_decision_record(line: object) -> bool:
    """Classify a decoded JSONL line so a migrated log may legally mix formats.

    Shape-based only: a canonical entry is a *different* object, and guessing by "it has a
    timestamp" would misclassify the fork's own ledger lines.
    """
    if not isinstance(line, Mapping) or not line:
        return False
    keys = list(line.keys())
    if "type" not in line:
        return False
    return all(key in _RECORD_KEYS for key in keys)
