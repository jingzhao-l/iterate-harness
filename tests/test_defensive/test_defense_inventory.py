"""Guard resolution + inventory tests (``ih defense show`` / ``/api/v1/defense``).

The inventory must resolve the guard with the kernel's own rules, or the page
would confidently describe checks the kernel will never run. These tests pin
the four sources (invariants / validation fallback / explicit disable /
none), the empty-module degradation, the "entry that can never execute"
problems, and the assumption reconstruction from the decision log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iterate_harness.defensive.inventory import (
    build_defense_inventory,
    render_defense_inventory,
)
from iterate_harness.defensive.invariants import (
    SOURCE_DISABLED,
    SOURCE_INVARIANTS,
    SOURCE_NONE,
    SOURCE_VALIDATION_FALLBACK,
    InvariantReport,
    InvariantViolation,
    report_outcome,
    resolve_invariants,
)
from iterate_harness.defensive.journal import (
    KIND_INVARIANT,
    KIND_ROLLBACK,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    record_defense_event,
)
from iterate_harness.iterate.config_loader import EffectiveConfig, load_effective_config
from iterate_harness.iterate.types import (
    InvariantConfig,
    IterateConfig,
    ValidationConfig,
)


def _effective(
    *,
    invariants: InvariantConfig | None = None,
    validation_commands: dict[str, list[str]] | None = None,
    override: dict[str, object] | None = None,
) -> EffectiveConfig:
    config = IterateConfig(
        invariants=invariants,
        validation=ValidationConfig(commands=validation_commands or {}),
    )
    return EffectiveConfig(
        config=config,
        source="override",
        override=override if override is not None else {},
    )


def test_resolves_invariants_section() -> None:
    resolved = resolve_invariants(
        _effective(
            invariants=InvariantConfig(
                ensure=["README.md"], commands={"core": ["pytest -q"], "empty": []}
            ),
            override={"invariants": {"commands": {"core": ["pytest -q"]}}},
        )
    )
    assert resolved.source == SOURCE_INVARIANTS
    assert resolved.configured is True
    assert resolved.checks == 2
    assert resolved.commands == {"core": ["pytest -q"]}
    assert resolved.dropped_empty_modules == ["empty"]


def test_falls_back_to_validation_commands() -> None:
    resolved = resolve_invariants(
        _effective(validation_commands={"lint": ["ruff check ."]})
    )
    assert resolved.source == SOURCE_VALIDATION_FALLBACK
    assert resolved.configured is True
    assert resolved.checks == 1
    assert resolved.ensure == []


def test_explicit_empty_invariants_disables_fallback() -> None:
    resolved = resolve_invariants(
        _effective(
            invariants=InvariantConfig(),
            validation_commands={"lint": ["ruff check ."]},
            override={"invariants": {}},
        )
    )
    assert resolved.source == SOURCE_DISABLED
    assert resolved.configured is True
    assert resolved.checks == 0
    assert resolved.commands == {}


def test_only_empty_module_lists_degrades_to_disabled() -> None:
    resolved = resolve_invariants(
        _effective(
            invariants=InvariantConfig(commands={"core": []}),
            override={"invariants": {"commands": {"core": []}}},
        )
    )
    assert resolved.source == SOURCE_DISABLED
    assert resolved.configured is True
    assert resolved.checks == 0
    assert resolved.dropped_empty_modules == ["core"]


def test_no_guard_configured() -> None:
    resolved = resolve_invariants(_effective())
    assert resolved.source == SOURCE_NONE
    assert resolved.configured is False
    assert resolved.checks == 0


def test_report_outcome_vocabulary() -> None:
    assert report_outcome(InvariantReport(passed=True, checks_run=1)) == OUTCOME_PASS
    assert report_outcome(InvariantReport(passed=True, checks_run=0)) == "unmeasured"
    assert (
        report_outcome(
            InvariantReport(
                passed=False,
                checks_run=1,
                violations=[InvariantViolation(kind="command", label="x", detail="")],
            )
        )
        == OUTCOME_FAIL
    )
    assert (
        report_outcome(
            InvariantReport(
                passed=False,
                checks_run=1,
                violations=[InvariantViolation(kind="refused", label="x", detail="")],
            )
        )
        == "error"
    )


def _write_config(project: Path, body: str) -> None:
    (project / "iterate.config.yaml").write_text(body, encoding="utf-8")


def test_inventory_resolves_disk_config_and_journal(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    (tmp_path / "check.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (tmp_path / "check.sh").chmod(0o755)
    _write_config(
        tmp_path,
        "invariants:\n"
        "  ensure:\n"
        "    - README.md\n"
        "  commands:\n"
        "    core:\n"
        "      - ./check.sh\n",
    )
    record_defense_event(
        tmp_path,
        kind=KIND_INVARIANT,
        outcome=OUTCOME_PASS,
        tool="write_file",
        path="README.md",
        checks_run=2,
    )
    record_defense_event(
        tmp_path,
        kind=KIND_ROLLBACK,
        outcome=OUTCOME_FAIL,
        tool="edit_file",
        path="README.md",
        rolled_back=True,
    )

    inventory = build_defense_inventory(tmp_path)

    assert inventory["has_problems"] is False
    assert inventory["policy"]["source"] == SOURCE_INVARIANTS
    assert inventory["policy"]["checks"] == 2
    assert inventory["summary"]["rollbacks"] == 1
    assert inventory["summary"]["invariant_runs"] == 2
    assert inventory["journal_kinds"] == [KIND_INVARIANT, KIND_ROLLBACK]
    assert len(inventory["recent_events"]) == 2
    text = render_defense_inventory(inventory)
    assert "guard: invariants (enabled)" in text
    assert "README.md" in text
    assert "2 event(s)" in text


def test_inventory_reports_unsafe_command_and_empty_ensure(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "invariants:\n"
        "  ensure:\n"
        '    - ""\n'
        "  commands:\n"
        "    core:\n"
        "      - pytest -q && rm -rf build\n",
    )

    inventory = build_defense_inventory(tmp_path)

    assert inventory["has_problems"] is True
    kinds = {problem["kind"] for problem in inventory["problems"]}
    assert kinds == {"command", "ensure"}
    for problem in inventory["problems"]:
        assert problem["detail"]
        assert problem["remedy"]
    assert "problems (2)" in render_defense_inventory(inventory)


def test_inventory_reports_validation_fallback_and_disabled(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "validation:\n"
        "  commands:\n"
        "    lint:\n"
        "      - ./lint.sh\n",
    )
    fallback = build_defense_inventory(tmp_path)
    assert fallback["policy"]["source"] == SOURCE_VALIDATION_FALLBACK
    assert "falls back" in " ".join(fallback["policy"]["notes"])

    _write_config(tmp_path, "invariants: {}\n")
    disabled = build_defense_inventory(tmp_path)
    assert disabled["policy"]["source"] == SOURCE_DISABLED
    assert "disables guard checks on purpose" in " ".join(disabled["policy"]["notes"])
    assert disabled["summary"]["checks"] == 0


def test_inventory_reconstructs_latest_assumption_state(tmp_path: Path) -> None:
    from iterate_harness.defensive.kernel import DefensiveKernel

    kernel = DefensiveKernel(tmp_path)
    kernel.record_assumption("src/main.py exists", status="declared")
    kernel.record_assumption_checked("src/main.py exists", holds=False, detail="missing")
    kernel.record_assumption("tests run", status="declared")

    inventory = build_defense_inventory(tmp_path)

    rows = {row["statement"]: row for row in inventory["assumptions"]}
    assert rows["src/main.py exists"]["status"] == "falsified"
    assert rows["src/main.py exists"]["detail"] == "missing"
    assert rows["tests run"]["status"] == "declared"
    assert inventory["assumption_summary"] == {
        "total": 2,
        "declared": 1,
        "holds": 0,
        "falsified": 1,
    }


def test_config_load_failure_is_a_problem_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import iterate_harness.iterate.config_loader as config_loader

    def _boom(_root: object) -> object:
        raise RuntimeError("loader exploded")

    monkeypatch.setattr(config_loader, "load_effective_config", _boom)

    inventory = build_defense_inventory(tmp_path)

    assert inventory["has_problems"] is True
    assert inventory["problems"][0]["kind"] == "config"
    assert "cannot load the project config" in inventory["problems"][0]["detail"]
    assert "loader exploded" in inventory["problems"][0]["detail"]
    assert "ih iterate init" in inventory["problems"][0]["remedy"]


def test_load_effective_config_matches_resolver(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "validation:\n"
        "  commands:\n"
        "    lint:\n"
        "      - ./lint.sh\n",
    )
    resolved = resolve_invariants(load_effective_config(tmp_path))
    assert resolved.source == SOURCE_VALIDATION_FALLBACK
    assert resolved.commands == {"lint": ["./lint.sh"]}
