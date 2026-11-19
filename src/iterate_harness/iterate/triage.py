"""Console/CI findings triage that the engine actually honours.

The WebUI Runs page has always been able to record a human verdict on a
finding (``approve`` / ``reject``), and the model-driven loop has an
``iterate_triage`` tool that can persist an ``always-ignore`` decision. What
was missing is the bridge between those two worlds:

* A console operator (or a CI job) could *see* findings with
  ``ih iterate log`` / ``ih iterate report`` but had no way to say "this one
  is intentional" without opening a model session and asking the model to
  call ``iterate_triage``.
* A "reject" clicked in the WebUI was a human record only — the next round
  still reported and fixed the same finding, because the engine's filter
  reads ``known_intentional`` (personalization JSON + ``iterate.config.yaml``),
  not the WebUI journal.

This module is the single place that answers "what is the triage state of
this project's findings" and the single writer for a *permanent* ignore:

* ``collect_findings`` reconstructs the deduplicated finding list from the
  decision log (``review_result`` + ``report`` entries, latest occurrence
  wins) without depending on the WebUI route.
* ``ignore_findings`` appends ``known_intentional`` entries to the project's
  personalization store, bridges them into ``iterate.config.yaml`` (so they
  are visible and committable), records a journal decision, and appends a
  ``decision`` entry to the decision log. The engine's review filter reads
  the same stores, so the finding stops coming back from the very next round.
* ``restore_findings`` reverses all of that — but only for entries this
  module marked, so a hand-written ``known_intentional`` entry is never
  deleted by an undo.

The WebUI journal stays owned by :mod:`iterate_harness.web.findings_triage`;
it is imported lazily so the semantic layer does not acquire a FastAPI
dependency (or a circular import) just to read the console's records.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config_loader, decision_log, personalization
from .types import KnownIntentional
from ..utils.file_lock import exclusive_file_lock

log = logging.getLogger(__name__)

#: Machine-added ``known_intentional`` reasons carry this prefix so an undo can
#: remove exactly what triage added and never touch a hand-written entry.
IGNORE_REASON_PREFIX = "triaged-intentional:"

#: Reason recorded when the operator did not supply one.
DEFAULT_IGNORE_REASON = "marked intentional via triage"

#: Status values surfaced by :func:`triage_report`.
STATUS_OPEN = "open"
STATUS_APPROVED = "approve"
STATUS_REJECTED = "reject"
STATUS_IGNORED = "ignore"

#: Severities used by the CLI filters (ordered most-severe first).
SEVERITY_ORDER: tuple[str, ...] = ("critical", "high", "medium", "low")

#: Decision-log entry types that carry findings.
_FINDING_ENTRY_TYPES = ("review_result", "report")


# ---------------------------------------------------------------------------
# Finding collection
# ---------------------------------------------------------------------------


def _int_line(value: Any) -> int | None:
    """Coerce a finding line to a positive int, or ``None``.

    ``bool`` is an ``int`` subclass, so ``line: true`` must not become line 1.
    Zero is not a valid finding line; ``known_intentional`` treats zero as
    "whole file", which is handled separately when reading filter entries.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() and int(text) > 0:
            return int(text)
    return None


def _key_line(value: Any) -> int | None:
    """Coerce a *journal key* line: unlike a finding line, zero is a real key.

    The WebUI's ``triageKey`` renders ``line: 0`` as ``"0"`` (it is what the
    user clicked on), so the console must not silently turn it into the
    whole-file key and then fail to find the record it just wrote.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def finding_key(*, file: Any, line: Any, dimension: Any) -> str:
    """Stable dedup key shared with the WebUI journal (``file:::line:::dimension``).

    ``None`` line numbers canonicalize to the empty string, matching
    ``web.findings_triage`` and the frontend ``triageKey``.
    """
    line_part = "" if line is None else str(line)
    return f"{file}:::{line_part}:::{dimension}"


def _normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one raw finding; ``None`` when it cannot be matched later."""
    file = str(raw.get("file") or "").strip()
    dimension = str(raw.get("dimension") or "").strip()
    if not file:
        return None
    severity = str(raw.get("severity") or "").strip().lower() or "unknown"
    summary = str(raw.get("summary") or "").strip()
    return {
        "file": file,
        "line": _int_line(raw.get("line")),
        "dimension": dimension,
        "severity": severity,
        "summary": summary,
    }


