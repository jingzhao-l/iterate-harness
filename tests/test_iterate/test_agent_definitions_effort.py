"""Regression tests for ``effort`` parsing in coordinator/agent_definitions.py.

The coordinator test tree is out of scope for this change set, so the guard is
pinned here: ``bool`` subclasses ``int``, so a frontmatter ``effort: true``
used to be silently accepted as effort level 1.
"""

from __future__ import annotations

from pathlib import Path

from iterate_harness.coordinator.agent_definitions import AgentDefinition, load_agents_dir

AGENT_TEMPLATE = """---
name: tester
description: test agent
{effort_line}
---

You are a test agent.
"""


def _agent_with_effort(tmp_path: Path, effort_line: str = "") -> AgentDefinition:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "tester.md").write_text(
        AGENT_TEMPLATE.format(effort_line=effort_line), encoding="utf-8"
    )
    agents = load_agents_dir(agents_dir)
    assert len(agents) == 1
    return agents[0]


def test_boolean_effort_is_rejected(tmp_path: Path) -> None:
    """``effort: true`` is a YAML bool, not "effort level 1"."""
    assert _agent_with_effort(tmp_path, "effort: true").effort is None
    assert _agent_with_effort(tmp_path, "effort: false").effort is None


def test_positive_integer_effort_is_kept(tmp_path: Path) -> None:
    assert _agent_with_effort(tmp_path, "effort: 3").effort == 3


def test_named_effort_levels_are_kept(tmp_path: Path) -> None:
    for level in ("low", "medium", "high"):
        assert _agent_with_effort(tmp_path, f"effort: {level}").effort == level


def test_non_positive_integer_effort_is_rejected(tmp_path: Path) -> None:
    assert _agent_with_effort(tmp_path, "effort: 0").effort is None
    assert _agent_with_effort(tmp_path, "effort: -2").effort is None


def test_unparseable_effort_is_rejected(tmp_path: Path) -> None:
    assert _agent_with_effort(tmp_path, 'effort: "turbo"').effort is None


def test_absent_effort_stays_none(tmp_path: Path) -> None:
    assert _agent_with_effort(tmp_path).effort is None
