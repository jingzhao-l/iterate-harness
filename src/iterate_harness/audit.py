"""Shared audit-journal reader and renderers (design §17.3 P13).

The WebUI records every mutating operation (config save, checkpoint restore /
clear, schedule upsert / toggle / delete, findings triage, agent shutdown,
workspace removal, run control) into an append-only ``.iterate/web-audit.jsonl``
journal. Until now that journal was **write-only from an operator's point of
view**: the dashboard surfaced the newest ten lines and nothing else. This
module is the read side both the CLI (``ih audit``) and the console (the
``审计`` page, ``GET /api/v1/audit``) share:

- :func:`iter_audit_entries` streams the whole journal defensively (a corrupt
  line is skipped, never raised — an audit trail that cannot be read must not
  take the page down);
- :func:`list_audit_entries` filters (action prefix / target substring /
  since timestamp), pages newest-first, and reports the matching total so the
  UI can render "showing m of n" instead of guessing;
- :func:`distinct_actions` powers the action filter's dropdown;
- the renderers (:func:`render_audit_table` / :func:`render_audit_markdown` /
  :func:`audit_csv`) turn the same filtered list into the three human/machine
  shapes, so the CLI, the page and the export buttons can never disagree.

The journal's *write* side stays where it lives (``web/security.py``); the
``AUDIT_DIR`` / ``AUDIT_FILE`` constants are declared here so the writer and
the readers cannot drift apart.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Python 3.10 compatibility — ``datetime.UTC`` is only available in 3.11+.
UTC = timezone.utc

#: Audit journal file name (inside the project's ``.iterate`` directory).
#: Declared here, imported by ``web.security.AuditLog`` so writer and reader
#: share one source of truth.
AUDIT_DIR = ".iterate"
AUDIT_FILE = "web-audit.jsonl"


def audit_path(project_root: str | Path) -> Path:
    """Resolve the audit journal path for a project root."""
    return Path(project_root) / AUDIT_DIR / AUDIT_FILE


def iter_audit_entries(project_root: str | Path) -> Iterator[dict[str, Any]]:
    """Yield every audit entry in the journal (oldest first, defensive).

    Corrupt lines and non-dict lines are skipped — an audit trail is evidence,
    and a single mangled line must never blank the whole view (nor hide the
    healthy lines around it). A missing or unreadable journal yields nothing.
    """
    path = audit_path(project_root)
    try:
        if not path.is_file():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                yield parsed
    except OSError:
        # A journal we cannot read is a warning, not a crash: the trail may be
        # on a read-only mount or mid-rotation. Return nothing rather than
        # raising into a 500 on a page that exists to *show* history.
        return


def _matches(
    entry: dict[str, Any],
    *,
    action: str,
    target: str,
    since: datetime | None,
) -> bool:
    """Apply the route/CLI filters to one entry (pure)."""
    if action:
        entry_action = str(entry.get("action") or "")
        if not entry_action.startswith(action):
            return False
    if target:
        entry_target = str(entry.get("target") or "")
        if target not in entry_target:
            return False
    if since is not None:
        raw_timestamp = entry.get("timestamp")
        if isinstance(raw_timestamp, str):
            try:
                parsed = datetime.fromisoformat(raw_timestamp)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                if parsed < since:
                    return False
    return True


def list_audit_entries(
    project_root: str | Path,
    *,
    action: str = "",
    target: str = "",
    since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(page, total)`` of matching entries, newest first.

    ``action`` is a prefix match (``config`` matches ``config.save``); a
    missing journal is an empty page with ``total`` 0 — never an error.
    """
    matched: list[dict[str, Any]] = []
    for entry in iter_audit_entries(project_root):
        if _matches(entry, action=action, target=target, since=since):
            matched.append(entry)
    total = len(matched)
    # Newest first: the console's job is "what happened most recently".
    matched.reverse()
    page = matched[offset : offset + limit]
    return page, total


def distinct_actions(project_root: str | Path) -> list[str]:
    """Every distinct action prefix-group seen in the journal (sorted).

    Returns the top-level action words (``config``, ``checkpoint``,
    ``schedule``, ``findings``, ``agent``, ``workspace``, ``run``) so the
    filter dropdown is stable even when the underlying dotted actions vary.
    """
    seen: set[str] = set()
    for entry in iter_audit_entries(project_root):
        action = str(entry.get("action") or "")
        head = action.split(".", 1)[0]
        if head:
            seen.add(head)
    return sorted(seen)


def _ts(entry: dict[str, Any]) -> str:
    return str(entry.get("timestamp") or "")


def render_audit_table(entries: list[dict[str, Any]], *, total: int) -> str:
    """Render the filtered entries as a fixed-width table (CLI default)."""
    lines: list[str] = []
    if not entries:
        return "(no audit entries)"
    lines.append(f"{'TIME':<20}  {'ACTION':<24}  {'TARGET':<28}  SUMMARY")
    lines.append("-" * 100)
    for entry in entries:
        timestamp = _ts(entry)
        if len(timestamp) > 16:
            timestamp = timestamp[5:16].replace("T", " ")
        action = str(entry.get("action") or "")[:24]
        target = str(entry.get("target") or "")[:28]
        summary = entry.get("summary")
        summary_text = json.dumps(summary, ensure_ascii=False) if isinstance(summary, dict) else ""
        lines.append(f"{timestamp:<20}  {action:<24}  {target:<28}  {summary_text}")
    lines.append(f"\n{len(entries)} of {total} audit entr{'y' if total == 1 else 'ies'} shown")
    return "\n".join(lines)


def render_audit_markdown(entries: list[dict[str, Any]], project_root: str | Path) -> str:
    """Render the filtered entries as a paste-ready Markdown digest."""
    lines: list[str] = [
        "# 审计记录",
        "",
        f"- 项目：`{Path(project_root)}`",
        f"- 条目：{len(entries)}",
        "",
        "| 时间 | 操作 | 目标 | 摘要 |",
        "| --- | --- | --- | --- |",
    ]
    for entry in entries:
        summary = entry.get("summary")
        summary_text = json.dumps(summary, ensure_ascii=False) if isinstance(summary, dict) else ""
        summary_text = summary_text.replace("|", "\\|").replace("\n", " ")
        target = str(entry.get("target") or "").replace("|", "\\|").replace("\n", " ")
        action = str(entry.get("action") or "").replace("|", "\\|")
        lines.append(f"| {_ts(entry)} | {action} | {target} | {summary_text} |")
    return "\n".join(lines) + "\n"


def audit_csv(entries: list[dict[str, Any]]) -> str:
    """Render the filtered entries as CSV (one row per entry)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["timestamp", "action", "target", "summary"])
    for entry in entries:
        summary = entry.get("summary")
        writer.writerow(
            [
                _ts(entry),
                str(entry.get("action") or ""),
                str(entry.get("target") or ""),
                json.dumps(summary, ensure_ascii=False) if isinstance(summary, dict) else "",
            ]
        )
    return buffer.getvalue()


def audit_export_filename(fmt: str) -> str:
    """Stamp a sortable, UTC-based download name for an audit export."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"iterate-audit-{stamp}.{fmt}"


__all__ = [
    "AUDIT_DIR",
    "AUDIT_FILE",
    "audit_csv",
    "audit_export_filename",
    "audit_path",
    "distinct_actions",
    "iter_audit_entries",
    "list_audit_entries",
    "render_audit_markdown",
    "render_audit_table",
]