def _findings_of(entry: decision_log.DecisionLogEntry) -> list[dict[str, Any]]:
    data = entry.data if isinstance(entry.data, dict) else None
    if not isinstance(data, dict):
        return []
    if entry.type == "report":
        raw = decision_log.findings_from_report(data)
    else:
        candidate = data.get("findings")
        raw = [item for item in candidate if isinstance(item, dict)] if isinstance(candidate, list) else []
    out: list[dict[str, Any]] = []
    for item in raw:
        normalized = _normalize(item)
        if normalized is not None:
            out.append(normalized)
    return out


@dataclass
class TriageFinding:
    """One deduplicated finding plus its current triage state."""

    index: int
    file: str
    line: int | None
    dimension: str
    severity: str
    summary: str
    round: int
    status: str = STATUS_OPEN
    reason: str | None = None
    decided_at: str | None = None
    effective: bool = False

    @property
    def key(self) -> str:
        return finding_key(file=self.file, line=self.line, dimension=self.dimension)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "key": self.key,
            "file": self.file,
            "line": self.line,
            "dimension": self.dimension,
            "severity": self.severity,
            "summary": self.summary,
            "round": self.round,
            "status": self.status,
            "reason": self.reason,
            "decidedAt": self.decided_at,
            "effective": self.effective,
        }


