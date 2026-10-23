"""The shell's echoed dimension is witnessed where the agent's own call happens (场景②/⑧).

Trace this file against the two seams it covers:

    McpToolAdapter.execute   -> call_tool_parts, joined for the model, echo witnessed
    record_observed_echo     -> the run's tool metadata, keyed by operation
    echo_contestation        -> iterate_decision_log refuses a claim that contradicts the witness
    relay_answer             -> the same classifier scenario ① uses, one parse, two consumers

Why the judgement lives here and not over the ledger's own ``reverification`` rows (an earlier
version of this put it there, and that version was unreachable in production): the only sender
that could put an echo into those rows is scenario ①'s re-verification, and ① could declare a
dimension for an operation only by quoting the attribution the check was meant to test. Here the
two sides are independent — the shell witnessed the call at the time, the agent is claiming
about it afterwards — so a mismatch is evidence rather than a tautology.

The fixtures are production-shaped: the pack comes from the shipped corpus
(``tests/kernel_fixtures/evidence-decision.ok-01.json``) and the echo line is transcribed from
``mcp-shell/src/dimension.ts``, whose advertised shape was read off the bundle built from today's
main (the older binary installed on this machine has no slot at all, which is the other branch
several of these cases pin).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.iterate import decision_log, dimension_ledger, run_plan
from iterate_harness.iterate.decision_log import make_entry
from iterate_harness.iterate.glasspane_reverification import relay_answer
from iterate_harness.iterate.invariant_report import OPERATION_ID_PATTERN
from iterate_harness.iterate.reverify_trigger import (
    GLASSPANE_TRANSPORT_KEY,
    trigger_reverification,
)
from iterate_harness.mcp.types import McpToolInfo
from iterate_harness.tools.base import ToolExecutionContext
from iterate_harness.tools.iterate_tools import IterateDecisionLogInput, IterateDecisionLogTool
from iterate_harness.tools.mcp_tool import McpToolAdapter

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"

ECHO_SECURITY = (
    "dimension: security | declared by this call; this shell records no decision, so the ledger "
    "gains an attribution only where a decision is written for this operation"
)
PROVIDER_LINE = (
    "provider: glasspane-engine-ae version 0.1.9 method last_evidence"
    " | declared prerequisites: evidencePack with at least one non-empty signal field"
)
UPDATE_BLOCK = (
    "update state: status=needs-consent current=1.3.1 latest=1.4.0"
    " updatedAt=2026-09-29T04:00:00.000Z autoUpdate=on"
)


def _real_pack() -> dict[str, Any]:
    corpus = json.loads((CORPUS / "evidence-decision.ok-01.json").read_text(encoding="utf-8"))
    for case in corpus["cases"]:
        candidate = case.get("input")
        if isinstance(candidate, dict) and isinstance(candidate.get("operationId"), str):
            return candidate
    raise AssertionError("the corpus carries no pack; nothing here would be production-shaped")


PACK = _real_pack()
OP = str(PACK["operationId"])
assert OPERATION_ID_PATTERN.match(OP), OP
OTHER_OP = "op_" + "B" * 26
assert OPERATION_ID_PATTERN.match(OTHER_OP), OTHER_OP


def _frame() -> str:
    return json.dumps({"evidencePack": PACK})


def _reply(echo: str | None = ECHO_SECURITY) -> list[str]:
    items = [_frame(), UPDATE_BLOCK, PROVIDER_LINE]
    if echo is not None:
        items.append(echo)
    return items


class StubManager:
    """The two views ``McpClientManager`` offers, so byte-equality is asserted not assumed."""

    def __init__(self, parts: list[str]) -> None:
        self._parts = parts
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool_parts(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> list[str]:
        self.calls.append((tool_name, dict(arguments)))
        return list(self._parts)

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        return "\n".join(await self.call_tool_parts(server_name, tool_name, arguments)).strip()


def _adapter(parts: list[str]) -> tuple[McpToolAdapter, StubManager]:
    manager = StubManager(parts)
    info = McpToolInfo(
        server_name="glasspane-mcp",
        name="gp_last_evidence",
        description="read the pack",
        input_schema={
            "type": "object",
            "properties": {
                "operationId": {"type": "string"},
                "dimension": {"type": "string", "pattern": "^[a-z][a-z0-9]*(-[a-z0-9]+)*$"},
            },
        },
    )
    return McpToolAdapter(manager, info), manager


# ------------------------------------------------------------------ the classifier side of it


def test_the_echo_item_is_read_and_stays_verbatim_beside_the_frame() -> None:
    answer = relay_answer("gp_last_evidence", {"operationId": OP}, _reply())
    assert answer.state == "answered"
    assert answer.echoed_dimension == "security"
    assert ECHO_SECURITY in answer.annotations
    assert PROVIDER_LINE in answer.annotations


def test_a_reply_without_an_echo_claims_nothing() -> None:
    assert relay_answer("gp_last_evidence", {"operationId": OP}, _reply(None)).echoed_dimension is None


def test_the_single_item_shape_has_no_echo_to_read() -> None:
    answer = relay_answer("gp_last_evidence", {"operationId": OP}, _frame())
    assert answer.state == "answered"
    assert answer.echoed_dimension is None


def test_a_malformed_or_shifted_echo_is_silence_not_a_competing_claim() -> None:
    for line in [
        "dimension: Spec Compliance | declared by this call",
        "dimension: spec_compliance | declared by this call",
        "dimension:  | declared by this call",
        "the dimension: security | not an echo the shell writes",
    ]:
        answer = relay_answer("gp_diagnose", {}, [_frame(), line])
        assert answer.echoed_dimension is None, line
        assert line in answer.annotations, "the side item is still the model's to read"


# ------------------------------------------------------ the adapter: same bytes, plus a witness


def test_the_adapter_returns_exactly_what_the_joined_view_returned() -> None:
    """The model-visible bytes must not move: ``call_tool`` IS this join."""
    adapter, manager = _adapter(_reply())
    arguments = adapter.input_model(operationId=OP, dimension="security")
    context = ToolExecutionContext(cwd=Path("/tmp"))
    result = asyncio.run(adapter.execute(arguments, context))
    assert result.is_error is False
    assert result.output == "\n".join(_reply()).strip()
    assert result.output == asyncio.run(manager.call_tool("glasspane-mcp", "gp_last_evidence", {"operationId": OP}))


def test_the_adapter_witnesses_the_declaration_against_the_operation_it_answered() -> None:
    adapter, _manager = _adapter(_reply())
    context = ToolExecutionContext(cwd=Path("/tmp"))
    arguments = adapter.input_model(operationId=OP, dimension="security")
    asyncio.run(adapter.execute(arguments, context))
    assert context.metadata[dimension_ledger.ECHO_METADATA_KEY] == {OP: "security"}


def test_a_reply_without_an_echo_leaves_the_stash_alone() -> None:
    adapter, _manager = _adapter(_reply(None))
    context = ToolExecutionContext(cwd=Path("/tmp"))
    asyncio.run(adapter.execute(adapter.input_model(operationId=OP), context))
    assert dimension_ledger.ECHO_METADATA_KEY not in context.metadata


def test_a_non_glasspane_reply_never_reaches_the_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prefix guard is the whole cost model: no echo line means no classify, no parse.

    The spy records rather than raises. The adapter deliberately keeps a witness failure from
    breaking an agent's tool reply, so a raising spy would be swallowed by exactly that guard and
    the shot would read as a pass — a recorder cannot be swallowed, which is what makes the
    "never called" claim checkable.
    """
    from iterate_harness.tools import mcp_tool as mcp_tool_module

    seen: list[str] = []

    def recording(tool: str, arguments: Any, raw: Any) -> Any:
        seen.append(tool)
        return relay_answer(tool, arguments, raw)

    monkeypatch.setattr(mcp_tool_module, "relay_answer", recording)

    adapter, manager = _adapter(["just prose, no frame, no echo"])
    context = ToolExecutionContext(cwd=Path("/tmp"))
    result = asyncio.run(adapter.execute(adapter.input_model(operationId=OP), context))
    assert result.is_error is False
    assert result.output == "just prose, no frame, no echo"
    assert seen == [], "a plain reply was run through the GlassPane classifier"
    assert dimension_ledger.ECHO_METADATA_KEY not in context.metadata
    assert manager.calls == [("gp_last_evidence", {"operationId": OP})]


