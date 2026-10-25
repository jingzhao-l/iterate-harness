"""The `/iterate` entry points, and the enumerations the READMEs make about them.

WHY THIS FILE EXISTS. Two README sentences and the registry's own command description were rewritten
this month to say "`/iterate` with no argument prints the current subcommand list". Measured, that is
false: a bare `/iterate` takes the **status** branch, and the list is what an *unrecognised*
subcommand prints. The prose was a copy of an older copy, and nothing watched it — which is exactly
how a hand-typed enumeration rots. So the list is pinned to the dispatcher here instead of being
re-typed, and both entry points are checked by calling the handler rather than by trusting a
docstring.

Measured 2026-10-25 with the handler itself, in a project carrying only `iterate.config.yaml`:

    ""                -> "config source: override …"          (no "Usage:" line)
    "no-such-command" -> "Usage: /iterate [status|config|…]"  (19 lines)
"""

from __future__ import annotations

import re
from pathlib import Path

from iterate_harness.commands.iterate import iterate_command_handler
from iterate_harness.commands.registry import CommandContext

REPO = Path(__file__).resolve().parents[2]
HANDLER_SOURCE = REPO / "src" / "iterate_harness" / "commands" / "iterate.py"
REGISTRY_SOURCE = REPO / "src" / "iterate_harness" / "commands" / "registry.py"
README_EN = REPO / "README.md"
README_ZH = REPO / "README.zh-CN.md"

#: `run` also answers to two aliases; a list a reader sees names the canonical spelling once.
ALIASES = {"iterate", "loop"}

USAGE_HEAD = "Usage: /iterate ["


def _dispatched_names() -> set[str]:
    """Every name the handler branches on, read out of the dispatcher's own source."""
    source = HANDLER_SOURCE.read_text(encoding="utf-8")
    body = source[source.index("async def iterate_command_handler") :]
    names: set[str] = set()
    for clause in re.findall(r"sub (?:==|in) \(?([^)\n]*)", body):
        names.update(re.findall(r'"([a-z][a-z-]*)"', clause))
    return names - ALIASES


def _context(tmp_path: Path) -> CommandContext:
    (tmp_path / "iterate.config.yaml").write_text('goal: "g"\n', encoding="utf-8")
    return CommandContext(engine=None, cwd=str(tmp_path))  # type: ignore[arg-type]


async def test_the_bare_command_reports_status_and_not_the_list(tmp_path: Path) -> None:
    message = (await iterate_command_handler("", _context(tmp_path))).message or ""
    assert "config source" in message, message
    assert USAGE_HEAD not in message, (
        "a bare `/iterate` is the status entry; the list belongs to the unrecognised-subcommand "
        "branch, and the READMEs had the two swapped"
    )


async def test_an_unrecognised_subcommand_prints_the_list(tmp_path: Path) -> None:
    message = (await iterate_command_handler("no-such-subcommand", _context(tmp_path))).message or ""
    head = message.splitlines()[0] if message else ""
    assert head.startswith(USAGE_HEAD), message
    listed = {name for name in head[len(USAGE_HEAD) :].rstrip("]").split("|")}
    assert listed == _dispatched_names(), (
        f"the usage line and the dispatcher disagree: {sorted(listed ^ _dispatched_names())}"
    )


async def test_the_handler_still_answers_the_two_entry_points_differently(tmp_path: Path) -> None:
    """The pair the docs describe, as one control: same handler, two different jobs.

    Without this, a future edit that makes both branches print the list would keep the two tests
    above green and make the sentence about status false again.
    """
    status = (await iterate_command_handler("", _context(tmp_path))).message or ""
    usage = (await iterate_command_handler("nonsense", _context(tmp_path))).message or ""
    assert USAGE_HEAD not in status and USAGE_HEAD in usage
    assert "entries" in status, "the status entry counts the decision log — that is its job"


def test_the_documented_enumerations_are_the_dispatched_names() -> None:
    """Both READMEs list the subcommands; a list in prose has to equal the list in code."""
    en = re.search(r"\(status,[^)]*validate\)", README_EN.read_text(encoding="utf-8"))
    assert en is not None, "README.md no longer carries the subcommand enumeration at all"
    en_names = {name.strip() for name in en.group(0)[1:-1].split(",")}

    zh = re.search(r"（status、[^）]*validate）", README_ZH.read_text(encoding="utf-8"))
    assert zh is not None, "README.zh-CN.md no longer carries the subcommand enumeration at all"
    zh_names = {name.strip() for name in zh.group(0)[1:-1].split("、")}

    dispatched = _dispatched_names()
    assert en_names == dispatched, f"README.md drifts from the dispatcher: {sorted(en_names ^ dispatched)}"
    assert zh_names == dispatched, f"README.zh-CN.md drifts: {sorted(zh_names ^ dispatched)}"


def test_the_registry_description_does_not_claim_the_bare_command_prints_the_list() -> None:
    """The description is what an assistant shows before the user types anything."""
    source = REGISTRY_SOURCE.read_text(encoding="utf-8")
    block = source[source.index('SlashCommand(\n            "iterate"') :]
    description = " ".join(re.findall(r'"([^"]+)"', block[:420])[1:])
    assert "no argument prints the current command list" not in description, description
    assert "status" in description and "unrecognised" in description, description