def collect_findings(project_root: str | Path) -> list[dict[str, Any]]:
    """Deduplicated findings from the decision log, in first-seen order.

    The same ``(file, line, dimension)`` reported again in a later round keeps
    its original position (so indexes are stable while the log only grows) but
    takes the *latest* severity/summary — the operator is triaging the current
    report, not a stale first mention.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in decision_log.read_entries(project_root):
        if entry.type not in _FINDING_ENTRY_TYPES:
            continue
        for finding in _findings_of(entry):
            key = finding_key(file=finding["file"], line=finding["line"], dimension=finding["dimension"])
            if key not in merged:
                order.append(key)
                finding = dict(finding)
                finding["round"] = entry.round
                merged[key] = finding
            else:
                prior = merged[key]
                prior["severity"] = finding["severity"]
                prior["summary"] = finding["summary"] or prior["summary"]
                prior["round"] = entry.round
    return [merged[key] for key in order]


# ---------------------------------------------------------------------------
# State (personalization + config + WebUI journal)
# ---------------------------------------------------------------------------


def known_intentional_entries(project_root: str | Path) -> list[KnownIntentional]:
    """Every filter entry the engine will apply, config first then personalization.

    Mirrors ``tools.iterate_tools._load_known_intentional`` (the review tool's
    own merge) so a status shown here agrees with what the next round filters.
    """
    root = Path(project_root)
    entries: list[KnownIntentional] = []
    effective = config_loader.load_effective_config(root)
    raw_config = effective.config.personalization
    if isinstance(raw_config, dict):
        raw_list = raw_config.get("known_intentional")
        if isinstance(raw_list, list):
            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                entries.append(
                    KnownIntentional(
                        file=str(item.get("file") or ""),
                        dimension=str(item.get("dimension") or ""),
                        reason=str(item.get("reason") or ""),
                        line=_int_line(item.get("line")),
                    )
                )
    entries.extend(personalization.known_intentional_of(None, root))
    return entries


def _matches_known(
    *,
    file: str,
    line: int | None,
    dimension: str,
    known: Sequence[KnownIntentional],
) -> KnownIntentional | None:
    """Return the entry that filters this finding, if any."""
    for entry in known:
        if entry.file != file or entry.dimension != dimension:
            continue
        if entry.line is None or entry.line == 0 or entry.line == line:
            return entry
    return None


def _journal_decisions(project_root: str | Path) -> dict[str, dict[str, Any]]:
    """Latest WebUI journal decision per key (lazy import: console owns the file)."""
    from ..web import findings_triage  # noqa: PLC0415 - keeps the semantic layer web-free at import time

    return findings_triage.load_decisions(project_root)


def triage_report(
    project_root: str | Path,
    *,
    severity: str = "",
    dimension: str = "",
    file_pattern: str = "",
    status: str = "",
    limit: int = 0,
) -> list[TriageFinding]:
    """Collect findings, join their triage state, and apply the CLI filters."""
    root = Path(project_root)
    known = known_intentional_entries(root)
    decisions = _journal_decisions(root)
    wanted_severity = severity.strip().lower()
    wanted_dimension = dimension.strip()
    wanted_status = status.strip().lower()
    rows: list[TriageFinding] = []
    for index, finding in enumerate(collect_findings(root), start=1):
        if wanted_severity and finding["severity"] != wanted_severity:
            continue
        if wanted_dimension and finding["dimension"] != wanted_dimension:
            continue
        if file_pattern and not _matches_file(finding["file"], file_pattern):
            continue
        key = finding_key(file=finding["file"], line=finding["line"], dimension=finding["dimension"])
        known_match = _matches_known(
            file=finding["file"],
            line=finding["line"],
            dimension=finding["dimension"],
            known=known,
        )
        decision = decisions.get(key)
        row_status = STATUS_OPEN
        reason: str | None = None
        decided_at: str | None = None
        effective = False
        if decision is not None:
            decided = str(decision.get("decision") or "").strip().lower()
            if decided in (STATUS_APPROVED, STATUS_REJECTED, STATUS_IGNORED):
                row_status = decided
            reason = str(decision.get("note") or "") or None
            decided_at = str(decision.get("timestamp") or "") or None
        if known_match is not None:
            # The engine filter is the ground truth: a finding can be filtered
            # even when the journal was cleared or was never written (a
            # hand-written known_intentional entry).
            row_status = STATUS_IGNORED
            effective = True
            reason = known_match.reason or reason
        rows.append(
            TriageFinding(
                index=index,
                file=finding["file"],
                line=finding["line"],
                dimension=finding["dimension"],
                severity=finding["severity"],
                summary=finding["summary"],
                round=finding["round"],
                status=row_status,
                reason=reason,
                decided_at=decided_at,
                effective=effective,
            )
        )
    if wanted_status:
        rows = [row for row in rows if row.status == wanted_status]
    if limit > 0:
        rows = rows[:limit]
    return rows


def _matches_file(file: str, pattern: str) -> bool:
    """Exact match, or glob when the pattern contains a wildcard."""
    pattern = pattern.strip()
    if not pattern:
        return True
    if any(token in pattern for token in "*?["):
        return fnmatch.fnmatch(file, pattern)
    return file == pattern


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------


def parse_selectors(tokens: Iterable[str], total: int) -> list[int]:
    """Parse ``all`` / ``3`` / ``1,4-6`` into sorted, unique 1-based indexes.

    Raises ``ValueError`` with an operator-readable message for a bad token or
    an out-of-range index; nothing is silently widened to "everything".
    """
    selected: set[int] = set()
    raw_tokens = [token.strip() for token in tokens if token and token.strip()]
    if not raw_tokens:
        return []
    for token in raw_tokens:
        for part in token.split(","):
            piece = part.strip()
            if not piece:
                continue
            lowered = piece.lower()
            if lowered == "all":
                selected.update(range(1, total + 1))
                continue
            if "-" in piece:
                start_text, _, end_text = piece.partition("-")
                try:
                    start, end = int(start_text), int(end_text)
                except ValueError as exc:
                    raise ValueError(f"invalid selector {piece!r}; use 3, 2-5, or all") from exc
                if start < 1 or end < start or end > total:
                    raise ValueError(
                        f"selector {piece!r} is out of range (findings are 1-{total})"
                    )
                selected.update(range(start, end + 1))
                continue
            try:
                index = int(piece)
            except ValueError as exc:
                raise ValueError(f"invalid selector {piece!r}; use 3, 2-5, or all") from exc
            if index < 1 or index > total:
                raise ValueError(f"selector {piece!r} is out of range (findings are 1-{total})")
            selected.add(index)
    return sorted(selected)


def select_findings(
    rows: Sequence[TriageFinding],
    *,
    selectors: Sequence[str] = (),
    severity: str = "",
    dimension: str = "",
    file_pattern: str = "",
) -> list[TriageFinding]:
    """Resolve selectors + filters to rows.

    A bare command with no selector and no filter returns ``[]`` so callers can
    refuse to act: "ignore everything" must be an explicit ``all``.
    """
    wanted_severity = severity.strip().lower()
    indexes = parse_selectors(selectors, len(rows))
    if not indexes and not (wanted_severity or dimension.strip() or file_pattern.strip()):
        # "Ignore everything" must be an explicit ``all``. Returning an empty
        # list here keeps a caller that forgets to check from wiping the
        # project's findings in one call.
        return []
    if indexes:
        by_index = {row.index: row for row in rows}
        chosen = [by_index[index] for index in indexes]
    else:
        chosen = list(rows)
    return [
        row
        for row in chosen
        if (not wanted_severity or row.severity == wanted_severity)
        and (not dimension.strip() or row.dimension == dimension.strip())
        and (not file_pattern or _matches_file(row.file, file_pattern))
    ]


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


@dataclass
class IgnoreResult:
    """What an ignore/restore attempt changed (and what it could not)."""

    applied: bool
    requested: int = 0
    added: list[dict[str, Any]] = field(default_factory=list)
    already: int = 0
    journaled: int = 0
    removed: int = 0
    config_removed: int = 0
    config: str | None = None
    config_reason: str | None = None
    journal_error: str | None = None
    decision_log_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "requested": self.requested,
            "added": self.added,
            "already": self.already,
            "journaled": self.journaled,
            "removed": self.removed,
            "configRemoved": self.config_removed,
            "config": self.config,
            "configReason": self.config_reason,
            "journalError": self.journal_error,
            "decisionLogError": self.decision_log_error,
        }


def _reason_for(reason: str) -> str:
    detail = (reason or "").strip() or DEFAULT_IGNORE_REASON
    return f"{IGNORE_REASON_PREFIX} {detail}"


def _latest_round(project_root: str | Path) -> int:
    rounds = [entry.round for entry in decision_log.read_entries(project_root) if entry.round >= 0]
    return max(rounds, default=0)


def _record_journal(
    project_root: str | Path,
    rows: Sequence[TriageFinding],
    *,
    decision: str,
    note: str | None,
) -> tuple[int, str | None]:
    from ..web import findings_triage  # noqa: PLC0415 - lazy owner of the journal

    written = 0
    for row in rows:
        findings_triage.record_decision(
            project_root,
            file=row.file,
            line=row.line,
            dimension=row.dimension,
            decision=decision,
            note=note,
        )
        written += 1
    return written, None


def _append_audit_entry(
    project_root: str | Path,
    rows: Sequence[TriageFinding],
    *,
    action: str,
    reason: str,
    added: int,
) -> str | None:
    """Append one ``decision`` entry (best effort); returns an error string."""
    try:
        entry = decision_log.make_entry(
            entry_type="decision",
            round_number=_latest_round(project_root),
            data={
                "kind": "triage",
                "source": "console",
                "action": action,
                "reason": reason,
                "counts": {"requested": len(rows), "changed": added},
                "decisions": [
                    {
                        "file": row.file,
                        "line": row.line,
                        "dimension": row.dimension,
                        "severity": row.severity,
                        "status": STATUS_IGNORED if action == "ignore" else STATUS_OPEN,
                    }
                    for row in rows
                ],
            },
        )
        decision_log.append_entry(project_root, entry)
    except Exception as exc:  # noqa: BLE001 - the audit trail must not undo the filter
        log.warning("triage decision-log append failed: %s", exc)
        return str(exc)
    return None


def _personalization_lock(root: Path) -> Path:
    """Lock file guarding the personalization read-modify-write.

    ``ignore``/``restore``/clear mutate the same JSON store the engine reads;
    two consoles (or a console and the WebUI) acting at once must not lose
    each other's entries, so every mutation is serialized on this sidecar.
    """
    return personalization.storage_dir(None, root) / "personalization.json.lock"


def _drop_added_entries_locked(
    root: Path, added: Sequence[dict[str, Any]], marker_reason: str
) -> int:
    """Remove the entries an ignore just added (journal failure rollback).

    The caller must already hold :func:`_personalization_lock` — re-entering a
    non-reentrant file lock here would deadlock the process.
    """
    keys = {
        (str(item.get("file") or ""), str(item.get("dimension") or ""), item.get("line"))
        for item in added
    }
    if not keys:
        return 0
    data = personalization.load(None, root)
    kept = [
        entry
        for entry in data.known_intentional
        if not (
            (entry.file, entry.dimension, entry.line) in keys
            and entry.reason == marker_reason
        )
    ]
    removed = len(data.known_intentional) - len(kept)
    if not removed:
        return 0
    data.known_intentional = kept
    personalization.save(None, root, data)
    personalization.remove_known_intentional_from_config(
        root,
        [
            KnownIntentional(file=f, dimension=d, reason=marker_reason, line=line)
            for f, d, line in keys
        ],
        reason_prefix=marker_reason,
    )
    return removed


def ignore_findings(
    project_root: str | Path,
    rows: Sequence[TriageFinding],
    *,
    reason: str = "",
    dry_run: bool = False,
) -> IgnoreResult:
    """Persist a permanent "intentional" decision for each row.

    Writes personalization (what the engine filters), the config bridge (what
    a teammate sees in the repository), the WebUI journal (what the console
    shows), and the decision log (the audit trail). The write is transactional
    from the operator's point of view: if the journal record cannot be
    written, the filter entries this call added are rolled back, so a caller
    never sees "ok" while findings are silently suppressed.
    """
    root = Path(project_root)
    result = IgnoreResult(applied=False, requested=len(rows))
    if not rows:
        result.applied = not dry_run
        return result
    marker_reason = _reason_for(reason)
    note = (reason or "").strip() or DEFAULT_IGNORE_REASON

    with exclusive_file_lock(_personalization_lock(root)):
        data = personalization.load(None, root)
        effective_known = known_intentional_entries(root)
        existing = {(entry.file, entry.dimension, entry.line) for entry in effective_known}
        whole_files = {
            (entry.file, entry.dimension)
            for entry in effective_known
            if entry.line in (None, 0)
        }
        for row in rows:
            key = (row.file, row.dimension, row.line)
            if key in existing or (row.file, row.dimension) in whole_files:
                result.already += 1
                continue
            data.known_intentional.append(
                KnownIntentional(
                    file=row.file,
                    dimension=row.dimension,
                    reason=marker_reason,
                    line=row.line,
                )
            )
            existing.add(key)
            result.added.append(
                {
                    "file": row.file,
                    "line": row.line,
                    "dimension": row.dimension,
                    "reason": marker_reason,
                }
            )

        if dry_run:
            return result

        if result.added:
            personalization.save(None, root, data)
            sync = personalization.sync_known_intentional_to_config(
                root, list(data.known_intentional)
            )
            result.config = str(sync.get("config") or "") or None
            result.config_reason = str(sync.get("reason") or "") or None

        try:
            result.journaled, _ = _record_journal(
                root, rows, decision=STATUS_IGNORED, note=note
            )
        except Exception as exc:  # noqa: BLE001 - roll back so nothing hides silently
            log.warning("triage journal append failed, rolling back filter: %s", exc)
            _drop_added_entries_locked(root, result.added, marker_reason)
            result.added = []
            result.journaled = 0
            result.journal_error = str(exc)
            return result

        result.applied = True
        result.decision_log_error = _append_audit_entry(
            root, rows, action="ignore", reason=note, added=len(result.added)
        )
    return result


def restore_findings(
    project_root: str | Path,
    rows: Sequence[TriageFinding],
    *,
    dry_run: bool = False,
) -> IgnoreResult:
    """Undo a permanent ignore for each row (journal + personalization + config).

    Only entries carrying :data:`IGNORE_REASON_PREFIX` are removed, so a
    hand-written ``known_intentional`` entry with the same key survives and
    the status honestly stays "ignored". The config bridge is cleaned even
    when the personalization file no longer has the entry (a teammate may have
    committed the config copy), by matching the same marker prefix.
    """
    root = Path(project_root)
    result = IgnoreResult(applied=False, requested=len(rows))
    if not rows:
        result.applied = not dry_run
        return result
    keys = {(row.file, row.dimension, row.line) for row in rows}

    with exclusive_file_lock(_personalization_lock(root)):
        data = personalization.load(None, root)
        kept: list[KnownIntentional] = []
        removed_entries: list[KnownIntentional] = []
        for entry in data.known_intentional:
            if (
                (entry.file, entry.dimension, entry.line) in keys
                and entry.reason.startswith(IGNORE_REASON_PREFIX)
            ):
                removed_entries.append(entry)
                continue
            kept.append(entry)
        result.removed = len(removed_entries)

        if dry_run:
            return result

        if removed_entries:
            data.known_intentional = kept
            personalization.save(None, root, data)

        # Always ask the config bridge to clean these keys: an entry may exist
        # only in ``iterate.config.yaml`` (an earlier bridge) or the JSON and
        # YAML copies may have diverged.
        candidates = list(removed_entries) + [
            KnownIntentional(
                file=row.file,
                dimension=row.dimension,
                reason=IGNORE_REASON_PREFIX,
                line=row.line,
            )
            for row in rows
        ]
        sync = personalization.remove_known_intentional_from_config(
            root, candidates, reason_prefix=IGNORE_REASON_PREFIX
        )
        config_removed = sync.get("removed")
        result.config = str(sync.get("config") or "") or None
        result.config_reason = str(sync.get("reason") or "") or None
        config_count = len(config_removed) if isinstance(config_removed, list) else 0
        result.config_removed = config_count

        from ..web import findings_triage  # noqa: PLC0415 - lazy owner of the journal

        for row in rows:
            try:
                findings_triage.clear_decision(
                    root, file=row.file, line=row.line, dimension=row.dimension
                )
            except OSError as exc:
                log.warning("triage journal clear failed: %s", exc)
                result.journal_error = str(exc)

        result.applied = True
        if result.removed or config_count:
            result.decision_log_error = _append_audit_entry(
                root,
                rows,
                action="restore",
                reason="",
                added=result.removed + config_count,
            )
    return result


def ignore_one(
    project_root: str | Path,
    *,
    file: str,
    line: int | None,
    dimension: str,
    reason: str = "",
    dry_run: bool = False,
) -> IgnoreResult:
    """Ignore exactly one (file, line, dimension) key (used by the WebUI route)."""
    row = TriageFinding(
        index=0,
        file=file,
        line=_key_line(line),
        dimension=dimension,
        severity="unknown",
        summary="",
        round=0,
    )
    return ignore_findings(project_root, [row], reason=reason, dry_run=dry_run)


def restore_one(
    project_root: str | Path,
    *,
    file: str,
    line: int | None,
    dimension: str,
) -> IgnoreResult:
    """Clear one key's journal decision and machine-added filter entry."""
    row = TriageFinding(
        index=0,
        file=file,
        line=_key_line(line),
        dimension=dimension,
        severity="unknown",
        summary="",
        round=0,
    )
    return restore_findings(project_root, [row])


