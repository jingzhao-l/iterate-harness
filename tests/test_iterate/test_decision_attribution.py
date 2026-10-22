"""Attribution on the write side: a decision says which dimension it served, or it is refused.

Scenario ②'s producer half. The kernel's ``decision-log-entry`` schema carries optional
``dimension``/``operationId`` and the transcription in
``iterate/kernel_decision_record.py`` keeps them *when valid* and drops them silently when
not — so the tool has to refuse a malformed id instead of writing an entry that looks
recorded and counts as unattributed. Each test below states the fact it protects, and the
zero-byte assertions are the point of the refusal tests: a refusal that appended something
would leave a decision nobody attributed and nobody can find.
"""

from __future__ import annotations

import json
from pathlib import Path

from iterate_harness.iterate import decision_log, dimension_ledger
from iterate_harness.iterate.dimension_context import (
    dimension_context,
    format_dimension_context,
)
from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
from iterate_harness.tools.base import ToolExecutionContext
from iterate_harness.tools.iterate_tools import (
    IterateDecisionLogInput,
    IterateDecisionLogTool,
    IterateReviewInput,
    IterateReviewTool,
)

#: ``op_`` plus 26 Crockford base32 characters (the alphabet the imported pattern allows:
#: digits and A-Z minus I, L, O, U). Built, not typed out, so a change to the pattern shows
#: up here instead of quietly making every fixture unattributed.
def _operation_id(letter: str) -> str:
    from iterate_harness.iterate.invariant_report import OPERATION_ID_PATTERN

    value = "op_" + letter * 26
    assert OPERATION_ID_PATTERN.match(value), value
    return value


OP_SECURITY = _operation_id("A")
OP_PERFORMANCE = _operation_id("B")
OP_SHARED = _operation_id("C")
OP_OTHER = _operation_id("D")

LEDGER = ".iterate/decision-log.jsonl"


def make_context(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=tmp_path)


def write_config(tmp_path: Path, content: str) -> None:
    (tmp_path / "iterate.config.yaml").write_text(content, encoding="utf-8")


async def run_tool(tool, arguments, context):
    return await tool.execute(arguments, context)


def append_args(**kwargs) -> IterateDecisionLogInput:
    return IterateDecisionLogInput(operation="append", **kwargs)


def ledger_bytes(tmp_path: Path) -> bytes | None:
    path = tmp_path / LEDGER
    return path.read_bytes() if path.exists() else None


async def aggregate_state(tmp_path: Path, context: ToolExecutionContext) -> dict:
    """Run one aggregate so the loop-policy state channel exists, and hand it back."""
    await run_tool(
        IterateReviewTool(),
        IterateReviewInput(
            operation="aggregate",
            rounds=[
                {
                    "round": 1,
                    "findings": [
                        {
                            "dimension": "security",
                            "file": "src/a.py",
                            "severity": "critical",
                            "summary": "sql injection",
                            "failure_scenario": "attacker input reaches a query",
                            "suggested_fix": "parameterize",
                            "is_atomic": True,
                            "line": 10,
                        }
                    ],
                }
            ],
            max_review_rounds=2,
        ),
        context,
    )
    return context.metadata[ITERATE_STATE_KEY]


