"""Append-only decision log for the iterate loop.

**The write side is canonical since this batch (scenario 3).** ``append_entry`` no longer
stores the loop's own ``{timestamp, round, type, data}`` record: it transcribes that record
through the kernel (``kernel_decision_record.decision_log_draft_from_record``) and appends a
verified hash-chain entry (``kernel_decision_log``) — the same shape the GlassPane fork writes
into its own ledger. Two ecosystems, one format on disk, and a chain either side can recompute.

The history that predates the flip is neither rewritten away nor ignored:

* a file whose every line is still a legacy record is **migrated on the next write** — each line
  transcribed into the chain in order, the original bytes kept beside it (mode ``0600``, sha256
  reported), the new chain verified before anything is swapped in;
* ``read_entries`` classifies every line and returns the record view for both shapes, which is
  what lets ``last_state``, ``ci_report``, ``html_report``, ``replay`` and the web routes keep
  working unchanged: rule 3 of ``kernel/src/decision-log-transcribe.ts`` copies ``data`` into
  ``payload`` in full, so nothing a report reads is lost by the flip;
* a log that mixes the two (a writer that has not flipped appending after the chain started) is
  **refused, never quietly renumbered** — re-deriving a line that already carries a hash would
  forge its continuity.

The legacy reader stays deliberately forgiving (a partial log beats a hard failure) and the
*ledger* API stays deliberately not: they answer different questions — the record view is for
the loop's own reports, the chain is for audit.

Python port of ``harness/iterate-plugin/src/tools/decision-log.ts`` (the pure core; harness tool
registration wires this into the kernel separately). That TypeScript copy still writes the legacy
shape; flipping it is the same transcription call, and it is listed as unfinished rather than
folded into a claim that both ecosystems flipped.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .kernel_decision_log import (
    KernelDecisionLogError,
    append_decision_log_entry,
    build_decision_log_entry,
    decision_log_entry_hash,
    new_decision_entry_id,
    serialize_decision_log_entry,
    verify_decision_log_text,
)
from .kernel_decision_record import (
    DECISION_KIND_PATTERN,
    KernelDecisionTranscribeError,
    decision_log_draft_from_record,
    is_legacy_decision_record,
)
from .types import DecisionLogEntry as DecisionLogEntry

#: Python 3.10 compatibility — ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc

LOG_DIR = ".iterate"
LOG_FILE = "decision-log.jsonl"

#: What the harness itself writes, kept as documentation — **not** a validator. The kernel
#: deliberately has no closed set of decision kinds (``DECISION_KIND_PATTERN`` is a shape rule),
#: precisely so adding a kind does not require a kernel release. This set used to be the gate and
#: it was narrower than the TypeScript plugin's own list: ``round_failed`` and ``resume`` were
#: rejected here and accepted there. That was the divergence, not a specification.
KNOWN_ENTRY_TYPES = frozenset({
    "round_start",
    "review_result",
    "atomic_fix",
    "architectural_fix",
    "revert",
    "validation",
    "decision",
    "report",
})

#: Backwards-compatible name. Importing it no longer means "check membership against this".
VALID_ENTRY_TYPES = KNOWN_ENTRY_TYPES


class DecisionLogWriteError(RuntimeError):
    """A decision must not be recorded, carrying a code and an executable remedy."""

    def __init__(self, code: str, message: str, remedy: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.remedy = remedy


#: Harness-level refusals. The kernel's own codes propagate unchanged from
#: ``kernel_decision_log`` / ``kernel_decision_record``; these two name the cases only a file's
#: *history* can produce, so a caller can tell "the ledger is broken" from "a legacy record and
#: the chain are interleaved, and I will not guess which side owns the tail".
DECISION_LOG_ERROR_CODES = {
    "interleaved": "ITERATE_E_DECISION_LOG_INTERLEAVED",
    "migration": "ITERATE_E_DECISION_LOG_MIGRATION",
}

#: Everything "this decision cannot be recorded" can raise, so a tool boundary can answer with a
#: code and a remedy instead of letting an exception reach the agent as a crash. The kernel's own
#: classes are in here too: their ``code``/``remedy`` attributes are the same shape on purpose.
DECISION_LOG_REFUSALS = (
    DecisionLogWriteError,
    KernelDecisionLogError,
    KernelDecisionTranscribeError,
)


def is_decision_kind(value: object) -> bool:
    """True when ``value`` is a decision kind the kernel can carry — shape, not membership."""
    return isinstance(value, str) and DECISION_KIND_PATTERN.match(value) is not None



def log_path(project_root: str | Path) -> Path:
    """Resolve the log file path, creating the directory if needed.

    A directory this function creates is set to ``0700`` with an explicit ``chmod``: the ledger
    refuses a group/other-writable parent (anyone could replace the whole chain), and ``mkdir``'s
    mode argument is masked by the process umask, so passing ``0o700`` alone would not be the
    mode it looks like.
    """
    directory = Path(project_root) / LOG_DIR
    fresh = not directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
    if fresh:
        os.chmod(directory, 0o700)
    return directory / LOG_FILE


_CANONICAL_REQUIRED_KEYS = frozenset({"entryId", "sequence", "prevEntryHash", "summary", "outcome", "createdAt"})


def _classify_line(decoded: Any) -> str:
    """``"legacy"`` | ``"canonical"`` | ``"unknown"`` for one decoded JSONL line.

    Shape-based, like ``is_legacy_decision_record``: guessing "it has a timestamp" would
    misclassify the fork's own ledger lines, which also carry one.
    """
    if not isinstance(decoded, dict) or not decoded:
        return "unknown"
    if is_legacy_decision_record(decoded):
        return "legacy"
    if _CANONICAL_REQUIRED_KEYS <= set(decoded):
        return "canonical"
    return "unknown"


def _decoded_lines(file_path: Path) -> list[tuple[str, Any]]:
    """Every non-empty line, paired with its decoded object (``None`` when it does not decode)."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[tuple[str, Any]] = []
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            out.append((raw, None))
            continue
        out.append((raw, decoded))
    return out


