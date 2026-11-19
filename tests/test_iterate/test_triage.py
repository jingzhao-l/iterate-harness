"""Tests for console/CI findings triage (``ih iterate triage``).

The feature's promise is that an ignore decided at the console is *effective*:
the next review round's own filter (personalization + ``iterate.config.yaml``)
must drop the finding. These tests therefore assert the stores the engine
reads, not just the journal record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from iterate_harness import cli
from iterate_harness.iterate import personalization, triage
from iterate_harness.iterate.decision_log import append_entry, make_entry, read_entries
from iterate_harness.iterate.types import KnownIntentional
from iterate_harness.web import findings_triage


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep personalization out of the real home directory and chdir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_findings(root: Path, findings: list[dict[str, object]], *, round_number: int = 1) -> None:
    append_entry(
        root,
        make_entry(entry_type="review_result", round_number=round_number, data={"findings": findings}),
    )


def _finding(
    file: str = "src/a.py",
    *,
    line: int | None = 10,
    dimension: str = "security",
    severity: str = "high",
    summary: str = "hardcoded key",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "file": file,
        "dimension": dimension,
        "severity": severity,
        "summary": summary,
    }
    if line is not None:
        payload["line"] = line
    return payload


def _config(root: Path) -> dict[str, object]:
    return yaml.safe_load((root / "iterate.config.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Collection and status
# ---------------------------------------------------------------------------


def test_collect_findings_dedupes_and_keeps_latest_severity(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding(severity="high", summary="first")])
    append_entry(
        tmp_path,
        make_entry(
            entry_type="report",
            round_number=2,
            data={"findings": [_finding(severity="critical", summary="confirmed")]},
        ),
    )
    rows = triage.collect_findings(tmp_path)
    assert len(rows) == 1
    assert rows[0]["severity"] == "critical"
    assert rows[0]["summary"] == "confirmed"
    assert rows[0]["round"] == 2


def test_collect_findings_skips_entries_without_a_file(tmp_path: Path) -> None:
    _write_findings(tmp_path, [{"dimension": "security", "summary": "no path"}])
    assert triage.collect_findings(tmp_path) == []


def test_triage_report_joins_journal_and_engine_filter(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding(), _finding("src/b.py", line=4, dimension="perf")])
    findings_triage.record_decision(
        tmp_path, file="src/a.py", line=10, dimension="security", decision="approve"
    )
    personalization.save(
        None,
        tmp_path,
        personalization.PersonalizationData(
            known_intentional=[
                KnownIntentional(
                    file="src/b.py",
                    dimension="perf",
                    reason=f"{triage.IGNORE_REASON_PREFIX} generated",
                    line=4,
                )
            ]
        ),
    )
    rows = triage.triage_report(tmp_path)
    assert rows[0].status == triage.STATUS_APPROVED
    assert rows[1].status == triage.STATUS_IGNORED
    assert rows[1].effective is True


def test_report_marks_ignore_ineffective_when_the_filter_is_missing(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    findings_triage.record_decision(
        tmp_path, file="src/a.py", line=10, dimension="security", decision="ignore"
    )
    rows = triage.triage_report(tmp_path)
    assert rows[0].status == triage.STATUS_IGNORED
    assert rows[0].effective is False


# ---------------------------------------------------------------------------
# Ignore / restore
# ---------------------------------------------------------------------------


def test_ignore_writes_every_store_the_engine_reads(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    (tmp_path / "iterate.config.yaml").write_text("goal: test\n", encoding="utf-8")
    rows = triage.triage_report(tmp_path)

    result = triage.ignore_findings(tmp_path, rows, reason="generated code")

    assert len(result.added) == 1
    stored = personalization.known_intentional_of(None, tmp_path)
    assert stored == [
        KnownIntentional(
            file="src/a.py",
            dimension="security",
            reason=f"{triage.IGNORE_REASON_PREFIX} generated code",
            line=10,
        )
    ]
    config = _config(tmp_path)
    assert config["personalization"]["known_intentional"][0]["file"] == "src/a.py"
    assert findings_triage.load_decisions(tmp_path)["src/a.py:::10:::security"]["decision"] == "ignore"
    # Audit trail is in the same decision log the console already reads.
    assert any(
        entry.type == "decision" and entry.data.get("kind") == "triage"
        for entry in read_entries(tmp_path)
    )


def test_ignore_is_idempotent_but_still_records_the_new_decision(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    rows = triage.triage_report(tmp_path)
    triage.ignore_findings(tmp_path, rows, reason="first")
    second = triage.ignore_findings(tmp_path, rows, reason="second")
    assert second.added == []
    assert second.already == 1
    assert len(personalization.known_intentional_of(None, tmp_path)) == 1
    assert second.journaled == 1


def test_ignore_matching_a_whole_file_entry_adds_nothing(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    personalization.save(
        None,
        tmp_path,
        personalization.PersonalizationData(
            known_intentional=[KnownIntentional(file="src/a.py", dimension="security", reason="legacy")]
        ),
    )
    result = triage.ignore_findings(tmp_path, triage.triage_report(tmp_path), reason="dup")
    assert result.added == []
    assert result.already == 1


def test_ignore_dry_run_writes_nothing(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    result = triage.ignore_findings(tmp_path, triage.triage_report(tmp_path), reason="x", dry_run=True)
    assert result.added
    assert personalization.known_intentional_of(None, tmp_path) == []
    assert not (tmp_path / ".iterate" / findings_triage.TRIAGE_FILE).exists()
    assert not (tmp_path / "iterate.config.yaml").exists()


def test_restore_lifts_only_machine_added_entries(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    (tmp_path / "iterate.config.yaml").write_text("goal: test\n", encoding="utf-8")
    personalization.save(
        None,
        tmp_path,
        personalization.PersonalizationData(
            known_intentional=[
                KnownIntentional(
                    file="src/a.py",
                    dimension="security",
                    reason="hand written, keep me",
                    line=10,
                ),
                KnownIntentional(
                    file="src/other.py",
                    dimension="security",
                    reason=f"{triage.IGNORE_REASON_PREFIX} other file",
                    line=1,
                ),
            ]
        ),
    )
    personalization.sync_known_intentional_to_config(
        tmp_path, personalization.known_intentional_of(None, tmp_path)
    )

    result = triage.restore_findings(tmp_path, triage.triage_report(tmp_path))

    assert result.removed == 0
    remaining = personalization.known_intentional_of(None, tmp_path)
    assert [entry.file for entry in remaining] == ["src/a.py", "src/other.py"]
    # The hand-written config entry must survive the config cleanup too.
    config_known = _config(tmp_path)["personalization"]["known_intentional"]
    assert {entry["file"] for entry in config_known} == {"src/a.py", "src/other.py"}
    assert triage.triage_report(tmp_path)[0].effective is True


def test_restore_removes_machine_entry_config_bridge_and_journal(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    (tmp_path / "iterate.config.yaml").write_text("goal: test\n", encoding="utf-8")
    triage.ignore_findings(tmp_path, triage.triage_report(tmp_path), reason="generated")
    assert triage.triage_report(tmp_path)[0].effective is True

    result = triage.restore_findings(tmp_path, triage.triage_report(tmp_path))

    assert result.removed == 1
    assert personalization.known_intentional_of(None, tmp_path) == []
    assert "personalization" not in (_config(tmp_path) or {})
    assert findings_triage.load_decisions(tmp_path) == {}
    row = triage.triage_report(tmp_path)[0]
    assert row.status == triage.STATUS_OPEN
    assert row.effective is False


def test_clear_auto_ignores_preserves_hand_written_entries(tmp_path: Path) -> None:
    personalization.save(
        None,
        tmp_path,
        personalization.PersonalizationData(
            known_intentional=[
                KnownIntentional(file="a.py", dimension="d", reason=f"{triage.IGNORE_REASON_PREFIX} auto"),
                KnownIntentional(file="b.py", dimension="d", reason="manual"),
            ]
        ),
    )
    assert triage.clear_auto_ignores(tmp_path) == 1
    remaining = personalization.known_intentional_of(None, tmp_path)
    assert [entry.file for entry in remaining] == ["b.py"]


def test_ignore_rolls_back_the_filter_when_the_journal_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_findings(tmp_path, [_finding()])
    rows = triage.triage_report(tmp_path)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(findings_triage, "record_decision", boom)

    result = triage.ignore_findings(tmp_path, rows, reason="generated")

    assert result.applied is False
    assert result.journal_error and "disk full" in result.journal_error
    # Nothing is silently hidden: both the JSON store and the config bridge
    # must be back to their pre-call state.
    assert personalization.known_intentional_of(None, tmp_path) == []
    assert triage.triage_report(tmp_path)[0].status == triage.STATUS_OPEN


def test_restore_cleans_a_config_only_bridge_entry(tmp_path: Path) -> None:
    """A committed config entry must be lifted even if personalization is gone."""
    _write_findings(tmp_path, [_finding()])
    (tmp_path / "iterate.config.yaml").write_text(
        "goal: test\n"
        "personalization:\n"
        "  known_intentional:\n"
        "  - file: src/a.py\n"
        "    dimension: security\n"
        "    reason: 'triaged-intentional: generated'\n"
        "    line: 10\n",
        encoding="utf-8",
    )
    assert triage.triage_report(tmp_path)[0].effective is True

    result = triage.restore_findings(tmp_path, triage.triage_report(tmp_path))

    assert result.removed == 0
    assert result.config_removed == 1
    assert "personalization" not in (_config(tmp_path) or {})
    assert triage.triage_report(tmp_path)[0].effective is False


def test_ignore_one_keeps_a_zero_line_key(tmp_path: Path) -> None:
    """``line: 0`` is a real journal key and must not be widened to ``None``."""
    result = triage.ignore_one(
        tmp_path, file="src/a.py", line=0, dimension="security", reason="generated"
    )
    assert result.applied is True
    stored = personalization.known_intentional_of(None, tmp_path)
    assert [(entry.file, entry.dimension, entry.line) for entry in stored] == [
        ("src/a.py", "security", 0)
    ]
    assert findings_triage.load_decisions(tmp_path)["src/a.py:::0:::security"]["decision"] == "ignore"


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        ([], []),
        (["1"], [1]),
        (["1,3"], [1, 3]),
        (["2-4"], [2, 3, 4]),
        (["all"], [1, 2, 3, 4, 5]),
        (["all", "2"], [1, 2, 3, 4, 5]),
    ],
)
def test_parse_selectors_accepts_ranges_and_all(tokens: list[str], expected: list[int]) -> None:
    assert triage.parse_selectors(tokens, 5) == expected


@pytest.mark.parametrize("token", ["0", "6", "4-2", "x", "1-"])
def test_parse_selectors_rejects_bad_input(token: str) -> None:
    with pytest.raises(ValueError):
        triage.parse_selectors([token], 5)


def test_select_findings_is_empty_without_an_explicit_selection(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    rows = triage.triage_report(tmp_path)
    assert triage.select_findings(rows) == []


def test_select_findings_intersects_selectors_with_filters(tmp_path: Path) -> None:
    _write_findings(
        tmp_path,
        [_finding(severity="high"), _finding("src/b.py", dimension="perf", severity="low")],
    )
    rows = triage.triage_report(tmp_path)
    assert [row.index for row in triage.select_findings(rows, selectors=["1", "2"], severity="low")] == [2]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _invoke(*args: str):
    return CliRunner().invoke(cli.app, ["iterate", "triage", *args])


def test_cli_list_renders_and_json_is_machine_readable(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    result = _invoke("list")
    assert result.exit_code == 0, result.output
    assert "src/a.py:10" in result.output
    assert "open" in result.output

    payload = json.loads(_invoke("list", "--json").output)
    assert payload["total"] == 1
    assert payload["findings"][0]["key"] == "src/a.py:::10:::security"


def test_cli_list_with_no_findings_exits_zero(tmp_path: Path) -> None:
    result = _invoke("list")
    assert result.exit_code == 0, result.output
    assert "No findings" in result.output


def test_cli_ignore_then_restore_round_trip(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    (tmp_path / "iterate.config.yaml").write_text("goal: test\n", encoding="utf-8")

    ignored = _invoke("ignore", "1", "--reason", "generated")
    assert ignored.exit_code == 0, ignored.output
    assert "added 1" in ignored.output
    assert triage.triage_report(tmp_path)[0].effective is True

    restored = _invoke("restore", "1")
    assert restored.exit_code == 0, restored.output
    assert triage.triage_report(tmp_path)[0].effective is False


def test_cli_ignore_refuses_without_a_selection(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    result = _invoke("ignore")
    assert result.exit_code == 1
    assert "Nothing selected" in result.output
    assert personalization.known_intentional_of(None, tmp_path) == []


def test_cli_ignore_rejects_an_out_of_range_selector(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    result = _invoke("ignore", "9")
    assert result.exit_code == 1
    assert "out of range" in result.output


def test_cli_ignore_can_select_by_severity(tmp_path: Path) -> None:
    _write_findings(
        tmp_path,
        [_finding(severity="high"), _finding("src/b.py", dimension="perf", severity="low")],
    )
    result = _invoke("ignore", "--severity", "low")
    assert result.exit_code == 0, result.output
    assert personalization.known_intentional_of(None, tmp_path)[0].file == "src/b.py"


def test_cli_list_rejects_an_unknown_status(tmp_path: Path) -> None:
    _write_findings(tmp_path, [_finding()])
    result = _invoke("list", "--status", "bogus")
    assert result.exit_code != 0
    assert "unknown status" in result.output
