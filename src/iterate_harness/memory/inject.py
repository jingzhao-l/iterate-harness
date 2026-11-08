"""Build the memory sections that are injected into the runtime system prompt.

The runtime prompt (``iterate_harness.prompts.context.build_runtime_system_prompt``)
injects two memory sections: the ``MEMORY.md`` index and the memories whose
metadata/body match the latest user prompt. The *reader* surfaces
(``ih memory preview`` and the console's memory page) must show exactly what
the model will be told, not an approximation — otherwise a "preview" that
disagrees with the prompt is worse than no preview at all.

:func:`build_memory_injection` is therefore the single implementation: the
prompt builder calls it, and so does the inventory. It reports per-file
included characters and truncation so the operator can see when a memory was
clipped by the per-file budget instead of silently losing context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .memdir import load_memory_prompt
from .paths import get_memory_entrypoint
from .search import find_relevant_memories

#: Per-file cap used by the runtime prompt. Kept here (not in ``settings``) so
#: the preview and the prompt share one constant.
DEFAULT_MAX_ENTRY_CHARS = 8000


@dataclass(frozen=True)
class InjectionFile:
    """One memory file as it appears (or would appear) in the system prompt."""

    name: str
    title: str
    chars_total: int
    chars_included: int
    truncated: bool


@dataclass(frozen=True)
class MemoryInjection:
    """The memory sections for one prompt plus the budget accounting behind them."""

    sections: tuple[str, ...]
    files: tuple[InjectionFile, ...]
    entrypoint: InjectionFile | None
    entrypoint_lines_total: int
    entrypoint_lines_included: int
    max_files: int
    max_entry_chars: int

    def rendered(self) -> str:
        """Return the exact text appended to the system prompt."""
        return "\n\n".join(section for section in self.sections if section.strip())

    def summary(self) -> dict[str, object]:
        """Return a JSON-ready summary for the CLI/API surfaces."""
        return {
            "chars": len(self.rendered()),
            "files": len(self.files),
            "max_files": self.max_files,
            "max_entry_chars": self.max_entry_chars,
            "entrypoint_lines_total": self.entrypoint_lines_total,
            "entrypoint_lines_included": self.entrypoint_lines_included,
            "entrypoint_truncated": bool(
                self.entrypoint is not None and self.entrypoint.truncated
            ),
            "truncated_files": [
                {
                    "name": item.name,
                    "chars_total": item.chars_total,
                    "chars_included": item.chars_included,
                }
                for item in self.files
                if item.truncated
            ],
            "included_files": [
                {
                    "name": item.name,
                    "title": item.title,
                    "chars_total": item.chars_total,
                    "chars_included": item.chars_included,
                    "truncated": item.truncated,
                }
                for item in self.files
            ],
        }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def build_memory_injection(
    cwd: str | Path,
    latest_prompt: str | None = None,
    *,
    max_entrypoint_lines: int = 200,
    max_files: int = 5,
    max_entry_chars: int = DEFAULT_MAX_ENTRY_CHARS,
) -> MemoryInjection:
    """Return the memory sections a runtime prompt would carry for *cwd*.

    ``latest_prompt`` drives the relevance search. Without it only the
    ``MEMORY.md`` index section is produced — exactly like the runtime prompt,
    which skips the "Relevant Memories" section when there is no user prompt.
    Unreadable files are skipped rather than raising: a broken memory file must
    never take down prompt assembly.
    """
    entrypoint_path = get_memory_entrypoint(cwd)
    entrypoint_text = _read_text(entrypoint_path) if entrypoint_path.exists() else ""
    entrypoint_lines_total = len(entrypoint_text.splitlines()) if entrypoint_text else 0
    entrypoint_lines_included = min(entrypoint_lines_total, max_entrypoint_lines)

    entrypoint: InjectionFile | None = None
    if entrypoint_path.exists():
        entrypoint = InjectionFile(
            name=entrypoint_path.name,
            title="MEMORY.md",
            chars_total=len(entrypoint_text),
            chars_included=len("\n".join(entrypoint_text.splitlines()[:max_entrypoint_lines])),
            truncated=entrypoint_lines_total > max_entrypoint_lines,
        )

    sections: list[str] = []
    memory_section = load_memory_prompt(cwd, max_entrypoint_lines=max_entrypoint_lines)
    if memory_section:
        sections.append(memory_section)

    files: list[InjectionFile] = []
    if latest_prompt:
        relevant = find_relevant_memories(latest_prompt, cwd, max_results=max_files)
        lines = ["# Relevant Memories"] if relevant else []
        for header in relevant:
            content = _read_text(header.path).strip()
            included = content[:max_entry_chars]
            files.append(
                InjectionFile(
                    name=header.path.name,
                    title=header.title,
                    chars_total=len(content),
                    chars_included=len(included),
                    truncated=len(content) > max_entry_chars,
                )
            )
            lines.extend(
                [
                    "",
                    f"## {header.path.name}",
                    "```md",
                    included,
                    "```",
                ]
            )
        if lines:
            sections.append("\n".join(lines))

    return MemoryInjection(
        sections=tuple(sections),
        files=tuple(files),
        entrypoint=entrypoint,
        entrypoint_lines_total=entrypoint_lines_total,
        entrypoint_lines_included=entrypoint_lines_included,
        max_files=max_files,
        max_entry_chars=max_entry_chars,
    )


__all__ = [
    "DEFAULT_MAX_ENTRY_CHARS",
    "InjectionFile",
    "MemoryInjection",
    "build_memory_injection",
]