def test_an_echo_reply_does_reach_the_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other side of the guard: it must not skip the calls it exists to witness."""
    from iterate_harness.tools import mcp_tool as mcp_tool_module

    seen: list[str] = []

    def recording(tool: str, arguments: Any, raw: Any) -> Any:
        seen.append(tool)
        return relay_answer(tool, arguments, raw)

    monkeypatch.setattr(mcp_tool_module, "relay_answer", recording)
    adapter, _manager = _adapter(_reply())
    context = ToolExecutionContext(cwd=Path("/tmp"))
    asyncio.run(adapter.execute(adapter.input_model(operationId=OP, dimension="security"), context))
    assert seen == ["gp_last_evidence"]


# --------------------------------------------------------------- the ledger's write-time check


def _stash(context: ToolExecutionContext, mapping: dict[str, str]) -> None:
    context.metadata[dimension_ledger.ECHO_METADATA_KEY] = dict(mapping)


def _append(context: ToolExecutionContext, operation_id: str, dimension: str) -> Any:
    tool = IterateDecisionLogTool()
    return asyncio.run(
        tool.execute(
            IterateDecisionLogInput(
                operation="append",
                type="decision",
                round=1,
                data={"kind": "evidence", "summary": "engine answered"},
                operation_id=operation_id,
                dimension=dimension,
            ),
            context,
        )
    )


def test_a_decision_that_contradicts_the_witness_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    context = ToolExecutionContext(cwd=tmp_path)
    _stash(context, {OP: "security"})
    result = _append(context, OP, "correctness")
    body = json.loads(result.output)
    assert result.is_error is True
    assert body["code"] == dimension_ledger.DIMENSION_CONTEST_CODE
    assert "security" in body["remedy"] and "correctness" in body["remedy"]
    assert not (tmp_path / ".iterate" / "decision-log.jsonl").exists(), "a refusal must not reach the ledger"


def test_a_decision_that_agrees_with_the_witness_lands(tmp_path: Path) -> None:
    context = ToolExecutionContext(cwd=tmp_path)
    _stash(context, {OP: "security"})
    result = _append(context, OP, "security")
    assert result.is_error is False, result.output
    entries = decision_log.read_entries(tmp_path)
    assert entries[-1].data["dimension"] == "security"


def test_no_witness_leaves_the_claim_standing(tmp_path: Path) -> None:
    """Absence is not disagreement: a shell with no slot, or an op never fetched, says nothing."""
    context = ToolExecutionContext(cwd=tmp_path)
    result = _append(context, OTHER_OP, "security")
    assert result.is_error is False, result.output
    assert _append(context, OTHER_OP, "correctness").is_error is False


def test_the_witness_only_objects_it_cannot_adjudicate(tmp_path: Path) -> None:
    """A witness for a different operation must not contest this one."""
    context = ToolExecutionContext(cwd=tmp_path)
    _stash(context, {OP: "security"})
    assert _append(context, OTHER_OP, "correctness").is_error is False


# --------------------------------------------------------------------- the stash's own limits


def test_the_stash_is_bounded_and_evicts_the_oldest_witness() -> None:
    """Exactly at the cap, and the window slides: an unbounded dict rides every later turn."""
    metadata: dict[str, Any] = {}
    written: list[str] = []
    for index in range(dimension_ledger.ECHO_METADATA_CAP + 3):
        operation_id = f"op_{index:026d}"
        assert OPERATION_ID_PATTERN.match(operation_id), operation_id
        dimension_ledger.record_observed_echo(metadata, operation_id, "security")
        written.append(operation_id)
    stash = metadata[dimension_ledger.ECHO_METADATA_KEY]
    assert len(stash) == dimension_ledger.ECHO_METADATA_CAP, len(stash)
    assert written[-1] in stash, "the most recent witness must survive"
    assert written[0] not in stash, "the oldest witness is what the cap is for"


def test_the_stash_refuses_an_echo_it_cannot_key() -> None:
    metadata: dict[str, Any] = {}
    dimension_ledger.record_observed_echo(metadata, None, "security")
    dimension_ledger.record_observed_echo(metadata, "not-an-op", "security")
    dimension_ledger.record_observed_echo(metadata, OP, "Spec Compliance")
    dimension_ledger.record_observed_echo(metadata, OP, None)
    assert metadata == {}


# --------------------------------------------------- the reverification row still carries a citation


def test_the_recorder_still_cites_the_echo_without_judging_it(tmp_path: Path) -> None:
    """①'s rows carry `echoedDimension` when a shell echoed, and nothing when it did not."""

    def transport(tool: str, arguments: dict[str, Any]) -> object:
        return _reply()

    from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY
    from iterate_harness.iterate.loop_gates import measured_gates

    (tmp_path / "README.md").write_text("# real project\n", encoding="utf-8")
    (tmp_path / "iterate.config.yaml").write_text(
        "invariants:\n  ensure:\n    - README.md\n    - not-a-real-file.md\n"
        "reviewer:\n  evidence_validation: false\n",
        encoding="utf-8",
    )
    kernel = asyncio.run(_failing_kernel(tmp_path))
    decision_log.append_entry(
        tmp_path,
        make_entry(
            entry_type="decision",
            round_number=1,
            data={"kind": "evidence", "summary": "cited", "operationId": OP},
        ),
    )
    metadata = {DEFENSIVE_KERNEL_KEY: kernel, GLASSPANE_TRANSPORT_KEY: transport}
    published = trigger_reverification(
        project_root=tmp_path,
        metadata=metadata,
        gates=measured_gates(
            evidence_validation=False, findings=[], cwd=tmp_path, metadata={DEFENSIVE_KERNEL_KEY: kernel}
        ),
        round_number=1,
        planned_dimensions=["security"],
    )
    assert published is not None and published["attempted"] is True
    assert dimension_ledger.ECHOED_DIMENSION_DATA_KEY in json.dumps(published)
    rows = [entry.data for entry in decision_log.read_entries(tmp_path)]
    reverification = [row for row in rows if isinstance(row, dict) and row.get("kind") == "reverification"]
    assert reverification and dimension_ledger.ECHOED_DIMENSION_DATA_KEY in json.dumps(reverification[-1])


