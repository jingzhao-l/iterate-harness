"""Read-only memory inventory (``ih memory list`` + ``GET /api/v1/memory``).

The engine has always injected project memory into the runtime prompt — the
``MEMORY.md`` index plus the files whose metadata/body match the latest user
prompt — but the operator could only see that memory from inside the REPL's
``/memory`` slash command. A memory that was silently clipped by the per-file
budget, or an index entry pointing at a file that no longer exists, was
invisible everywhere else.

:func:`build_memory_inventory` returns one normalized, JSON-ready model shared
by the CLI and the console page. It is read-only and never creates the memory
directory: an empty project reports ``exists: false`` rather than being written
to just because someone looked.

:func:`build_memory_injection_preview` wraps the *same* builder the runtime
prompt uses (:func:`iterate_harness.memory.inject.build_memory_injection`), so
"what would the model be told for this task?" is answered by the code path that
actually tells it.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from .inject import build_memory_injection
from .paths import project_memory_dir
from .scan import scan_memory_files
from .search import find_relevant_memories
from .types import MemoryHeader

UTC = datetime.timezone.utc

#: Largest single memory file the inventory will read for ``show``/detail.
MAX_ENTRY_BYTES = 2 * 1024 * 1024


def _iso(timestamp: float) -> str:
    return datetime.datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _entry_row(cwd: Path, header: MemoryHeader) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": header.path.name,
        "title": header.title,
        "description": header.description,
        "type": header.memory_type,
        "modified_at": _iso(header.modified_at),
        "body_preview": header.body_preview,
        "bytes": 0,
        "path": str(header.path),
    }
    try:
        row["bytes"] = header.path.stat().st_size
    except OSError:
        row["bytes"] = len(header.body_preview.encode("utf-8"))
    return row


def _entrypoint_row(cwd: Path) -> dict[str, Any]:
    path = project_memory_dir(cwd) / "MEMORY.md"
    row: dict[str, Any] = {
        "name": "MEMORY.md",
        "path": str(path),
        "exists": path.exists(),
        "lines": 0,
        "bytes": 0,
        "preview": "",
    }
    if not path.exists():
        return row
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        row["error"] = f"cannot read: {exc}"
        return row
    lines = text.splitlines()
    row["lines"] = len(lines)
    row["bytes"] = len(text.encode("utf-8"))
    row["preview"] = "\n".join(lines[:200])
    return row


def _index_targets(entrypoint: dict[str, Any]) -> list[str]:
    """Return the ``.md`` filenames linked from MEMORY.md (in order)."""
    preview = str(entrypoint.get("preview") or "")
    targets: list[str] = []
    for line in preview.splitlines():
        marker = "]("
        if marker not in line:
            continue
        start = line.find(marker) + len(marker)
        end = line.find(")", start)
        if end <= start:
            continue
        target = line[start:end].strip()
        if target.endswith(".md") and "/" not in target:
            targets.append(target)
    return targets


def _settings_memory(settings: Any) -> tuple[bool, int, int]:
    """Return (enabled, max_files, max_entrypoint_lines) from settings or defaults."""
    if settings is None:
        try:
            from ..config.settings import load_settings

            settings = load_settings()
        except Exception:  # noqa: BLE001 - a broken settings file must not break a read-only page
            settings = None
    memory = getattr(settings, "memory", None)
    enabled = bool(getattr(memory, "enabled", True))
    max_files = int(getattr(memory, "max_files", 5) or 5)
    max_entrypoint_lines = int(getattr(memory, "max_entrypoint_lines", 200) or 200)
    return enabled, max_files, max_entrypoint_lines


def build_memory_injection_preview(
    project_root: str | Path,
    prompt: str,
    *,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Return exactly what memory would be injected for *prompt*.

    Uses the runtime prompt's builder (and the project's configured budgets) so
    the preview cannot disagree with the model's actual context.
    """
    cwd = Path(project_root).expanduser().resolve()
    enabled, max_files, max_entrypoint_lines = _settings_memory(settings)
    if not enabled:
        return {
            "enabled": False,
            "prompt": prompt,
            "rendered": "",
            "chars": 0,
            "files": 0,
            "max_files": max_files,
            "max_entry_chars": 0,
            "entrypoint_lines_total": 0,
            "entrypoint_lines_included": 0,
            "entrypoint_truncated": False,
            "truncated_files": [],
            "included_files": [],
            "reason": "memory is disabled in settings (memory.enabled = false)",
        }
    injection = build_memory_injection(
        cwd,
        prompt,
        max_entrypoint_lines=max_entrypoint_lines,
        max_files=max_files,
    )
    view = injection.summary()
    view.update(
        {
            "enabled": True,
            "prompt": prompt,
            "rendered": injection.rendered(),
            "reason": "",
        }
    )
    return view