def _timestamp_ms(raw: str) -> int | None:
    """Milliseconds since the epoch for an ISO-8601 timestamp with a zone, or ``None``.

    The ledger's entry id is ULID-shaped and time-ordered, so migration must derive it from the
    record's own instant: two runs migrating the same history have to produce the *same* chain.
    ``fromisoformat`` on 3.10 rejects a trailing ``Z``, hence the rewrite; a timestamp this
    cannot date is refused upstream rather than given a wall-clock id.
    """
    if not isinstance(raw, str) or not raw:
        return None
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp() * 1000)


def _draft_for(record: dict[str, Any]) -> dict[str, object]:
    """Transcribe one legacy record into a ledger draft, with a time-ordered entry id."""
    created_at = str(record.get("timestamp", ""))
    epoch_ms = _timestamp_ms(created_at)
    if epoch_ms is None:
        raise DecisionLogWriteError(
            DECISION_LOG_ERROR_CODES["migration"],
            f"a decision dated {created_at!r} cannot be entered into the ledger",
            "the kernel accepts ISO-8601 with an explicit zone; fix the producer's timestamp — "
            "an entry the ledger cannot date is un-auditable, and minting an id from this machine's "
            "clock would make the same history hash two different ways",
        )
    return decision_log_draft_from_record(record, new_decision_entry_id(epoch_ms))


