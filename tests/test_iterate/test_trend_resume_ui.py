"""Tests for 0.5.0 features: trend library, last-run resume, pause select menu.

Covers:
- :mod:`iterate_harness.iterate.trend_store` — fingerprint stability, the
  new/fixed/regressed/stubborn lifecycle, corrupt-library reset, summary
  rendering;
- :mod:`iterate_harness.iterate.last_state` — resume summary extraction from
  the decision log (severity buckets, preview, last intervention);
- ``/iterate resume`` and ``/iterate trend`` slash commands;
- the directional-key pause menu channel in
  :func:`iterate_harness.engine.query._handle_iterate_pause`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iterate_harness.commands.iterate import iterate_command_handler
from iterate_harness.commands.registry import CommandContext
from iterate_harness.config.settings import PermissionSettings
from iterate_harness.engine.query import (
    QueryContext,
    _handle_iterate_pause,
)
from iterate_harness.iterate import trend_store
from iterate_harness.iterate.decision_log import append_entry, make_entry, read_entries
from iterate_harness.iterate.last_state import summarize_last_run
from iterate_harness.permissions import PermissionChecker
from iterate_harness.tools import ToolRegistry


def make_context(cwd: Path) -> CommandContext:
    return CommandContext(engine=None, cwd=str(cwd))  # type: ignore[arg-type]


def finding(file: str = "src/a.py", line: int = 10, dimension: str = "security") -> dict:
    return {
        "file": file,
        "line": line,
        "dimension": dimension,
        "severity": "high",
        "summary": "sql injection risk",
    }


# -- trend_store --------------------------------------------------------------


def test_fingerprint_is_stable_and_line_sensitive():
    a = trend_store.finding_fingerprint(finding())
    b = trend_store.finding_fingerprint(finding())
    c = trend_store.finding_fingerprint(finding(line=11))
    d = trend_store.finding_fingerprint(finding(dimension="correctness"))
    assert a == b
    assert a != c
    assert a != d


def test_fingerprint_none_for_untrackable_findings():
    assert trend_store.finding_fingerprint({"file": "", "dimension": "security"}) is None
    assert trend_store.finding_fingerprint({"file": "a.py", "dimension": ""}) is None
    assert trend_store.finding_fingerprint({"file": "a.py"}) is None


def test_record_run_marks_new_findings_and_persists(tmp_path):
    delta = trend_store.record_run(tmp_path, [finding()], run_timestamp="2026-01-01T00:00:00+00:00")
    assert delta.new_findings and delta.new_findings[0]["file"] == "src/a.py"
    assert delta.fixed_findings == []
    library = trend_store.load_library(tmp_path)
    assert len(library) == 1
    record = next(iter(library.values()))
    assert record["status"] == trend_store.STATUS_OPEN
    assert record["first_seen"] == "2026-01-01T00:00:00+00:00"


def test_absent_finding_becomes_fixed_then_regressed(tmp_path):
    stamp = "2026-01-0%dT00:00:00+00:00"
    trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % 1)
    delta2 = trend_store.record_run(tmp_path, [], run_timestamp=stamp % 2)
    assert delta2.fixed_findings and delta2.fixed_findings[0]["status"] == trend_store.STATUS_FIXED
    delta3 = trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % 3)
    assert delta3.regressed_findings and delta3.regressed_findings[0]["status"] == trend_store.STATUS_OPEN


def test_stubborn_after_three_open_runs(tmp_path):
    stamp = "2026-01-0%dT00:00:00+00:00"
    for day in (1, 2):
        delta = trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % day)
        assert delta.stubborn_findings == []
    delta3 = trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % 3)
    assert delta3.stubborn_findings and delta3.stubborn_findings[0]["runs"] == 3


def test_first_seen_persists_across_runs(tmp_path):
    stamp = "2026-01-0%dT00:00:00+00:00"
    trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % 1)
    trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % 2)
    library = trend_store.load_library(tmp_path)
    record = next(iter(library.values()))
    assert record["first_seen"] == stamp % 1
    assert record["last_seen"] == stamp % 2


def test_fixed_at_persists_in_library(tmp_path):
    stamp = "2026-01-0%dT00:00:00+00:00"
    trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % 1)
    trend_store.record_run(tmp_path, [], run_timestamp=stamp % 2)
    library = trend_store.load_library(tmp_path)
    record = next(iter(library.values()))
    assert record["status"] == trend_store.STATUS_FIXED
    assert record["fixed_at"] == stamp % 2


def test_malformed_findings_are_skipped(tmp_path):
    delta = trend_store.record_run(
        tmp_path,
        ["not-a-dict", {"file": "", "dimension": "x"}, finding()],  # type: ignore[list-item]
        run_timestamp="2026-01-01T00:00:00+00:00",
    )
    assert len(delta.new_findings) == 1


def test_corrupt_library_resets_to_empty(tmp_path):
    target = trend_store.library_path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")
    assert trend_store.load_library(tmp_path) == {}
    delta = trend_store.record_run(tmp_path, [finding()], run_timestamp="2026-01-01T00:00:00+00:00")
    assert len(delta.new_findings) == 1


def test_summarize_and_render_trend_summary(tmp_path):
    stamp = "2026-01-0%dT00:00:00+00:00"
    trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % 1)
    trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % 2)
    trend_store.record_run(tmp_path, [finding()], run_timestamp=stamp % 3)
    summary = trend_store.summarize(tmp_path)
    assert summary["trackedFindings"] == 1
    assert summary["open"] == 1
    assert summary["stubborn"] == 1
    rendered = trend_store.render_trend_summary(summary)
    assert "1 tracked finding(s)" in rendered
    assert "stubborn findings (open for 3+ runs):" in rendered
    assert "src/a.py:10 security" in rendered


def test_render_summary_without_stubborn_is_friendly(tmp_path):
    rendered = trend_store.render_trend_summary(trend_store.summarize(tmp_path))
    assert "no stubborn findings" in rendered


# -- diff_runs / render_diff -------------------------------------------------


def _diff_finding(file: str = "src/a.py", line: int = 10) -> dict:
    return {
        "file": file,
        "line": line,
        "dimension": "security",
        "severity": "high",
        "summary": "sql injection risk",
    }


def test_diff_runs_classifies_new_fixed_unchanged():
    run_a = [_diff_finding("a.py", 1), _diff_finding("b.py", 2)]
    run_b = [_diff_finding("b.py", 2), _diff_finding("c.py", 3)]
    diff = trend_store.diff_runs(run_a, run_b)
    assert len(diff.new) == 1 and diff.new[0]["file"] == "c.py"
    assert len(diff.fixed) == 1 and diff.fixed[0]["file"] == "a.py"
    assert len(diff.unchanged) == 1 and diff.unchanged[0]["file"] == "b.py"
    assert diff.regressed == []


def test_diff_runs_regressed_from_previously_fixed():
    # A finding that was fixed in an earlier run and reappears is classified
    # as regressed (not new / unchanged) when its fixed snapshot is supplied.
    previous = [_diff_finding("a.py", 1), _diff_finding("b.py", 2)]
    current = [_diff_finding("a.py", 1), _diff_finding("b.py", 2)]
    diff = trend_store.diff_runs(
        previous, current, previously_fixed=[_diff_finding("b.py", 2)]
    )
    assert len(diff.regressed) == 1 and diff.regressed[0]["file"] == "b.py"
    assert len(diff.unchanged) == 1 and diff.unchanged[0]["file"] == "a.py"
    assert diff.new == []
    assert diff.fixed == []


def test_diff_runs_skips_untrackable_findings():
    untrackable = {"file": "", "dimension": ""}
    trackable = _diff_finding("a.py", 1)
    diff = trend_store.diff_runs([untrackable], [untrackable, trackable])
    assert len(diff.new) == 1 and diff.new[0]["file"] == "a.py"
    assert diff.fixed == []
    assert diff.unchanged == []
    assert diff.regressed == []


def test_diff_runs_empty_runs():
    assert trend_store.diff_runs([], []) == trend_store.RunDiff([], [], [], [])
    diff = trend_store.diff_runs([], [_diff_finding("a.py", 1)])
    assert len(diff.new) == 1
    diff2 = trend_store.diff_runs([_diff_finding("a.py", 1)], [])
    assert len(diff2.fixed) == 1


def test_diff_runs_consecutive_runs_have_no_regressions():
    # Without a previously-fixed snapshot, a consecutive-run comparison never
    # reports regressions: the fixed set A - B cannot reappear in B.
    diff = trend_store.diff_runs([_diff_finding("a.py", 1)], [_diff_finding("a.py", 1)])
    assert diff.regressed == []
    assert len(diff.unchanged) == 1
    assert diff.new == []
    assert diff.fixed == []


def test_diff_runs_line_sensitive_classification():
    run_a = [_diff_finding("a.py", 10)]
    run_b = [_diff_finding("a.py", 11)]
    diff = trend_store.diff_runs(run_a, run_b)
    assert len(diff.fixed) == 1  # line 10 disappeared
    assert len(diff.new) == 1  # line 11 appeared
    assert diff.unchanged == []
    assert diff.regressed == []


def test_diff_runs_regressed_removed_from_new():
    # A regressed finding must never also be classified as new.
    previous = [_diff_finding("b.py", 2)]
    current = [_diff_finding("b.py", 2), _diff_finding("c.py", 3)]
    diff = trend_store.diff_runs(
        previous, current, previously_fixed=[_diff_finding("b.py", 2)]
    )
    assert len(diff.regressed) == 1 and diff.regressed[0]["file"] == "b.py"
    assert len(diff.new) == 1 and diff.new[0]["file"] == "c.py"
    assert diff.fixed == []
    assert diff.unchanged == []


def test_render_diff_shows_counts_and_sections():
    diff = trend_store.RunDiff(
        new=[_diff_finding("c.py", 3)],
        fixed=[_diff_finding("a.py", 1)],
        regressed=[_diff_finding("b.py", 2)],
        unchanged=[_diff_finding("d.py", 4)],
    )
    rendered = trend_store.render_diff(diff)
    assert "1 new, 1 fixed, 1 regressed, 1 unchanged" in rendered
    assert "New findings (1):" in rendered
    assert "Fixed (1):" in rendered
    assert "Regressed (1):" in rendered
    assert "c.py" in rendered and "a.py" in rendered and "b.py" in rendered


def test_render_diff_empty_diff():
    rendered = trend_store.render_diff(trend_store.RunDiff([], [], [], []))
    assert "0 new, 0 fixed, 0 regressed, 0 unchanged" in rendered


# -- last_state ---------------------------------------------------------------


def report_entry(round_number: int = 2, findings: list[dict] | None = None):
    return make_entry(
        entry_type="report",
        round_number=round_number,
        data={
            "mode": "dry-run",
            "verdict": "architectural",
            "findings": findings if findings is not None else [finding(), finding(line=20)],
        },
    )


def test_summarize_last_run_none_without_history(tmp_path):
    assert summarize_last_run(str(tmp_path)) is None
    append_entry(tmp_path, make_entry(entry_type="round_start", round_number=1))
    assert summarize_last_run(str(tmp_path)) is None  # no report entry yet


def test_summarize_last_run_extracts_fields(tmp_path):
    append_entry(tmp_path, make_entry(entry_type="round_start", round_number=1))
    append_entry(
        tmp_path,
        make_entry(
            entry_type="decision",
            round_number=1,
            data={"kind": "intervention", "action": "skip", "detail": "user pressed s"},
        ),
    )
    append_entry(tmp_path, report_entry(round_number=2))

    summary = summarize_last_run(str(tmp_path))
    assert summary is not None
    assert summary["mode"] == "dry-run"
    assert summary["verdict"] == "architectural"
    assert summary["rounds"] == 2
    assert summary["totalFindings"] == 2
    assert summary["severity"]["high"] == 2
    assert summary["severity"]["critical"] == 0
    assert len(summary["preview"]) == 2
    assert summary["preview"][0]["file"] == "src/a.py"
    assert summary["lastIntervention"]["action"] == "skip"
    assert summary["lastIntervention"]["round"] == 1


def test_summarize_last_run_unknown_severity_ignored(tmp_path):
    odd = finding()
    odd["severity"] = "catastrophic"
    append_entry(tmp_path, report_entry(findings=[odd]))
    summary = summarize_last_run(str(tmp_path))
    assert summary is not None
    assert summary["severity"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}


# -- /iterate resume and /iterate trend ---------------------------------------


@pytest.mark.asyncio
async def test_iterate_trend_renders_library(tmp_path):
    trend_store.record_run(tmp_path, [finding()], run_timestamp="2026-01-01T00:00:00+00:00")
    result = await iterate_command_handler("trend", make_context(tmp_path))
    assert "1 tracked finding(s)" in (result.message or "")


@pytest.mark.asyncio
async def test_iterate_log_trend_alias(tmp_path):
    trend_store.record_run(tmp_path, [finding()], run_timestamp="2026-01-01T00:00:00+00:00")
    result = await iterate_command_handler("log trend", make_context(tmp_path))
    assert "1 tracked finding(s)" in (result.message or "")


@pytest.mark.asyncio
async def test_iterate_resume_without_history_is_friendly(tmp_path):
    result = await iterate_command_handler("resume", make_context(tmp_path))
    assert result.submit_prompt is None
    assert "No iterate run to resume" in (result.message or "")


@pytest.mark.asyncio
async def test_iterate_resume_interrupted_uses_checkpoint(tmp_path):
    from iterate_harness.iterate.checkpoint import save_checkpoint

    append_entry(
        tmp_path,
        make_entry(
            entry_type="review_result",
            round_number=1,
            data={"findings": [finding()]},
        ),
    )
    save_checkpoint(
        tmp_path,
        round=2,
        new_findings=0,
        total_findings=1,
        per_dimension={"security": 1},
        converged=False,
        input_tokens=500,
        output_tokens=1500,
        cost_usd=0.03,
        mode="dry-run",
    )

    result = await iterate_command_handler("resume", make_context(tmp_path))
    assert result.submit_prompt is not None
    assert "Resume the interrupted iterate run" in result.submit_prompt
    assert "per-dimension breakdown" in result.submit_prompt
    assert "Resuming interrupted iterate run" in (result.message or "")
    assert "last converged checkpoint at round 2" in (result.message or "")


@pytest.mark.asyncio
async def test_iterate_resume_submits_resume_kickoff(tmp_path):
    append_entry(tmp_path, report_entry(round_number=2))
    result = await iterate_command_handler("resume", make_context(tmp_path))
    assert result.submit_prompt is not None
    assert "Resume the last iterate run" in result.submit_prompt
    assert 'verdict "architectural"' in result.submit_prompt
    assert "src/a.py" in result.submit_prompt
    assert "Resuming last iterate run (dry-run" in (result.message or "")


# -- pause menu over the select channel ----------------------------------------


def _pause_context(
    tmp_path: Path,
    select,
    prompt=None,
) -> QueryContext:
    return QueryContext(
        api_client=object(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        max_tokens=1,
        max_turns=1,
        ask_user_prompt=prompt,
        ask_user_select=select,
    )


class _Progress:
    round = 3
    new_findings = 2


def _last_intervention(tmp_path: Path) -> dict:
    entries = read_entries(tmp_path)
    for entry in reversed(entries):
        if entry.type == "decision" and isinstance(entry.data, dict) and entry.data.get("kind") == "intervention":
            return entry.data
    return {}


@pytest.mark.asyncio
async def test_pause_select_resume_keeps_loop(tmp_path):
    async def select(title, options):
        assert "round 3" in title
        assert options[0]["value"] == "resume"  # safe default first
        return "resume"

    context = _pause_context(tmp_path, select)
    action, message = await _handle_iterate_pause(context, _Progress())
    assert action == "resume"
    assert message is None
    assert _last_intervention(tmp_path)["action"] == "resume"


@pytest.mark.asyncio
async def test_pause_select_stop_halts_loop(tmp_path):
    async def select(title, options):
        return "stop"

    context = _pause_context(tmp_path, select)
    action, message = await _handle_iterate_pause(context, _Progress())
    assert action == "stop"
    assert message is None
    assert _last_intervention(tmp_path)["action"] == "stop"


@pytest.mark.asyncio
async def test_pause_select_skip_injects_instruction(tmp_path):
    async def select(title, options):
        return "skip"

    context = _pause_context(tmp_path, select)
    action, message = await _handle_iterate_pause(context, _Progress())
    assert action == "skip"
    assert message is not None and "SKIP the current top finding" in message


@pytest.mark.asyncio
async def test_pause_select_narrow_asks_dimensions(tmp_path):
    async def select(title, options):
        return "narrow"

    async def prompt(question):
        assert "dimensions" in question.lower()
        return "security, correctness"

    context = _pause_context(tmp_path, select, prompt)
    action, message = await _handle_iterate_pause(context, _Progress())
    assert action == "narrow"
    assert message is not None and "security, correctness" in message
    assert _last_intervention(tmp_path)["action"] == "narrow"


@pytest.mark.asyncio
async def test_pause_select_narrow_empty_degrades_to_resume(tmp_path):
    async def select(title, options):
        return "narrow"

    async def prompt(question):
        return "   "

    context = _pause_context(tmp_path, select, prompt)
    action, message = await _handle_iterate_pause(context, _Progress())
    assert action == "resume"
    assert message is None
    assert _last_intervention(tmp_path)["detail"] == "narrow without dimensions"


@pytest.mark.asyncio
async def test_pause_select_error_stops_safely(tmp_path):
    async def select(title, options):
        raise RuntimeError("frontend gone")

    context = _pause_context(tmp_path, select)
    action, _ = await _handle_iterate_pause(context, _Progress())
    assert action == "stop"
    assert _last_intervention(tmp_path)["detail"] == "select error"
