"""CLI tests for ``ih defense show|journal|check`` (design §17.3 P17).

The CLI is the operator's path when the console is not open and the CI path
when an invariant gate must be scripted. These tests pin the exit-code
contract (0 pass / 1 fail / 2 nothing configured), the dry-run no-record
promise, and that a real manual check leaves the same journal event the
console reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iterate_harness.cli import app
from iterate_harness.defensive.journal import (
    KIND_MANUAL_CHECK,
    KIND_ROLLBACK,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    list_defense_events,
    record_defense_event,
)


def _write_config(project: Path, body: str) -> None:
    (project / "iterate.config.yaml").write_text(body, encoding="utf-8")


def _invoke(*args: str):
    return CliRunner().invoke(app, list(args))


def test_show_json_resolves_the_guard(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    _write_config(
        tmp_path,
        "invariants:\n  ensure:\n    - README.md\n",
    )

    result = _invoke("defense", "show", "--project-root", str(tmp_path), "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["policy"]["source"] == "invariants"
    assert payload["policy"]["checks"] == 1
    assert payload["has_problems"] is False


def test_show_strict_fails_on_a_dead_entry(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "invariants:\n"
        "  commands:\n"
        "    core:\n"
        "      - pytest -q && rm -rf build\n",
    )
    result = _invoke("defense", "show", "--project-root", str(tmp_path), "--strict")
    assert result.exit_code == 1
    assert "problems (1)" in result.output
    assert "metacharacters" in result.output


def test_journal_empty_and_filters(tmp_path: Path) -> None:
    empty = _invoke("defense", "journal", "--project-root", str(tmp_path))
    assert empty.exit_code == 0
    assert "No defensive-kernel events" in empty.output

    record_defense_event(tmp_path, kind=KIND_ROLLBACK, outcome=OUTCOME_FAIL, tool="write_file")
    record_defense_event(tmp_path, kind=KIND_MANUAL_CHECK, outcome=OUTCOME_PASS, tool="ih defense check")

    filtered = _invoke(
        "defense", "journal", "--project-root", str(tmp_path), "--kind", "rollback"
    )
    assert filtered.exit_code == 0
    assert "Showing 1 of 1" in filtered.output

    invalid = _invoke(
        "defense", "journal", "--project-root", str(tmp_path), "--kind", "bogus"
    )
    assert invalid.exit_code == 1
    assert "invalid --kind" in invalid.output

    invalid_outcome = _invoke(
        "defense", "journal", "--project-root", str(tmp_path), "--outcome", "bogus"
    )
    assert invalid_outcome.exit_code == 1
    assert "invalid --outcome" in invalid_outcome.output


def test_journal_format_and_out(tmp_path: Path) -> None:
    record_defense_event(
        tmp_path,
        kind=KIND_ROLLBACK,
        outcome=OUTCOME_FAIL,
        tool="write_file",
        path="a.py",
        reason="boom",
    )
    target = tmp_path / "journal.csv"
    result = _invoke(
        "defense",
        "journal",
        "--project-root",
        str(tmp_path),
        "--format",
        "csv",
        "--out",
        str(target),
    )
    assert result.exit_code == 0
    assert "Wrote 1 defense events" in result.output
    assert "write_file" in target.read_text(encoding="utf-8")

    markdown = _invoke(
        "defense", "journal", "--project-root", str(tmp_path), "--format", "markdown"
    )
    assert markdown.exit_code == 0
    assert "# iterate-harness defensive kernel events" in markdown.output


def test_check_passes_and_records_manual_event(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    _write_config(tmp_path, "invariants:\n  ensure:\n    - README.md\n")

    result = _invoke("defense", "check", "--project-root", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert "[ok  ] ensure:README.md" in result.output
    page, total = list_defense_events(tmp_path)
    assert total == 1
    assert page[0]["kind"] == KIND_MANUAL_CHECK
    assert page[0]["outcome"] == OUTCOME_PASS
    assert page[0]["checks_run"] == 1


def test_check_fails_and_records_failure(tmp_path: Path) -> None:
    _write_config(tmp_path, "invariants:\n  ensure:\n    - missing.py\n")

    result = _invoke("defense", "check", "--project-root", str(tmp_path))

    assert result.exit_code == 1, result.output
    assert "[FAIL] ensure:missing.py" in result.output
    page, total = list_defense_events(tmp_path)
    assert total == 1
    assert page[0]["kind"] == KIND_MANUAL_CHECK
    assert page[0]["outcome"] == OUTCOME_FAIL
    assert page[0]["violations"][0]["label"] == "ensure:missing.py"


def test_check_dry_run_never_records(tmp_path: Path) -> None:
    _write_config(tmp_path, "invariants:\n  ensure:\n    - missing.py\n")

    result = _invoke(
        "defense", "check", "--project-root", str(tmp_path), "--dry-run", "--json"
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["outcome"] == OUTCOME_FAIL
    assert list_defense_events(tmp_path) == ([], 0)


def test_check_without_guard_exits_two_and_records_unmeasured(tmp_path: Path) -> None:
    result = _invoke("defense", "check", "--project-root", str(tmp_path))

    assert result.exit_code == 2, result.output
    assert "not configured" in result.output
    page, total = list_defense_events(tmp_path)
    assert total == 1
    assert page[0]["kind"] == KIND_MANUAL_CHECK
    assert page[0]["outcome"] == "unmeasured"
    assert page[0]["checks_run"] == 0


def test_check_reports_a_broken_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import iterate_harness.iterate.config_loader as config_loader

    def _boom(_root: object) -> object:
        raise RuntimeError("loader exploded")

    monkeypatch.setattr(config_loader, "load_effective_config", _boom)

    result = _invoke("defense", "check", "--project-root", str(tmp_path))

    assert result.exit_code == 1
    assert "cannot load the project config" in result.output
    assert "loader exploded" in result.output
    assert list_defense_events(tmp_path) == ([], 0)


@pytest.mark.parametrize("fmt", ["markdown", "json"])
def test_check_render_formats(tmp_path: Path, fmt: str) -> None:
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    _write_config(tmp_path, "invariants:\n  ensure:\n    - README.md\n")

    result = _invoke(
        "defense", "check", "--project-root", str(tmp_path), "--format", fmt, "--dry-run"
    )

    assert result.exit_code == 0, result.output
    if fmt == "json":
        assert json.loads(result.output)["checks_configured"] == 1
    else:
        assert "# iterate-harness defensive check" in result.output
