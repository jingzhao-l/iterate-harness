"""Helpers for managing memory files."""

from __future__ import annotations

from pathlib import Path
from re import sub

from iterate_harness.memory.paths import get_memory_entrypoint, get_project_memory_dir
from iterate_harness.utils.file_lock import exclusive_file_lock
from iterate_harness.utils.fs import atomic_write_text


def _memory_lock_path(cwd: str | Path) -> Path:
    return get_project_memory_dir(cwd) / ".memory.lock"


def list_memory_files(cwd: str | Path) -> list[Path]:
    """List memory markdown files for the project."""
    memory_dir = get_project_memory_dir(cwd)
    return sorted(path for path in memory_dir.glob("*.md"))


def add_memory_entry(
    cwd: str | Path,
    title: str,
    content: str,
    *,
    overwrite: bool = True,
) -> Path:
    """Create a memory file and append it to MEMORY.md.

    ``overwrite`` keeps the REPL's long-standing behavior (re-adding a title
    replaces the file). Callers that must not silently destroy context (the
    CLI's ``ih memory add`` and the console's memory page) pass
    ``overwrite=False`` and handle the conflict themselves.
    """
    memory_dir = get_project_memory_dir(cwd)
    slug = sub(r"[^a-zA-Z0-9]+", "_", title.strip().lower()).strip("_") or "memory"
    path = memory_dir / f"{slug}.md"
    with exclusive_file_lock(_memory_lock_path(cwd)):
        if not overwrite and path.exists():
            raise FileExistsError(f"memory '{slug}.md' already exists")
        atomic_write_text(path, content.strip() + "\n")

        entrypoint = get_memory_entrypoint(cwd)
        existing = entrypoint.read_text(encoding="utf-8") if entrypoint.exists() else "# Memory Index\n"
        if path.name not in existing:
            existing = existing.rstrip() + f"\n- [{title}]({path.name})\n"
            atomic_write_text(entrypoint, existing)
    return path


def prune_memory_index(cwd: str | Path, name: str) -> bool:
    """Remove MEMORY.md entries that link a file which no longer exists.

    ``remove_memory_entry`` prunes the index when it deletes a file, so a dead
    link only survives when the file disappeared behind its back (deleted by
    hand, or the index was edited). This repairs that without requiring the
    file to be present.

    Returns ``True`` when the index changed.
    """
    entrypoint = get_memory_entrypoint(cwd)
    if not entrypoint.exists():
        return False
    target = Path(name).name
    if target.endswith(".md"):
        target = target[: -len(".md")]
    with exclusive_file_lock(_memory_lock_path(cwd)):
        lines = entrypoint.read_text(encoding="utf-8").splitlines()
        filtered = [
            line
            for line in lines
            if f"]({target}.md)" not in line and f"]({target})" not in line
        ]
        if len(filtered) == len(lines):
            return False
        atomic_write_text(entrypoint, "\n".join(filtered).rstrip() + "\n")
    return True


def remove_memory_entry(cwd: str | Path, name: str) -> bool:
    """Delete a memory file and remove its index entry."""
    memory_dir = get_project_memory_dir(cwd)
    entrypoint = get_memory_entrypoint(cwd)
    # Never delete the index/entrypoint file itself (MEMORY.md) — memory_scan
    # treats it as the index and skips it; deleting it would break every
    # subsequent add/list/scan.
    matches = [
        path
        for path in memory_dir.glob("*.md")
        if path != entrypoint and (path.stem == name or path.name == name)
    ]
    if not matches:
        return False
    path = matches[0]
    with exclusive_file_lock(_memory_lock_path(cwd)):
        if path.exists():
            path.unlink()

        if entrypoint.exists():
            lines = [
                line
                for line in entrypoint.read_text(encoding="utf-8").splitlines()
                if path.name not in line
            ]
            atomic_write_text(entrypoint, "\n".join(lines).rstrip() + "\n")
    return True
