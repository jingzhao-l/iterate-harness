"""Skill data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class SkillMetadata(TypedDict):
    """Unvalidated metadata extracted from a SKILL.md frontmatter block."""

    name: str
    description: str
    user_invocable: bool
    disable_model_invocation: bool
    model: str | None
    argument_hint: str | None


@dataclass(frozen=True)
class SkillDefinition:
    """A loaded skill."""

    name: str
    description: str
    content: str
    source: str
    path: str | None = None
    base_dir: str | None = None
    command_name: str | None = None
    display_name: str | None = None
    aliases: tuple[str, ...] = ()
    user_invocable: bool = True
    disable_model_invocation: bool = False
    model: str | None = None
    argument_hint: str | None = None