async def _failing_kernel(tmp_path: Path):
    from iterate_harness.defensive.kernel import DefensiveKernel
    from iterate_harness.iterate.config_loader import load_effective_config

    kernel = DefensiveKernel(tmp_path, load_effective_config(tmp_path))
    await kernel.check_invariants()
    assert kernel.last_invariant_report is not None
    return kernel


# ---------------------------------------------------- the ledger verdict is untouched by any of it


def test_the_coverage_verdict_counts_attributions_as_the_kernel_sees_them(tmp_path: Path) -> None:
    """No shell witness reaches this number; the ledger alone decides coverage."""
    plan = run_plan.build_run_plan({"dimensions": ["security", "correctness"]}, source_name="test")
    run_plan.write_run_plan(plan, run_plan.plan_path(tmp_path))
    context = ToolExecutionContext(cwd=tmp_path)
    _stash(context, {OP: "correctness"})
    assert _append(context, OP, "security").is_error is True
    built = dimension_ledger.project_dimension_context(tmp_path)
    assert built is not None
    assert {entry.id: entry.status for entry in built.planned} == {
        "security": "unverified",
        "correctness": "unverified",
    }


# ------------------------------------------------ the carry the engine actually performs
#
# These two go through `_execute_tool_call`, the production function that folds tool metadata
# back into the per-session state. Without them the check is only proven to work when a test
# hands the stash over by hand — the exact mistake the ledger-side version made: a guard wired
# to a state no real turn ever reaches.