class TestAttributedAppend:
    async def test_an_attributed_decision_reaches_the_ledger_and_the_coverage_line(
        self, tmp_path
    ):
        """The whole producer chain in one test: tool input -> entry data -> kernel draft -> coverage."""
        write_config(tmp_path, "dimensions:\n  - security\n  - correctness\n")
        context = make_context(tmp_path)
        result = await run_tool(
            IterateDecisionLogTool(),
            append_args(
                type="review_result",
                round=1,
                data={"summary": "no injection found"},
                dimension="security",
                operation_id=OP_SECURITY,
            ),
            context,
        )
        assert result.is_error is False, result.output
        payload = json.loads(result.output)
        assert payload["entry"]["data"]["dimension"] == "security"
        assert payload["entry"]["data"]["operationId"] == OP_SECURITY

        # The kernel transcription lifted the same two values out of the payload into the
        # entry's own optional fields -- that lift is what makes the attribution auditable
        # rather than a note inside a blob.
        lines = [json.loads(line) for line in (tmp_path / LEDGER).read_text().splitlines() if line]
        assert lines[0]["dimension"] == "security"
        assert lines[0]["operationId"] == OP_SECURITY
        assert lines[0]["payload"]["dimension"] == "security"

        state = await aggregate_state(tmp_path, context)
        assert state["dimension_coverage"] == format_dimension_context(
            dimension_context(["security", "correctness"], {"security": {"decisions": 1}})
        )

    async def test_an_uppercase_dimension_is_refused_and_the_ledger_gains_no_bytes(
        self, tmp_path
    ):
        write_config(tmp_path, "dimensions:\n  - security\n")
        context = make_context(tmp_path)
        before = ledger_bytes(tmp_path)
        result = await run_tool(
            IterateDecisionLogTool(),
            append_args(type="decision", round=1, dimension="Security"),
            context,
        )
        payload = json.loads(result.output)
        assert result.is_error is True
        assert payload["code"] == dimension_ledger.DIMENSION_E_ID
        assert payload["remedy"]
        assert ledger_bytes(tmp_path) == before
        assert decision_log.read_entries(tmp_path) == []

    async def test_a_dimension_longer_than_the_kernel_limit_is_refused_without_a_write(
        self, tmp_path
    ):
        from iterate_harness.iterate.dimension_context import DIMENSION_ID_MAX_LENGTH

        write_config(tmp_path, "dimensions:\n  - security\n")
        context = make_context(tmp_path)
        await run_tool(
            IterateDecisionLogTool(),
            append_args(type="decision", round=1, dimension="security"),
            context,
        )
        before = ledger_bytes(tmp_path)
        too_long = "a" * (DIMENSION_ID_MAX_LENGTH + 1)
        result = await run_tool(
            IterateDecisionLogTool(),
            append_args(type="decision", round=1, dimension=too_long),
            context,
        )
        payload = json.loads(result.output)
        assert result.is_error is True
        assert payload["code"] == dimension_ledger.DIMENSION_E_ID
        assert str(DIMENSION_ID_MAX_LENGTH) in payload["remedy"], (
            "the remedy quotes the limit it enforced; restating a number would let the two diverge"
        )
        assert ledger_bytes(tmp_path) == before, "a refusal must not leave a second decision behind"
        assert len(decision_log.read_entries(tmp_path)) == 1

    async def test_a_malformed_operation_id_is_refused_without_a_write(self, tmp_path):
        write_config(tmp_path, "dimensions:\n  - security\n")
        context = make_context(tmp_path)
        before = ledger_bytes(tmp_path)
        result = await run_tool(
            IterateDecisionLogTool(),
            append_args(type="decision", round=1, operation_id="op_short"),
            context,
        )
        payload = json.loads(result.output)
        assert result.is_error is True
        assert payload["code"] == dimension_ledger.OPERATION_ID_E_CODE
        assert payload["remedy"]
        assert ledger_bytes(tmp_path) == before
        assert decision_log.read_entries(tmp_path) == []

    async def test_an_omitted_attribution_is_still_a_valid_unattributed_decision(
        self, tmp_path
    ):
        """Absence is a fact the run is allowed to state: no dimension field, no refusal, empty coverage."""
        write_config(tmp_path, "dimensions:\n  - security\n  - correctness\n")
        context = make_context(tmp_path)
        result = await run_tool(
            IterateDecisionLogTool(),
            append_args(type="decision", round=1, data={"kind": "note"}),
            context,
        )
        assert result.is_error is False
        assert "dimension" not in json.loads(result.output)["entry"]["data"]
        state = await aggregate_state(tmp_path, context)
        assert state["dimension_coverage"].startswith("0/2 dimensions verified"), (
            "an unattributed decision verifies nothing; counting it would invent a verdict"
        )

    async def test_the_attributed_append_refreshes_the_published_coverage_line(self, tmp_path):
        """The line is a fact about the ledger, so it goes stale the moment the ledger grows.

        Before the append the run had no attributed decision at all (aggregate's findings are
        not ledger attributions), and the append must move the verdict rather than leave the
        compacted model reading yesterday's number.
        """
        write_config(tmp_path, "dimensions:\n  - security\n  - correctness\n")
        context = make_context(tmp_path)
        state = await aggregate_state(tmp_path, context)
        assert state["dimension_coverage"].startswith("0/2 dimensions verified")

        result = await run_tool(
            IterateDecisionLogTool(),
            append_args(
                type="review_result",
                round=2,
                dimension="correctness",
                operation_id=OP_PERFORMANCE,
            ),
            context,
        )
        payload = json.loads(result.output)
        expected = format_dimension_context(
            dimension_context(["security", "correctness"], {"correctness": {"decisions": 1}})
        )
        assert payload["dimensionCoverage"] == expected
        assert state["dimension_coverage"] == expected, (
            "the refresh mutates the published snapshot rather than returning a private copy"
        )

    async def test_an_attributed_append_does_not_fabricate_a_loop_snapshot(self, tmp_path):
        """This tool does not own ``iterate_state``; publishing a half-populated one would
        hand the loop policy a round count and a mode nobody measured."""
        write_config(tmp_path, "dimensions:\n  - security\n")
        context = make_context(tmp_path)
        result = await run_tool(
            IterateDecisionLogTool(),
            append_args(type="decision", round=1, dimension="security"),
            context,
        )
        assert result.is_error is False
        assert ITERATE_STATE_KEY not in context.metadata
        assert "dimensionCoverage" not in json.loads(result.output)