def _migrate_to_ledger(file_path: Path, records: list[dict[str, Any]]) -> None:
    """Rewrite an all-legacy log as a verified chain, keeping the original bytes.

    Refuses rather than improvises: any record the kernel will not transcribe stops the whole
    migration, because a ledger that silently omits the line it could not read is a ledger whose
    gaps are invisible. The new text is verified *before* it replaces anything.
    """
    sequence, prev = 0, ""
    lines: list[str] = []
    for number, record in enumerate(records, start=1):
        try:
            draft = _draft_for(record)
            entry = build_decision_log_entry(draft, sequence, prev)
        except (DecisionLogWriteError, ValueError) as error:
            code = getattr(error, "code", DECISION_LOG_ERROR_CODES["migration"])
            raise DecisionLogWriteError(
                code,
                f"line {number} of {file_path} cannot be transcribed into the ledger: {error}",
                "the ledger will not start on top of a record it cannot read — preserve the file, "
                "repair that line's timestamp/payload/kind (kernel code above), then write again",
            ) from error
        prev = decision_log_entry_hash(entry)
        sequence += 1
        lines.append(serialize_decision_log_entry(entry))

    body = "".join(line + "\n" for line in lines)
    check = verify_decision_log_text(body)
    if not check.ok or len(check.entries) != len(records):
        raise DecisionLogWriteError(
            DECISION_LOG_ERROR_CODES["migration"],
            f"the migrated chain does not verify ({check.reason})",
            f"nothing was written; keep {file_path} as it is and investigate line "
            f"{check.first_broken_line}",
        )

    original = file_path.read_bytes()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = file_path.with_name(f"{file_path.stem}.pre-ledger-{stamp}.jsonl.bak")
    backup_fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(backup_fd, 0o600)
        os.write(backup_fd, original)
        os.fsync(backup_fd)
    finally:
        os.close(backup_fd)

    tmp = file_path.with_name(f"{file_path.name}.migrating-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, body.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, file_path)


def append_entry(
    project_root: str | Path, entry: DecisionLogEntry
) -> tuple[int, Path]:
    """Append one decision to the ledger as a canonical, hash-chained entry.

    Returns ``(entry_count_after_append, log_file_path)`` — the count counts every readable
    record line, legacy or canonical, because that is what the reports iterate over.

    A legacy log is migrated on the first write here; a log where a legacy line follows a
    canonical one is refused. Both are stated instead of silently renumbering hashed bytes.
    """
    record = {
        "timestamp": entry.timestamp,
        "round": entry.round,
        "type": entry.type,
        "data": entry.data,
    }
    if not is_decision_kind(record["type"]):
        raise DecisionLogWriteError(
            "KERNEL_E_DECISION_KIND",
            f"'{record['type']}' is not a decision kind the kernel can carry",
            "a kind is lowercase snake_case up to 32 characters (`^[a-z][a-z0-9_]{0,31}$`); "
            "it is not renamed or case-folded, because two ecosystems must be able to compare it",
        )
    # After the kind check, never before it: a refusal must not leave a state directory behind
    # as the only evidence that something was attempted.
    file_path = log_path(project_root)
    lines = _decoded_lines(file_path)
    kinds = [_classify_line(decoded) for _, decoded in lines]
    if kinds and all(kind == "legacy" for kind in kinds):
        _migrate_to_ledger(file_path, [decoded for _, decoded in lines if isinstance(decoded, dict)])
    contested = _is_contested(file_path)
    if contested is not None:
        raise DecisionLogWriteError(
            DECISION_LOG_ERROR_CODES["interleaved"],
            f"line {contested} of {file_path} is a legacy record appended after the chain began",
            "this cannot be repaired in place: re-deriving a line that already carries a hash forges it, "
            "and leaving it means the ledger stays unverifiable. Either flip the writer that produced it "
            "(the TypeScript plugin in harness/iterate-plugin still emits the legacy shape), or archive the "
            "contested log losslessly and start the chain again — `decision_log.archive_contested_log("
            f"{str(project_root)!r})`. Nothing is deleted either way.",
        )

    draft = _draft_for(record)
    append_decision_log_entry(str(file_path), draft)
    return len(read_entries(project_root)), file_path


def _is_contested(file_path: Path) -> int | None:
    """The 1-based line number of the first legacy record after the chain began, else ``None``."""
    kinds = [_classify_line(decoded) for _, decoded in _decoded_lines(file_path)]
    if "canonical" not in kinds:
        return None
    first_canonical = kinds.index("canonical")
    trailing = [index for index, kind in enumerate(kinds) if index > first_canonical and kind == "legacy"]
    return None if not trailing else trailing[0] + 1


def archive_contested_log(project_root: str | Path) -> Path:
    """Move a log with canonical and legacy lines interleaved aside, without losing a byte.

    This is the operator's action after the ``…INTERLEAVED`` refusal, and it is the only safe one:
    the chain cannot be renumbered and the foreign lines cannot be left inside a file the ledger
    has to verify. The archive keeps the original bytes at mode ``0600`` under a name that says
    what it is, and the next ``append_entry`` starts a fresh chain — a restart of continuity that a
    human chose, rather than a silent repair this module could not do honestly.

    Refuses when the log is *not* contested, because otherwise this would be a way to erase a
    healthy chain by accident.
    """
    file_path = log_path(project_root)
    if _is_contested(file_path) is None:
        raise DecisionLogWriteError(
            DECISION_LOG_ERROR_CODES["interleaved"],
            f"{file_path} is not a contested ledger",
            "archiving is only for a log whose legacy lines follow the chain: a healthy ledger "
            "would lose its continuity to this call, and an all-legacy one migrates on the next write",
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archive = file_path.with_name(f"{file_path.stem}.contested-{stamp}.jsonl")
    os.rename(file_path, archive)
    os.chmod(archive, 0o600)
    return archive


def _record_view(decoded: dict[str, Any]) -> DecisionLogEntry | None:
    """Project one line — legacy record or canonical entry — into the loop's record shape.

    For a canonical entry this is the inverse of the kernel's transcription on the fields the
    readers actually use: ``createdAt``→``timestamp``, ``kind``→``type``, ``payload``→``data``.
    ``payload`` is the record's ``data`` copied in full (transcription rule 3), so a report that
    read a finding list before the flip reads the same list after it.
    """
    kind = _classify_line(decoded)
    if kind == "legacy":
        try:
            return DecisionLogEntry(
                timestamp=str(decoded.get("timestamp", "")),
                round=int(decoded.get("round", 0)),
                type=str(decoded.get("type", "")),
                data=dict(decoded.get("data") or {}),
            )
        except (ValueError, TypeError):
            return None
    if kind != "canonical":
        return None
    try:
        round_value = int(decoded.get("round", 0))
    except (ValueError, TypeError):
        round_value = 0
    payload = decoded.get("payload")
    return DecisionLogEntry(
        timestamp=str(decoded.get("createdAt", "")),
        round=round_value,
        type=str(decoded.get("kind", "")),
        data=dict(payload) if isinstance(payload, dict) else {},
    )


def read_entries(project_root: str | Path) -> list[DecisionLogEntry]:
    """Read every decision the log can still express, in file order.

    Returns ``[]`` when the log is missing or unreadable, and skips individual lines that decode
    to neither shape (the append-only contract makes a partial log more valuable than a hard
    failure). Canonical lines are *projected*, not verified: this reader answers "what did the
    loop decide", and ``kernel_decision_log.read_decision_log`` is what answers "is this ledger
    intact" — a report must not be silenced by a broken chain link.
    """
    file_path = Path(project_root) / LOG_DIR / LOG_FILE
    entries: list[DecisionLogEntry] = []
    for _, decoded in _decoded_lines(file_path):
        if not isinstance(decoded, dict):
            continue
        view = _record_view(decoded)
        if view is not None:
            entries.append(view)
    return entries


def make_entry(
    *,
    entry_type: str,
    round_number: int,
    data: dict[str, object] | None = None,
) -> DecisionLogEntry:
    """Build a timestamped entry for the current instant (UTC, ISO-8601)."""
    return DecisionLogEntry(
        timestamp=datetime.now(UTC).isoformat(),
        round=round_number,
        type=entry_type,
        data=data or {},
    )


#: Keys that may carry finding payloads across the legacy report shapes.
#: The canonical loop writes the full list as ``findings``, but several older
#: producer paths recorded the same data under different keys (``topFindings``,
#: ``notableFindings``) or nested it under a ``summary`` sub-object. Order here
#: is precedence: the fully-qualified names win over the trimmed slices.
_FINDING_CARRIER_KEYS = ("findings", "notableFindings", "topFindings")


def _normalize_finding(raw: Any) -> dict[str, Any] | None:
    """Return a finding dict when ``raw`` is a dict with the core fields.

    Finding payloads across legacy shapes share the same per-finding keys
    (``dimension``/``file``/``severity``/``summary``); anything else is skipped
    so a mixed list never poisons the report.
    """
    if not isinstance(raw, dict):
        return None
    if not any(key in raw for key in ("dimension", "file", "summary")):
        return None
    return raw


def findings_from_report(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract the finding list from a ``report`` entry payload regardless of
    which legacy shape the producer wrote.

    Supports all three historical layouts so the WebUI last-run summary and
    ``ih iterate report`` produce identical, non-empty results:

    - canonical: ``{"findings": [{...}]}``
    - trimmed:   ``{"topFindings": [{...}]}`` or ``{"notableFindings": [...]}``
    - nested:    ``{"summary": {"findings": [...]}}``
    """
    if not isinstance(data, dict):
        return []

    # Nested ``summary`` sub-object may carry the full list itself.
    summary = data.get("summary")
    if isinstance(summary, dict):
        for key in _FINDING_CARRIER_KEYS:
            raw = summary.get(key)
            if isinstance(raw, list):
                findings = [f for f in raw if isinstance(f, dict)]
                if findings:
                    return findings

    for key in _FINDING_CARRIER_KEYS:
        raw = data.get(key)
        if isinstance(raw, list):
            findings = [f for f in raw if isinstance(f, dict)]
            if findings:
                return findings

    return []


def extract_deferred_architectural(project_root: str | Path) -> list[dict[str, Any]]:
    """Architectural (non-atomic) findings from the most recent review result.

    Used to persist a cross-session "deferred architectural" list in the
    checkpoint (design §11.2.2 状态继承), so a resumed run knows what broad
    changes were deliberately left for a follow-up instead of re-reporting or
    silently dropping them. Returns ``[]`` when the log has no architectural
    findings or the log is missing/malformed.
    """
    try:
        entries = read_entries(project_root)
    except Exception:  # noqa: BLE001 - never break the checkpoint path
        return []
    for entry in reversed(entries):
        if entry.type not in ("review_result", "report"):
            continue
        data = entry.data if isinstance(entry.data, dict) else {}
        # Reuse the shared legacy-shape extraction so deferred architectural
        # findings survive regardless of which report layout the producer
        # wrote (full ``findings``, trimmed ``topFindings``/``notableFindings``,
        # or a nested ``summary`` sub-object) — without it a trimmed/nested
        # report would silently contribute no deferred list.
        findings = findings_from_report(data)
        deferred: list[dict[str, Any]] = []
        for finding in findings:
            if not isinstance(finding, dict) or finding.get("is_atomic") is True:
                continue
            file = finding.get("file")
            dimension = finding.get("dimension")
            summary = finding.get("summary")
            if not file and not summary:
                continue
            deferred.append(
                {
                    "file": str(file or "?"),
                    "dimension": str(dimension or "?"),
                    "summary": str(summary or "")[:200],
                }
            )
        if deferred:
            return deferred
    return []