class _NoopApiClient:
    async def stream_messages(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("no provider turn is expected here")


def _query_context(tmp_path: Path, registry: Any) -> Any:
    from iterate_harness.config.settings import PermissionSettings
    from iterate_harness.engine.query import QueryContext
    from iterate_harness.permissions import PermissionChecker, PermissionMode

    return QueryContext(
        api_client=_NoopApiClient(),
        tool_registry=registry,
        # FULL_AUTO because an MCP-forwarded tool counts as mutating and DEFAULT would stop it at
        # the permission gate — this test measures the metadata carry, not the gate, and a
        # refusal there would look like the fold having failed.
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        max_tokens=1,
        max_turns=1,
        tool_metadata={},
    )


def test_the_engine_carries_the_witness_from_the_call_to_the_later_ledger_write(tmp_path: Path) -> None:
    from iterate_harness.engine.query import _execute_tool_call
    from iterate_harness.tools.base import ToolRegistry

    registry = ToolRegistry()
    adapter, _manager = _adapter(_reply())
    registry.register(adapter)
    registry.register(IterateDecisionLogTool())
    context = _query_context(tmp_path, registry)

    read = asyncio.run(
        _execute_tool_call(
            context,
            adapter.name,
            "toolu_read",
            {"operationId": OP, "dimension": "security"},
        )
    )
    assert read.is_error is False, read.content
    assert context.tool_metadata[dimension_ledger.ECHO_METADATA_KEY] == {OP: "security"}

    contradicting = asyncio.run(
        _execute_tool_call(
            context,
            "iterate_decision_log",
            "toolu_log",
            {
                "operation": "append",
                "type": "decision",
                "round": 1,
                "data": {"kind": "evidence", "summary": "engine answered"},
                "operation_id": OP,
                "dimension": "correctness",
            },
        )
    )
    assert contradicting.is_error is True
    assert dimension_ledger.DIMENSION_CONTEST_CODE in contradicting.content
    assert not (tmp_path / ".iterate" / "decision-log.jsonl").exists()

    agreeing = asyncio.run(
        _execute_tool_call(
            context,
            "iterate_decision_log",
            "toolu_log2",
            {
                "operation": "append",
                "type": "decision",
                "round": 1,
                "data": {"kind": "evidence", "summary": "engine answered"},
                "operation_id": OP,
                "dimension": "security",
            },
        )
    )
    assert agreeing.is_error is False, agreeing.content
    assert decision_log.read_entries(tmp_path)[-1].data["dimension"] == "security"


def test_two_sibling_mcp_calls_in_one_turn_both_leave_a_witness(tmp_path: Path) -> None:
    """The fold merges this one key: two parallel siblings must not overwrite each other.

    The barrier inside the fake client is the test. Without it the two calls finish one after the
    other, the second snapshots the first's dict and mutates it in place, and the assertion passes
    no matter how the fold behaves — it would be measuring the scheduler, not the rule. Forced
    overlap is the real shape of a multi-tool turn: both calls snapshot the durable metadata before
    either returns, so each builds its own witness dict and the second fold would otherwise
    replace the first's.
    """
    from iterate_harness.engine.query import _execute_tool_call
    from iterate_harness.tools.base import ToolRegistry

    class _OverlappingManager:
        """Two clients, one barrier: neither call returns until both are in flight."""

        def __init__(self, parts: list[str], both_in_flight: "asyncio.Event", arrivals: list[str]) -> None:
            self._parts = parts
            self._both = both_in_flight
            self._arrivals = arrivals

        async def call_tool_parts(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> list[str]:
            self._arrivals.append(server_name)
            if len(self._arrivals) >= 2:
                self._both.set()
            await asyncio.wait_for(self._both.wait(), timeout=10)
            return list(self._parts)

        async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
            return "\n".join(await self.call_tool_parts(server_name, tool_name, arguments)).strip()

    both_in_flight = asyncio.Event()
    arrivals: list[str] = []

    def adapter_for(server: str, operation_id: str) -> McpToolAdapter:
        pack = json.loads(json.dumps(PACK))
        pack["operationId"] = operation_id
        parts = [json.dumps({"evidencePack": pack}), UPDATE_BLOCK, PROVIDER_LINE, ECHO_SECURITY]
        info = McpToolInfo(
            server_name=server,
            name="gp_last_evidence",
            description="read the pack",
            input_schema={"type": "object", "properties": {"operationId": {"type": "string"}}},
        )
        return McpToolAdapter(_OverlappingManager(parts, both_in_flight, arrivals), info)

    first = adapter_for("glasspane-mcp", OP)
    second = adapter_for("glasspane-mcp-b", OTHER_OP)
    registry = ToolRegistry()
    registry.register(first)
    registry.register(second)
    context = _query_context(tmp_path, registry)

    async def both() -> list[Any]:
        return await asyncio.gather(
            _execute_tool_call(context, first.name, "toolu_a", {"operationId": OP}),
            _execute_tool_call(context, second.name, "toolu_b", {"operationId": OTHER_OP}),
        )

    results = asyncio.run(both())
    assert [result.is_error for result in results] == [False, False]
    assert len(arrivals) == 2, "the barrier never engaged, so the calls did not overlap"
    stash = context.tool_metadata[dimension_ledger.ECHO_METADATA_KEY]
    assert stash == {OP: "security", OTHER_OP: "security"}, stash


def test_the_contest_names_resolve_through_the_lazy_export_map() -> None:
    from iterate_harness import iterate as iterate_pkg

    for name in (
        "ECHOED_DIMENSION_DATA_KEY",
        "ECHO_METADATA_KEY",
        "DIMENSION_CONTEST_CODE",
        "record_observed_echo",
        "echo_contestation",
    ):
        assert getattr(iterate_pkg, name) is getattr(dimension_ledger, name), name
        assert name in iterate_pkg.__all__, name
    for gone in ("contested_attributions", "dimension_context_with_contested", "echoed_dimension_map"):
        assert not hasattr(dimension_ledger, gone), gone
        assert gone not in iterate_pkg.__all__, gone


@pytest.mark.parametrize("letter", ["B", "C"])
def test_the_operation_id_fixtures_follow_the_imported_pattern(letter: str) -> None:
    """A fixture that stopped matching the pattern would make every case silently unattributed."""
    assert OPERATION_ID_PATTERN.match(f"op_{letter * 26}")