def build_memory_inventory(
    project_root: str | Path,
    *,
    query: str = "",
    prompt: str = "",
    settings: Any | None = None,
    max_entries: int = 500,
) -> dict[str, Any]:
    """Build the read-only memory inventory for *project_root*.

    ``query`` runs the engine's relevance search and returns ``matches`` in
    rank order. ``prompt`` additionally produces the full injection preview.
    Nothing here raises for a single unreadable file: it becomes a ``problems``
    row, because explaining a broken memory is the point of the inventory.
    """
    cwd = Path(project_root).expanduser().resolve()
    memory_dir = project_memory_dir(cwd)
    problems: list[dict[str, str]] = []
    now = datetime.datetime.now(UTC)

    exists = memory_dir.is_dir()
    entrypoint = _entrypoint_row(cwd)
    if entrypoint.get("error"):
        problems.append(
            {
                "name": "MEMORY.md",
                "detail": str(entrypoint["error"]),
                "remedy": f"chmod +r {entrypoint.get('path')} (or delete it and re-add memories)",
            }
        )

    headers: list[MemoryHeader] = []
    if exists:
        try:
            headers = scan_memory_files(cwd, max_files=max_entries)
        except OSError as exc:  # pragma: no cover - defensive, scan swallows per-file OSError
            problems.append(
                {
                    "name": str(memory_dir),
                    "detail": f"cannot scan memory directory: {exc}",
                    "remedy": f"check permissions on {memory_dir}",
                }
            )
    entries = [_entry_row(cwd, header) for header in headers]

    # An index that links a memory file which no longer exists is a dead end
    # for the model (it reads MEMORY.md as the entrypoint). Report it here
    # instead of letting the agent chase a missing file.
    present = {row["name"] for row in entries}
    missing_index_targets = [
        target for target in _index_targets(entrypoint) if target not in present
    ]
    for target in missing_index_targets:
        problems.append(
            {
                "name": target,
                "detail": "MEMORY.md links a file that does not exist",
                "remedy": f"ih memory remove {Path(target).stem} --prune-index  # or restore {target}",
            }
        )

    matches: list[dict[str, Any]] = []
    if query.strip():
        matched_headers = find_relevant_memories(query, cwd, max_results=max_entries)
        by_name = {row["name"]: row for row in entries}
        for header in matched_headers:
            row = by_name.get(header.path.name)
            if row is not None:
                matches.append(row)

    injection: dict[str, Any] | None = None
    if prompt.strip():
        injection = build_memory_injection_preview(cwd, prompt, settings=settings)

    total_bytes = sum(int(row["bytes"]) for row in entries)
    enabled, max_files, max_entrypoint_lines = _settings_memory(settings)
    summary = {
        "entries": len(entries),
        "bytes": total_bytes,
        "index_lines": int(entrypoint.get("lines") or 0),
        "entrypoint_exists": bool(entrypoint.get("exists")),
        "index_targets": len(_index_targets(entrypoint)),
        "missing_index_targets": len(missing_index_targets),
        "matched": len(matches),
        "enabled": enabled,
        "max_files": max_files,
        "max_entrypoint_lines": max_entrypoint_lines,
        "problems": len(problems),
    }
    return {
        "project_root": str(cwd),
        "generated_at": now.isoformat(),
        "memory_dir": str(memory_dir),
        "exists": exists,
        "query": query,
        "prompt": prompt,
        "summary": summary,
        "entrypoint": entrypoint,
        "entries": entries,
        "matches": matches,
        "injection": injection,
        "problems": problems,
    }