def clear_auto_ignores(project_root: str | Path) -> int:
    """Remove every machine-added ``known_intentional`` entry; returns the count.

    Used by the WebUI's "clear all triage records" action: leaving the filter
    entries behind would make every finding look untriaged while the engine
    still suppressed them. Hand-written entries (no marker) are preserved.
    """
    root = Path(project_root)
    with exclusive_file_lock(_personalization_lock(root)):
        data = personalization.load(None, root)
        kept: list[KnownIntentional] = []
        removed: list[KnownIntentional] = []
        for entry in data.known_intentional:
            if entry.reason.startswith(IGNORE_REASON_PREFIX):
                removed.append(entry)
            else:
                kept.append(entry)
        if not removed:
            return 0
        data.known_intentional = kept
        personalization.save(None, root, data)
        personalization.remove_known_intentional_from_config(
            root, removed, reason_prefix=IGNORE_REASON_PREFIX
        )
    return len(removed)


__all__ = [
    "DEFAULT_IGNORE_REASON",
    "IGNORE_REASON_PREFIX",
    "IgnoreResult",
    "SEVERITY_ORDER",
    "STATUS_APPROVED",
    "STATUS_IGNORED",
    "STATUS_OPEN",
    "STATUS_REJECTED",
    "TriageFinding",
    "clear_auto_ignores",
    "collect_findings",
    "finding_key",
    "ignore_findings",
    "ignore_one",
    "known_intentional_entries",
    "parse_selectors",
    "restore_findings",
    "restore_one",
    "select_findings",
    "triage_report",
]
