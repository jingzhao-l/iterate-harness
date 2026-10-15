"""Tests for the re-verification sending line (5.7 R40 scenario 1, this side).

Every payload here has a source:
  * the evidence packs come from the published corpus (`kernel_fixtures/evidence-decision
    .ok-01.json`), i.e. archives the engine really wrote;
  * the refusal text is the shape the shipped `glasspane-mcp` actually answers with,
    captured live on 2026-10-12 (`GP_E_NOT_ATTACHED: no app attached | remedy: call
    attach first`);
  * the "engine outcome" strings are the transcription's own vocabulary, not a fixture's.

The controls that matter are the refusals: a relay that invents a verdict, a runner that
sends a mutating call, or a misuse that silently returns coroutines are each given their
own named test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.iterate.glasspane_reverification import (
    MUTATING_TOOLS,
    DeferredTarget,
    plan_calls,
    relay_answer,
    run_reverification,
    run_reverification_async,
)
from iterate_harness.iterate.invariant_report import (
    from_defensive_report,
    invariant_report_digest,
    re_verification_request_from_targets,
)

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"


def _real_pack() -> dict[str, Any]:
    """One archived evidence pack, exactly as the engine wrote it."""
    corpus = json.loads((CORPUS / "evidence-decision.ok-01.json").read_text(encoding="utf-8"))
    for case in corpus["cases"]:
        candidate = case.get("input")
        if isinstance(candidate, dict) and isinstance(candidate.get("operationId"), str):
            return candidate
    raise AssertionError("the corpus carries no real pack to relay; nothing here would be production-shaped")


def _failing_request() -> dict[str, Any]:
    ensure = ["README.md", "not-a-real-file.md"]
    commands: dict[str, list[str]] = {}

    class _Violation:
        kind = "ensure"
        label = "ensure:not-a-real-file.md"
        detail = "missing file: not-a-real-file.md"

    class _Report:
        passed = False
        checks_run = 2
        violations = [_Violation()]

    report = from_defensive_report(ensure, commands, _Report())  # type: ignore[arg-type]
    request = re_verification_request_from_targets(report, {"operationIds": [_real_pack()["operationId"]]})
    assert request is not None, "a failing report must produce a request"
    assert request["trigger"]["reportDigest"] == invariant_report_digest(report)
    return request


# ------------------------------------------------------------------ the plan: read-only only


def test_only_read_only_calls_are_planned() -> None:
    calls, _deferred = plan_calls(_failing_request())
    names = {tool for tool, _args in calls}
    assert names, "a request with operation ids must plan at least one call"
    assert names & set(MUTATING_TOOLS) == set(), f"the sender planned a mutating call: {names & set(MUTATING_TOOLS)}"
    # The mixed request is the one that could tempt a mutation: it carries a step whose
    # kind is literally `act`.
    mixed_calls, _mixed_deferred = plan_calls(
        {"targets": {"recipeSteps": [{"kind": "act", "params": {}}], "operationIds": [_real_pack()["operationId"]]}}
    )
    assert {tool for tool, _args in mixed_calls} & set(MUTATING_TOOLS) == set()


def test_a_recipe_step_and_a_dimension_are_deferred_not_dropped() -> None:
    """Silently dropping them would make a partial re-verification read as a complete one."""
    calls, deferred = plan_calls(
        {
            "targets": {
                "recipeSteps": [{"kind": "act", "params": {"selector": {"role": "AXButton"}}}],
                "dimensions": ["security"],
            }
        }
    )
    assert calls == []
    kinds = {target.kind for target in deferred}
    assert kinds == {"recipeStep", "dimension"}
    assert all(target.reason for target in deferred)


def test_a_request_without_targets_defers_with_a_reason() -> None:
    _calls, deferred = plan_calls({"targets": {}})
    assert deferred and isinstance(deferred[0], DeferredTarget)


# ---------------------------------------------------------------------------- the relay


def test_a_real_pack_is_relayed_with_the_engines_own_outcome() -> None:
    pack = _real_pack()
    answer = relay_answer("gp_last_evidence", {"operationId": pack["operationId"]}, json.dumps({"evidencePack": pack}))
    assert answer.state == "answered"
    assert answer.operation_id == pack["operationId"]
    assert answer.outcome in {"pass", "fail", "inconclusive", "soft-breaker", "breaker"}
    assert answer.summary


def test_the_engines_refusal_line_is_relayed_as_a_refusal() -> None:
    # Captured live from the shipped shell on 2026-10-12 — this is what `gp_diagnose`
    # says when nothing is attached, and it is not JSON.
    text = "GP_E_NOT_ATTACHED: no app attached | remedy: call attach first"
    answer = relay_answer("gp_diagnose", {}, text)
    assert answer.state == "refused"
    assert answer.code == "GP_E_NOT_ATTACHED"
    assert answer.remedy == "call attach first", "the remedy is the part the agent can execute"


def test_an_answer_of_unknown_shape_is_reported_and_not_interpreted() -> None:
    for raw in ("not json at all", "{", json.dumps({"mystery": 1}), json.dumps(["array"]), 42):
        answer = relay_answer("gp_last_evidence", {}, raw)  # type: ignore[arg-type]
        assert answer.state == "unparseable", f"{raw!r} was classified as an answer"


def test_a_pack_that_cannot_be_transcribed_is_not_called_a_verdict() -> None:
    """The transcription answers `inconclusive` for *absent* fields by design.

    Forwarding that as the engine's verdict would turn "this pack records nothing" into
    "the engine looked and could not tell", which is a different claim about the app.
    """
    answer = relay_answer("gp_last_evidence", {}, json.dumps({"evidencePack": {"schemaVersion": "glasspane.evidence/0.1"}}))
    assert answer.state == "unparseable"
    assert answer.code == "GP_E_PACK_INCOMPLETE"
    assert "operationId" in (answer.message or "")
    # A full pack still relays, so this is a gate on absence and not a blanket refusal.
    full = _real_pack()
    ok = relay_answer("gp_last_evidence", {}, json.dumps({"evidencePack": full}))
    assert ok.state == "answered"


# ------------------------------------------------------------------------- the runner


def test_no_channel_says_not_attempted_rather_than_returning_an_empty_agreement() -> None:
    run = run_reverification(_failing_request(), None)
    assert not run.attempted
    assert run.not_attempted_reason
    assert any("NOT attempted" in line for line in run.to_model_lines())
    assert not run.all_clean, "an unattempted run must never read as clean"


def test_the_sync_runner_refuses_an_async_host_instead_of_reliving_coroutines() -> None:
    async def host(_tool: str, _args: dict[str, Any]) -> str:
        return "{}"

    run = run_reverification(_failing_request(), host)  # type: ignore[arg-type]
    assert run.attempted
    codes = {answer.code for answer in run.answers}
    assert "GP_E_ASYNC_HOST" in codes, "the misuse must be named, not filed as noise"
    assert all(answer.state == "unparseable" for answer in run.answers)


def test_the_async_runner_relays_what_the_engine_answered() -> None:
    pack = _real_pack()
    asked: list[str] = []

    async def host(tool: str, arguments: dict[str, Any]) -> str:
        asked.append(tool)
        if tool == "gp_last_evidence":
            return json.dumps({"evidencePack": pack})
        return "GP_E_NOT_ATTACHED: no app attached | remedy: call attach first"

    run = asyncio.run(run_reverification_async(_failing_request(), host))
    assert run.attempted
    assert set(asked) == {"gp_last_evidence", "gp_diagnose"}
    states = {answer.state for answer in run.answers}
    assert states == {"answered", "refused"}
    assert not run.all_clean, "an engine refusal in the set means the run is not clean"
    text = "\n".join(run.to_model_lines())
    assert pack["operationId"] in text and "GP_E_NOT_ATTACHED" in text and "call attach first" in text


def test_a_channel_that_throws_is_reported_as_a_failed_call_not_a_missing_answer() -> None:
    def host(_tool: str, _args: dict[str, Any]) -> str:
        raise ConnectionError("glasspane-mcp exited")

    run = run_reverification(_failing_request(), host)
    assert run.attempted and run.answers
    assert all(answer.code == "GP_E_CHANNEL" for answer in run.answers)
    assert all(answer.remedy for answer in run.answers)


def test_the_request_digest_is_the_one_the_invariant_report_carries() -> None:
    request = _failing_request()
    run = run_reverification(request, None)
    assert run.request_digest == request["trigger"]["reportDigest"]
    assert run.request_digest.startswith("iv_")


@pytest.mark.parametrize("tool", MUTATING_TOOLS)
def test_a_mutating_tool_name_can_never_reach_the_engine(tool: str) -> None:
    calls, _deferred = plan_calls({"targets": {"operationIds": [_real_pack()["operationId"]]}})
    assert all(name != tool for name, _args in calls)