def _resolve_memory_file(project_root: str | Path, name: str) -> Path | None:
    """Resolve *name* inside the project memory dir, rejecting escapes."""
    memory_dir = project_memory_dir(project_root).resolve()
    candidate = Path(name.strip())
    if not candidate.name or candidate.is_absolute() or candidate.name != name.strip():
        return None
    if candidate.suffix == "":
        candidate = candidate.with_suffix(".md")
    if candidate.suffix != ".md":
        return None
    try:
        resolved = (memory_dir / candidate).resolve()
        resolved.relative_to(memory_dir)
    except (ValueError, OSError):
        return None
    return resolved


def read_memory_entry(
    project_root: str | Path,
    name: str,
    *,
    max_bytes: int = MAX_ENTRY_BYTES,
) -> dict[str, Any] | None:
    """Return one memory file's full content, or ``None`` when it does not exist."""
    path = _resolve_memory_file(project_root, name)
    if path is None or not path.is_file():
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(max_bytes + 1)
    except OSError:
        return None
    truncated = len(content) > max_bytes
    if truncated:
        content = content[:max_bytes]
    return {
        "name": path.name,
        "path": str(path),
        "bytes": size,
        "content": content,
        "truncated": truncated,
        "modified_at": _iso(path.stat().st_mtime) if path.exists() else "",
    }


def render_memory_inventory(inventory: dict[str, Any], *, limit: int = 50) -> str:
    """Render the inventory as human-readable terminal text."""
    summary = inventory.get("summary") or {}
    lines = [
        f"IterateHarness project memory — {inventory.get('project_root', '')}",
        f"dir: {inventory.get('memory_dir', '')}",
        (
            f"entries: {summary.get('entries', 0)} · bytes: {summary.get('bytes', 0)} · "
            f"index: {summary.get('index_lines', 0)} lines · "
            f"injection: {'enabled' if summary.get('enabled') else 'disabled'} "
            f"(max_files={summary.get('max_files', 0)}, "
            f"max_entrypoint_lines={summary.get('max_entrypoint_lines', 0)})"
        ),
    ]
    if not inventory.get("exists"):
        lines.extend(
            [
                "",
                "Memory directory does not exist yet — nothing has been remembered for this project.",
                "Add one with: ih memory add \"Title\" --content \"...\" (or /memory add in the REPL).",
            ]
        )
        return "\n".join(lines)

    problems = inventory.get("problems") or []
    if problems:
        lines.append("")
        lines.append(f"Problems ({len(problems)}):")
        for problem in problems:
            lines.append(f"  {problem.get('name', '?')}: {problem.get('detail', '')}")
            remedy = str(problem.get("remedy") or "").strip()
            if remedy:
                lines.append(f"    remedy: {remedy}")

    matches = inventory.get("matches") or []
    rows = matches if inventory.get("query") else (inventory.get("entries") or [])
    title = "Matches" if inventory.get("query") else "Entries"
    lines.append("")
    lines.append(f"{title} ({len(rows)}):")
    if not rows:
        if inventory.get("query"):
            lines.append("  (no memory matched this query)")
        else:
            lines.append("  (none)")
        return "\n".join(lines)
    for row in rows[:limit]:
        kind = f" [{row.get('type')}]" if row.get("type") else ""
        lines.append(
            f"  {row.get('name')}{kind} — {row.get('description') or row.get('title') or ''}"
        )
        lines.append(
            f"    {row.get('bytes', 0)} bytes · modified {str(row.get('modified_at', ''))[:19]}"
        )
    if len(rows) > limit:
        lines.append(f"  … {len(rows) - limit} more (raise --limit)")

    injection = inventory.get("injection")
    if injection:
        lines.append("")
        lines.append("Injection preview for the given task:")
        if not injection.get("enabled"):
            lines.append(f"  disabled — {injection.get('reason')}")
        else:
            lines.append(
                f"  {injection.get('chars', 0)} chars · files used "
                f"{injection.get('files', 0)}/{injection.get('max_files', 0)} · "
                f"index lines {injection.get('entrypoint_lines_included', 0)}/"
                f"{injection.get('entrypoint_lines_total', 0)}"
            )
            truncated = injection.get("truncated_files") or []
            for item in truncated:
                lines.append(
                    f"  clipped: {item.get('name')} "
                    f"({item.get('chars_included')}/{item.get('chars_total')} chars)"
                )
    return "\n".join(lines)


__all__ = [
    "MAX_ENTRY_BYTES",
    "build_memory_injection_preview",
    "build_memory_inventory",
    "read_memory_entry",
    "render_memory_inventory",
]
