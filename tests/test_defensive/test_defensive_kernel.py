"""Unit tests for the code-mode defensive kernel.

Covers the three mechanical guarantees (design §20.3.2):

- atomic mutations via :class:`FileTransactionBuffer` (snapshot → rollback)
- invariant guarding via :func:`check_invariants` (ensure assertions,
  exact-match command lists, fail-closed metachar refusal)
- :class:`DefensiveKernel.after_mutation` orchestrating snapshot → post-check
  → commit / rollback, plus the assumption audit trail.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iterate_harness.defensive.invariants import (
    COMMAND_METACHARS,
    check_invariants,
    command_is_safe,
)
from iterate_harness.defensive.kernel import DefensiveKernel
from iterate_harness.defensive.transaction import FileTransactionBuffer


# ---------------------------------------------------------------------------
# FileTransactionBuffer — atomic snapshots and rollback
# ---------------------------------------------------------------------------


def test_snapshot_then_rollback_restores_original(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("original", encoding="utf-8")
    buffer = FileTransactionBuffer(tmp_path)

    buffer.snapshot(target)
    target.write_text("mutated", encoding="utf-8")

    restored = buffer.rollback()
    assert restored == [target]
    assert target.read_text(encoding="utf-8") == "original"
    assert buffer.pending == []


def test_snapshot_of_missing_file_rolls_back_to_deletion(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    buffer = FileTransactionBuffer(tmp_path)

    buffer.snapshot(target)
    target.write_text("created", encoding="utf-8")

    restored = buffer.rollback()
    assert target in restored
    assert not target.exists()


def test_double_snapshot_keeps_first_capture(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("v1", encoding="utf-8")
    buffer = FileTransactionBuffer(tmp_path)

    buffer.snapshot(target)
    target.write_text("v2", encoding="utf-8")
    buffer.snapshot(target)  # second snapshot is a no-op
    target.write_text("v3", encoding="utf-8")

    buffer.rollback()
    assert target.read_text(encoding="utf-8") == "v1"


def test_commit_drops_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("v1", encoding="utf-8")
    buffer = FileTransactionBuffer(tmp_path)

    buffer.snapshot(target)
    buffer.commit(target)

    assert buffer.pending == []
    buffer.rollback()
    # Commit means "accepted": rollback must NOT restore the pre-commit bytes.
    assert target.read_text(encoding="utf-8") == "v1"


def test_snapshot_resolves_relative_path_under_root(tmp_path: Path) -> None:
    buffer = FileTransactionBuffer(tmp_path)
    target = tmp_path / "sub" / "b.txt"
    target.parent.mkdir()
    target.write_text("keep", encoding="utf-8")

    buffer.snapshot("sub/b.txt")
    target.write_text("change", encoding="utf-8")
    buffer.rollback()

    assert target.read_text(encoding="utf-8") == "keep"


# ---------------------------------------------------------------------------
# check_invariants — ensure assertions, exact-match commands, fail-closed
# ---------------------------------------------------------------------------


def test_invariant_ensure_passes_when_all_files_exist(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")

    report = check_invariants(tmp_path, ensure=["pyproject.toml", "src/main.py"])

    assert report.passed
    assert report.checks_run == 2
    assert report.violations == []


def test_invariant_ensure_reports_missing_file(tmp_path: Path) -> None:
    report = check_invariants(tmp_path, ensure=["missing.py"])

    assert not report.passed
    assert report.violations[0].kind == "ensure"
    assert "missing.py" in report.violations[0].detail


def test_invariant_ensure_distinguishes_directory_from_file(tmp_path: Path) -> None:
    (tmp_path / "somedir").mkdir()

    report = check_invariants(tmp_path, ensure=["somedir"])

    assert not report.passed
    assert "expected a file" in report.violations[0].detail


def test_invariant_command_runs_on_exact_match(tmp_path: Path) -> None:
    script = tmp_path / "ok.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)

    report = check_invariants(tmp_path, commands={"syntax": ["./ok.sh"]})

    assert report.passed
    assert report.checks_run == 1


def test_invariant_command_exit_code_failure(tmp_path: Path) -> None:
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)

    report = check_invariants(tmp_path, commands={"syntax": ["./fail.sh"]})

    assert not report.passed
    violation = report.violations[0]
    assert violation.kind == "command"
    assert "exit 3" in violation.detail


def test_invariant_metachar_command_refused_without_execution(tmp_path: Path) -> None:
    report = check_invariants(tmp_path, commands={"evil": ["pytest; rm -rf /"]})

    assert not report.passed
    assert report.violations[0].kind == "refused"


def test_command_is_safe_rejects_metacharacters() -> None:
    assert command_is_safe("pytest -q")
    assert command_is_safe("make test")
    assert not command_is_safe("pytest && echo hi")
    assert not command_is_safe("pytest | grep x")
    assert not command_is_safe("")
    assert not command_is_safe("   ")
    for ch in COMMAND_METACHARS:
        assert not command_is_safe(f"echo{ch}")


def test_dry_run_skips_execution(tmp_path: Path) -> None:
    script = tmp_path / "no.sh"
    script.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    script.chmod(0o755)

    report = check_invariants(tmp_path, commands={"x": ["./no.sh"]}, dry_run=True)

    assert report.passed
    assert report.checks_run == 1


# ---------------------------------------------------------------------------
# DefensiveKernel — snapshot → post-check → commit / rollback
# ---------------------------------------------------------------------------


def _kernel_with_commands(tmp_path: Path, commands: dict[str, list[str]]) -> DefensiveKernel:
    from iterate_harness.iterate.config_loader import EffectiveConfig
    from iterate_harness.iterate.types import IterateConfig, InvariantConfig

    config = IterateConfig(invariants=InvariantConfig(commands=commands))
    effective = EffectiveConfig(config=config, source="override", override={"invariants": {"commands": commands}})
    return DefensiveKernel(tmp_path, effective)


@pytest.mark.asyncio
async def test_after_mutation_rolls_back_on_invariant_violation(tmp_path: Path) -> None:
    script = tmp_path / "check.sh"
    script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    kernel = _kernel_with_commands(tmp_path, {"syntax": ["./check.sh"]})

    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")
    kernel.snapshot(target)
    target.write_text("v2", encoding="utf-8")

    reason = await kernel.after_mutation("write_file", target, success=True)

    assert reason is not None
    assert "invariant violated" in reason
    assert target.read_text(encoding="utf-8") == "v1"  # rolled back
    assert kernel.pending_mutations == []


@pytest.mark.asyncio
async def test_after_mutation_commits_when_invariants_pass(tmp_path: Path) -> None:
    script = tmp_path / "ok.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    kernel = _kernel_with_commands(tmp_path, {"syntax": ["./ok.sh"]})

    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")
    kernel.snapshot(target)
    target.write_text("v2", encoding="utf-8")

    reason = await kernel.after_mutation("write_file", target, success=True)

    assert reason is None
    assert target.read_text(encoding="utf-8") == "v2"  # committed, not rolled back
    assert kernel.pending_mutations == []


@pytest.mark.asyncio
async def test_after_mutation_rolls_back_on_failed_tool(tmp_path: Path) -> None:
    kernel = _kernel_with_commands(tmp_path, {})

    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")
    kernel.snapshot(target)
    target.write_text("v2", encoding="utf-8")

    reason = await kernel.after_mutation(
        "write_file", target, success=False, error_hint="disk full"
    )

    assert reason is not None
    assert "failed" in reason
    assert target.read_text(encoding="utf-8") == "v1"


@pytest.mark.asyncio
async def test_after_mutation_without_configured_invariants_commits(tmp_path: Path) -> None:
    kernel = DefensiveKernel(tmp_path)  # no effective config → no invariants

    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")
    kernel.snapshot(target)
    target.write_text("v2", encoding="utf-8")

    reason = await kernel.after_mutation("write_file", target, success=True)

    assert reason is None
    assert target.read_text(encoding="utf-8") == "v2"


@pytest.mark.asyncio
async def test_kernel_resolves_invariants_from_disk_config(tmp_path: Path) -> None:
    """Regression: load_effective_config must parse the ``invariants`` section.

    Before the fix ``config_from_dict`` dropped the section, so a project with
    a configured ``invariants`` block got its code-mode edits silently committed
    without any invariant check (design §20.3.2 guarantee bypassed).
    """
    from iterate_harness.iterate.config_loader import load_effective_config

    (tmp_path / "iterate.config.yaml").write_text(
        "invariants:\n"
        "  ensure:\n"
        "    - pyproject.toml\n",
        encoding="utf-8",
    )
    kernel = DefensiveKernel(tmp_path, load_effective_config(tmp_path))
    assert kernel.invariants_configured is True
    assert kernel.last_report is None

    # An edit that removes the guaranteed file must be rolled back.
    target = tmp_path / "pyproject.toml"
    target.write_text("v1", encoding="utf-8")
    kernel.snapshot(target)
    target.unlink()

    reason = await kernel.after_mutation("write_file", target, success=True)

    assert reason is not None
    assert "invariant violated" in reason
    assert target.exists()  # restored by the rollback
    assert target.read_text(encoding="utf-8") == "v1"


@pytest.mark.asyncio
async def test_disabled_kernel_is_noop(tmp_path: Path) -> None:
    kernel = DefensiveKernel(tmp_path, enabled=False)

    target = tmp_path / "a.py"
    target.write_text("v1", encoding="utf-8")
    kernel.snapshot(target)
    target.write_text("v2", encoding="utf-8")

    reason = await kernel.after_mutation("write_file", target, success=True)

    assert reason is None
    assert target.read_text(encoding="utf-8") == "v2"
    assert not kernel.enabled


def test_kernel_records_assumption_to_decision_log(tmp_path: Path) -> None:
    kernel = DefensiveKernel(tmp_path)

    kernel.record_assumption("src/main.py exists", status="declared", detail="seen in glob")

    log_file = tmp_path / ".iterate" / "decision-log.jsonl"
    assert log_file.is_file()
    content = log_file.read_text(encoding="utf-8")
    assert "assumption_declared" in content
    assert "src/main.py exists" in content


def test_kernel_records_assumption_checked(tmp_path: Path) -> None:
    kernel = DefensiveKernel(tmp_path)

    kernel.record_assumption_checked("dependency installed", holds=False, detail="pip list missing")

    log_file = tmp_path / ".iterate" / "decision-log.jsonl"
    content = log_file.read_text(encoding="utf-8")
    assert "assumption_checked" in content
    assert "falsified" in content


def test_kernel_to_metadata_exposes_state(tmp_path: Path) -> None:
    kernel = DefensiveKernel(tmp_path)
    meta = kernel.to_metadata()

    assert meta["enabled"] is True
    assert meta["invariants_configured"] is False
    assert meta["pending_mutations"] == []
    assert meta["last_invariant_passed"] is None


@pytest.mark.asyncio
async def test_invariants_run_off_the_event_loop(tmp_path: Path) -> None:
    script = tmp_path / "slow.sh"
    script.write_text("#!/bin/sh\nsleep 0.2\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    kernel = _kernel_with_commands(tmp_path, {"slow": ["./slow.sh"]})

    async def _tick() -> int:
        await asyncio.sleep(0)
        return 42

    report_task = asyncio.create_task(kernel.check_invariants())
    tick = await _tick()

    assert tick == 42  # event loop stayed responsive while invariants ran
    report = await report_task
    assert report.passed