class TestDimensionLedger:
    """The one accounting both readers share: the tool's coverage line and the compaction seam."""

    def test_the_plan_file_wins_and_an_empty_plan_file_is_a_plan_not_an_absence(self, tmp_path):
        from iterate_harness.iterate import run_plan

        plan = run_plan.build_run_plan({"dimensions": ["security"]}, source_name="test")
        run_plan.write_run_plan(plan, run_plan.plan_path(tmp_path))
        assert dimension_ledger.planned_dimension_ids(tmp_path, fallback=["perf"]) == ["security"]

        (tmp_path / ".iterate" / run_plan.RUN_PLAN_FILENAME).write_text(
            json.dumps({"dimensions": []}), encoding="utf-8"
        )
        assert dimension_ledger.planned_dimension_ids(tmp_path, fallback=["perf"]) == [], (
            "[] (a plan that names nothing) and None (no plan to read) are different facts"
        )

    def test_only_a_valid_dimension_or_operation_id_is_read_back(self, tmp_path):
        """A hand-written ledger line cannot create an attribution the kernel would not have written."""
        decision_log.append_entry(
            tmp_path,
            decision_log.make_entry(
                entry_type="decision",
                round_number=1,
                data={"dimension": "security", "operationId": OP_SECURITY},
            ),
        )
        assert dimension_ledger.recorded_dimension_counts(tmp_path) == {"security": 1}

        path = tmp_path / LEDGER
        lines = [json.loads(line) for line in path.read_text().splitlines() if line]
        lines[0]["payload"]["dimension"] = "Security"
        lines[0]["payload"]["operationId"] = "op_short"
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        assert decision_log.read_entries(tmp_path)[0].data["dimension"] == "Security", (
            "the reader stays tolerant; it is the attribution that must not be guessed"
        )
        assert dimension_ledger.recorded_dimension_counts(tmp_path) == {}
        assert dimension_ledger.operation_dimension_map(tmp_path) == {}

    def test_a_shared_operation_id_takes_the_last_ledger_attribution(self, tmp_path):
        """One operation, two decisions naming different dimensions: the newest attribution wins.

        The ledger is append-only and in decision order, so the last entry naming an
        operation is the attribution the run settled on. Anything else (dict order, sorted
        ids) would give two readers of the same file different answers.
        """
        for dimension in ("security", "performance"):
            decision_log.append_entry(
                tmp_path,
                decision_log.make_entry(
                    entry_type="decision",
                    round_number=1,
                    data={"dimension": dimension, "operationId": OP_SHARED},
                ),
            )
        decision_log.append_entry(
            tmp_path,
            decision_log.make_entry(
                entry_type="decision",
                round_number=1,
                data={"dimension": "security", "operationId": OP_OTHER},
            ),
        )
        assert dimension_ledger.operation_dimension_map(tmp_path) == {
            OP_SHARED: "performance",
            OP_OTHER: "security",
        }
        assert dimension_ledger.recorded_dimension_counts(tmp_path) == {
            "security": 2,
            "performance": 1,
        }, "every attributed decision counts, whichever way a shared operation id resolved"

    def test_the_refusal_remedies_quote_the_imported_rules_not_a_copy_of_them(self):
        from iterate_harness.iterate.dimension_context import (
            DIMENSION_ID_MAX_LENGTH,
            DIMENSION_ID_PATTERN,
        )
        from iterate_harness.iterate.invariant_report import OPERATION_ID_PATTERN

        dimension = dimension_ledger.attribution_refusal(
            dimension="Security", operation_id=None
        )
        operation = dimension_ledger.attribution_refusal(
            dimension=None, operation_id="op_short"
        )
        assert DIMENSION_ID_PATTERN.pattern in dimension["remedy"]
        assert str(DIMENSION_ID_MAX_LENGTH) in dimension["remedy"]
        assert OPERATION_ID_PATTERN.pattern in operation["remedy"]
        assert dimension_ledger.attribution_refusal(dimension=None, operation_id=None) is None


def test_the_new_names_resolve_through_the_lazy_export_map() -> None:
    """``iterate/__init__.py`` re-exports lazily; an unregistered name would be invisible."""
    from iterate_harness import iterate as iterate_pkg

    for name in (
        "operation_dimension_map",
        "planned_dimension_ids",
        "project_dimension_context",
        "recorded_dimension_counts",
    ):
        assert getattr(iterate_pkg, name) is getattr(dimension_ledger, name)
        assert name in iterate_pkg.__all__
