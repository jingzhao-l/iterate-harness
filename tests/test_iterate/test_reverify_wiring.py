"""The wiring: a measured invariant failure must actually reach the GlassPane sender.

``iterate/glasspane_reverification.py`` was complete and tested in isolation; these cases test
only the hand-off — ``iterate/reverify_trigger.py`` plus the one production call site in the
``iterate_review`` aggregate.

Frames are production-shaped, not invented:

* the invariant report comes from a **real** ``DefensiveKernel`` running ``check_invariants``
  against a real config whose ``ensure`` list names a file the tmp project does not have;
* the evidence pack, its ``operationId`` and the AE method are read from the published corpus
  (``kernel_fixtures/evidence-decision.ok-01.json``) exactly as
  ``test_glasspane_reverification.py`` does;
* the request vocabulary (``schemaVersion`` / ``trigger.kind`` / ``targets``) is the pinned
  ``invariant-report.ok-01.json`` fixture's ``request`` section;
* the engine refusal text is the line the shipped ``glasspane-mcp`` answers with.

The named controls are the honesty ones: a pass may not send, a missing verdict may not speak, a
dead channel may not measure a gate, and a refused ledger write may not be swallowed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY, DefensiveKernel
from iterate_harness.iterate import reverify_trigger
from iterate_harness.iterate.decision_log import (
    LOG_DIR,
    LOG_FILE,
    append_entry,
    make_entry,
    read_entries,
)
from iterate_harness.iterate.glasspane_recipe import (
    GATE_E_UNPROVEN,
    RecipeError,
    fix_proof,
    render_gate_state,
)
from iterate_harness.iterate.glasspane_reverification import (
    CONFIG_READ_TOOLS,
    READ_ONLY_TOOLS,
    plan_calls,
)
from iterate_harness.iterate.loop_gates import GATE_KEYS, measured_gates
from iterate_harness.iterate.reverify_trigger import (
    GLASSPANE_TRANSPORT_KEY,
    LEDGER_GATE_DATA_KIND,
    RECIPE_GATE_STATE_KEY,
    REGISTRY_READ_TOOL,
    REVERIFICATION_STATE_KEY,
    trigger_reverification,
)
from iterate_harness.tools.iterate_tools import IterateReviewInput, IterateReviewTool

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"

#: Captured live from the shipped shell (see test_glasspane_reverification): the refusal line is
#: text, not JSON, and its remedy is the part an agent can execute.
ENGINE_REFUSAL = "GP_E_NOT_ATTACHED: no app attached | remedy: call attach first"

FINDING = {
    "dimension": "security",
    "file": "src/a.py",
    "severity": "critical",
    "summary": "sql injection",
    "failure_scenario": "attacker input reaches query",
    "suggested_fix": "parameterize",
    "is_atomic": True,
    "line": 3,
}


def _real_pack() -> dict[str, Any]:
    corpus = json.loads((CORPUS / "evidence-decision.ok-01.json").read_text(encoding="utf-8"))
    for case in corpus["cases"]:
        candidate = case.get("input")
        if isinstance(candidate, dict) and isinstance(candidate.get("operationId"), str):
            return candidate
    raise AssertionError("the corpus carries no pack; nothing here would be production-shaped")


def _pinned_request() -> dict[str, Any]:
    return json.loads((CORPUS / "invariant-report.ok-01.json").read_text(encoding="utf-8"))["request"]


async def _project(tmp_path: Path, ensure: list[str]) -> DefensiveKernel:
    """A real defensive kernel that really ran the project's invariants."""
    (tmp_path / "README.md").write_text("# real project\n", encoding="utf-8")
    lines = "".join(f"    - {entry}\n" for entry in ensure)
    (tmp_path / "iterate.config.yaml").write_text(
        f"invariants:\n  ensure:\n{lines}", encoding="utf-8"
    )
    from iterate_harness.iterate.config_loader import load_effective_config

    kernel = DefensiveKernel(tmp_path, load_effective_config(tmp_path))
    await kernel.check_invariants()
    return kernel


def _gates(tmp_path: Path, kernel: DefensiveKernel | None) -> dict[str, object]:
    metadata = {DEFENSIVE_KERNEL_KEY: kernel} if kernel is not None else {}
    return measured_gates(evidence_validation=False, findings=[], cwd=tmp_path, metadata=metadata)


def _record_an_operation(tmp_path: Path, operation_id: str) -> None:
    """The engine recorded this operation, so the run may cite it."""
    append_entry(
        tmp_path,
        make_entry(
            entry_type="decision",
            round_number=1,
            data={"kind": "evidence", "operationId": operation_id, "summary": "engine recorded"},
        ),
    )


def _evidence_transport(pack: dict[str, Any]) -> tuple[Any, list[tuple[str, dict[str, Any]]]]:
    asked: list[tuple[str, dict[str, Any]]] = []

    def call_tool(tool: str, arguments: dict[str, Any]) -> object:
        asked.append((tool, arguments))
        if tool == "gp_last_evidence":
            return json.dumps({"evidencePack": pack})
        return ENGINE_REFUSAL

    return call_tool, asked


@pytest.fixture
def sender(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Records how many times the loop actually invoked the sender, and delegates for real."""
    seen: list[dict[str, Any]] = []
    real = reverify_trigger.run_reverification

    def spy(request: Any, call_tool: Any, **kwargs: Any) -> Any:
        seen.append(dict(request))
        return real(request, call_tool, **kwargs)

    monkeypatch.setattr(reverify_trigger, "run_reverification", spy)
    return seen


# ------------------------------------------------------------------- the aggregate call site


async def _aggregate(tmp_path: Path, kernel: DefensiveKernel | None, transport: Any = None):
    from iterate_harness.tools.base import ToolExecutionContext

    context = ToolExecutionContext(cwd=tmp_path)
    if kernel is not None:
        context.metadata[DEFENSIVE_KERNEL_KEY] = kernel
    if transport is not None:
        context.metadata[GLASSPANE_TRANSPORT_KEY] = transport
    result = await IterateReviewTool().execute(
        IterateReviewInput(operation="aggregate", rounds=[{"round": 1, "findings": [FINDING]}], max_review_rounds=2),
        context,
    )
    return result, context


async def test_a_failed_invariant_verdict_spawns_exactly_one_reverification_run(
    tmp_path: Path, sender: list[dict[str, Any]]
) -> None:
    """The gap this scenario names: the sender existed, and nobody called it."""
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    transport, asked = _evidence_transport(pack)

    result, context = await _aggregate(tmp_path, kernel, transport)
    assert result.is_error is False, result.output

    assert len(sender) == 1, f"one failing verdict must send exactly one run, saw {len(sender)}"
    request = sender[0]
    pinned = _pinned_request()
    assert request["schemaVersion"] == pinned["schemaVersion"]
    assert request["trigger"]["kind"] == pinned["trigger"]["kind"] == "invariant"
    digest = request["trigger"]["reportDigest"]
    assert len(digest) == 67 and digest.startswith("iv_") and digest[3:] == digest[3:].lower()

    # The re-verification calls the engine got are precisely the ones plan_calls produced for that
    # request. ④'s registry read rides the same channel and is filtered out here rather than folded
    # in: a read of what the other product *configured* must never be counted as an answer about
    # the app, and the assertion that keeps the two apart is what this line is for.
    expected, _deferred = plan_calls({"targets": request["targets"]})
    reverified = [call for call in asked if call[0] in READ_ONLY_TOOLS]
    assert reverified == expected, "the round must send the planned read-only calls, no more and none less"
    assert {tool for tool, _args in reverified} == {"gp_last_evidence", "gp_diagnose"}
    assert [call[0] for call in asked if call[0] in CONFIG_READ_TOOLS] == [REGISTRY_READ_TOOL], (
        "the recipe hand-off is exactly one registry read per round, on the read set that cannot "
        "move the app — any more than one is a channel the round does not own"
    )

    from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY

    published = context.metadata[ITERATE_STATE_KEY][REVERIFICATION_STATE_KEY]
    assert published == json.loads(result.output)[REVERIFICATION_STATE_KEY]
    assert published["attempted"] is True and published["verified"] is True
    # A refusal rode along with the evidence answer, so the run is not clean — relayed, not judged.
    assert published["clean"] is False
    assert len(read_entries(tmp_path)) == 2, "the seeded operation plus exactly one re-verification entry"


async def test_the_verdict_that_passes_never_reaches_the_sender(
    tmp_path: Path, sender: list[dict[str, Any]]
) -> None:
    """The control for the test above: the counter, not the outcome."""
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    transport, asked = _evidence_transport(pack)

    result, context = await _aggregate(tmp_path, kernel, transport)
    assert result.is_error is False
    assert sender == [], "a passing invariant report must not call run_reverification at all"
    # The control's claim is about the send, not about the socket: a pass spends no re-verification
    # call at all. What a pass does spend is ④'s single registry read for the recipe — a read of
    # configuration, which is why it is named here instead of being swallowed by a blanket "asked
    # nothing" that would have to be weakened the first time ④ grew a producer.
    assert [call for call in asked if call[0] != REGISTRY_READ_TOOL] == [], (
        "a passing report must not touch the engine with a re-verification call"
    )
    assert asked == [(REGISTRY_READ_TOOL, {})], "and exactly one registry read, not a per-target spray"
    # Two rows now, and the second one is the point: ④'s answer to a passing round used to live only
    # in this round's payload, so the two doors — which read the ledger — kept answering "no recipe
    # gate recorded yet" no matter how many rounds had run. Nothing was *sent* (asserted above);
    # what is recorded is the answer, not a send.
    assert len(read_entries(tmp_path)) == 2, "the seeded operation entry plus ④'s answer to the pass"

    from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY

    published = context.metadata[ITERATE_STATE_KEY][REVERIFICATION_STATE_KEY]
    assert published["spawned"] is False
    assert "passed" in published["reason"]
    gate = published[RECIPE_GATE_STATE_KEY]
    assert gate["allowed"] is False and gate["invariantState"] == "pass"
    assert gate["refusal"]["code"] == "RECIPE_E_READ", (
        "this transport answers the registry read with an evidence frame, which is not a "
        "{projects: [...]} document: ④ refuses the unreadable reply instead of reading it as "
        "'no recipe is configured' — the two sentences mean different things to whoever runs next"
    )


# ------------------------------------------- ④'s answer surviving a round that sent nothing


async def _passing_round(tmp_path: Path, sender: list[dict[str, Any]]) -> dict[str, Any]:
    """One passing round through the real aggregate call site; returns what it published."""
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    transport, _asked = _evidence_transport(pack)
    _result, context = await _aggregate(tmp_path, kernel, transport)

    from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY

    return context.metadata[ITERATE_STATE_KEY][REVERIFICATION_STATE_KEY]


async def test_a_passing_rounds_gate_answer_survives_the_round_it_was_made_in(
    tmp_path: Path, sender: list[dict[str, Any]]
) -> None:
    """The two doors read the ledger, and a pass used to leave nothing there for them to read.

    This is the named red for the defect: with the row unwritten, ``render_gate_state`` answers with
    its own absence line, so ``ih iterate gate`` reported "no recipe gate recorded yet" for a project
    that had already answered "may the recipe move now?" in every one of its passing rounds. The
    transient payload the round published is not a reader — it dies with the round.
    """
    await _passing_round(tmp_path, sender)
    text = render_gate_state(tmp_path)
    assert "no recipe gate recorded yet" not in text, text
    assert "round 1: recipe gate — not allowed" in text, text
    assert "invariant verdict: pass" in text, text


async def test_the_recorded_answer_is_a_gate_row_and_not_a_send(
    tmp_path: Path, sender: list[dict[str, Any]]
) -> None:
    """A gate row is filed under its own kind, says it sent nothing, and borrows no send's shape."""
    await _passing_round(tmp_path, sender)
    rows = read_entries(tmp_path)
    kinds = [entry.data.get("kind") for entry in rows]
    assert kinds.count(LEDGER_GATE_DATA_KIND) == 1, f"exactly one gate row per passing round: {kinds}"
    assert kinds.count(reverify_trigger.LEDGER_DATA_KIND) == 0, (
        "a round that sent nothing must not produce a reverification row"
    )
    data = [entry.data for entry in rows if entry.data.get("kind") == LEDGER_GATE_DATA_KIND]
    assert len(data) == 1, f"exactly one gate row, found {len(data)}"
    assert data[0]["spawned"] is False
    assert data[0]["invariantState"] == "pass"
    assert "passed" in str(data[0]["summary"]), data[0]["summary"]
    assert RECIPE_GATE_STATE_KEY in data[0]


async def test_a_gate_row_cannot_be_read_as_the_fix_it_is_talking_about(
    tmp_path: Path, sender: list[dict[str, Any]]
) -> None:
    """The shape of the row is a claim, so it gets pinned here rather than left to chance.

    ``fix_proof`` qualifies a line only with a passing outcome **and** an operation id. If the gate
    row ever grew either, the gate would open itself: the next round would cite the loop's own
    answer as evidence that a fix happened. Both halves are asserted, then the refusal is.
    """
    await _passing_round(tmp_path, sender)
    rows = [entry.data for entry in read_entries(tmp_path) if entry.data.get("kind") == LEDGER_GATE_DATA_KIND]
    assert len(rows) == 1, "the row under test is the one a passing round records"
    data = rows[0]
    assert "operationId" not in data, "a gate row must not carry the citation fix_proof looks for"
    assert "outcome" not in data, "nor the passing outcome it looks for"
    with pytest.raises(RecipeError) as raised:
        fix_proof(tmp_path, env={})
    assert raised.value.code == GATE_E_UNPROVEN


async def test_a_ledger_that_will_not_take_the_answer_does_not_cancel_the_round(
    tmp_path: Path,
    sender: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same error contract as the send: the round still publishes, and the failure is named in it."""

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk is full")

    monkeypatch.setattr(reverify_trigger, "append_entry", refuse)
    published = await _passing_round(tmp_path, sender)
    assert published["spawned"] is False, "the round's own answer is not lost with the ledger"
    assert "disk is full" in published["ledgerError"], published


async def test_no_verdict_sends_nothing_and_claims_nothing(
    tmp_path: Path, sender: list[dict[str, Any]]
) -> None:
    """No kernel in the metadata ⇒ no gate, no re-verification key, no statement at all."""
    pack = _real_pack()
    (tmp_path / "iterate.config.yaml").write_text(
        "reviewer:\n  evidence_validation: false\n", encoding="utf-8"
    )
    _record_an_operation(tmp_path, pack["operationId"])
    transport, asked = _evidence_transport(pack)

    result, context = await _aggregate(tmp_path, None, transport)
    assert result.is_error is False
    assert sender == [] and asked == []

    from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY

    state = context.metadata[ITERATE_STATE_KEY]
    for key in GATE_KEYS:
        assert key not in state, f"{key} must be absent, not None and not defaulted to a verdict"
        assert state.get(key) is None
    assert REVERIFICATION_STATE_KEY not in state, "an unmeasured verdict may not publish an answer"
    assert REVERIFICATION_STATE_KEY not in json.loads(result.output)
    assert trigger_reverification(
        project_root=tmp_path, metadata={}, gates={}, round_number=1, planned_dimensions=["security"]
    ) is None
    assert trigger_reverification(
        project_root=tmp_path,
        metadata={},
        gates={"evidence_complete": True},
        round_number=1,
        planned_dimensions=["security"],
    ) is None, "an evidence attestation is not an invariant verdict"


async def test_a_transport_that_raises_leaves_the_gates_exactly_as_measured(tmp_path: Path) -> None:
    """A dead channel is a stated state, never a converged one."""
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    gates = _gates(tmp_path, kernel)
    assert gates == {"invariant_state": "fail"}

    def call_tool(_tool: str, _arguments: dict[str, Any]) -> object:
        raise ConnectionError("glasspane-mcp exited")

    payload = trigger_reverification(
        project_root=tmp_path,
        metadata={**{DEFENSIVE_KERNEL_KEY: kernel, GLASSPANE_TRANSPORT_KEY: call_tool}},
        gates=gates,
        round_number=1,
        planned_dimensions=["security"],
    )
    assert payload is not None and payload["spawned"] is True
    assert payload["attempted"] is True
    assert payload["verified"] is False and payload["clean"] is False
    assert payload["answers"], "the failed call has to be named, not filed as silence"
    for answer in payload["answers"]:
        assert answer["code"] == "GP_E_CHANNEL"
        assert answer["remedy"]
    # The honesty rule proper: nothing here measures a gate or upgrades the verdict.
    assert gates == {"invariant_state": "fail"}, "the trigger mutated the gates it was handed"
    assert "pass" not in json.dumps(payload)


async def test_an_engine_refusal_reaches_the_round_output_with_its_remedy(tmp_path: Path) -> None:
    """Verbatim: the code and the remedy the shell wrote, not a re-decoded verdict."""
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    gates = _gates(tmp_path, kernel)

    def call_tool(tool: str, _arguments: dict[str, Any]) -> object:
        return ENGINE_REFUSAL if tool == "gp_diagnose" else json.dumps({"evidencePack": pack})

    result_payload = trigger_reverification(
        project_root=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel, GLASSPANE_TRANSPORT_KEY: call_tool},
        gates=gates,
        round_number=1,
        planned_dimensions=["security"],
    )
    assert result_payload is not None
    refused = [answer for answer in result_payload["answers"] if answer["state"] == "refused"]
    assert [answer["code"] for answer in refused] == ["GP_E_NOT_ATTACHED"]
    assert refused[0]["remedy"] == "call attach first"
    text = "\n".join(result_payload["lines"])
    assert "GP_E_NOT_ATTACHED" in text and "call attach first" in text


async def test_a_pack_the_provider_will_not_price_is_relayed_as_a_refusal(tmp_path: Path) -> None:
    """A pack that records nothing must not reach the round as "the engine could not tell".

    Whether it may be read at all is the provider contract's answer; this wiring only shows up
    in the red if it swallows that refusal, so the swallow has to be visible here too.
    """
    real = _real_pack()
    empty = {
        "schemaVersion": real["schemaVersion"],
        "operationId": real["operationId"],
        "attribution": None,
        "circuitBreaker": None,
        "signals": {},
    }
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    _record_an_operation(tmp_path, real["operationId"])
    gates = _gates(tmp_path, kernel)

    def call_tool(_tool: str, _arguments: dict[str, Any]) -> object:
        return json.dumps({"evidencePack": empty})

    payload = trigger_reverification(
        project_root=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel, GLASSPANE_TRANSPORT_KEY: call_tool},
        gates=gates,
        round_number=1,
        planned_dimensions=[],
    )
    assert payload is not None and payload["spawned"] is True
    assert payload["verified"] is False and payload["clean"] is False
    assert [answer["state"] for answer in payload["answers"]] == ["refused", "refused"]
    for answer in payload["answers"]:
        assert answer["code"] == "PROVIDER_E_METHOD"
        assert answer["remedy"], "the contract's remedy is the executable half of the refusal"
        assert answer["method"] is None


async def test_no_configured_transport_is_recorded_as_not_attempted(tmp_path: Path) -> None:
    """The host never handed over a synchronous sender: that is said, and no gate is touched."""
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    gates = _gates(tmp_path, kernel)

    payload = trigger_reverification(
        project_root=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel},
        gates=gates,
        round_number=1,
        planned_dimensions=["security"],
    )
    assert payload is not None
    assert payload["attempted"] is False and payload["verified"] is False
    assert payload["notAttemptedReason"]
    assert any("NOT attempted" in line for line in payload["lines"])
    assert gates == {"invariant_state": "fail"}


async def test_a_refused_ledger_write_is_reported_and_appends_zero_bytes(tmp_path: Path) -> None:
    """The round still completes, the failure is named, and not one byte reaches the log."""
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    transport, asked = _evidence_transport(pack)

    # The shape the TypeScript plugin still produces: a legacy record after the chain began.
    # The ledger refuses it rather than renumbering hashed bytes, and refuses this append with it.
    log_file = tmp_path / LOG_DIR / LOG_FILE
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"timestamp": "2026-08-14T00:00:01+00:00", "round": 2, "type": "report", "data": {}}
            )
            + "\n"
        )
    before = log_file.read_bytes()

    result, context = await _aggregate(tmp_path, kernel, transport)
    assert result.is_error is False, "a ledger that will not take the entry must not fail the round"
    assert asked, "the engine was really asked; the refusal is about the record, not the send"

    from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY

    payload = context.metadata[ITERATE_STATE_KEY][REVERIFICATION_STATE_KEY]
    assert "ledgerError" in payload
    assert "ITERATE_E_DECISION_LOG_INTERLEAVED" in payload["ledgerError"]
    assert payload["spawned"] is True, "the send itself is not undone by the ledger refusing it"
    assert log_file.read_bytes() == before
    assert hashlib.sha256(log_file.read_bytes()).hexdigest() == hashlib.sha256(before).hexdigest()


async def test_the_recorded_answer_carries_the_method_that_licensed_the_reading(
    tmp_path: Path,
) -> None:
    """Provenance survives to the round output and to the ledger entry."""
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    gates = _gates(tmp_path, kernel)
    transport, _asked = _evidence_transport(pack)

    payload = trigger_reverification(
        project_root=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel, GLASSPANE_TRANSPORT_KEY: transport},
        gates=gates,
        round_number=1,
        planned_dimensions=["security"],
    )
    assert payload is not None
    answered = [answer for answer in payload["answers"] if answer["state"] == "answered"]
    assert answered, "the corpus pack is a pack the registered provider prices"
    from iterate_harness.iterate.provider_producers import AE_METHOD_NAME

    assert [answer["method"] for answer in answered] == [AE_METHOD_NAME]
    assert any(f" via {AE_METHOD_NAME}" in line for line in payload["lines"])
    assert "ledgerError" not in payload

    entry = read_entries(tmp_path)[-1]
    assert entry.data["kind"] == "reverification"
    assert entry.data["digest"] == payload["digest"]
    recorded = [answer for answer in entry.data["answers"] if answer["state"] == "answered"]
    assert [answer["method"] for answer in recorded] == [AE_METHOD_NAME], (
        "the ledger entry is the audit trail; a method dropped here is provenance lost"
    )
    assert "outcome" not in entry.data and "passed" not in entry.data, (
        "the entry cites the engine's answers; it must not write a verdict of its own"
    )


async def test_a_request_with_nothing_to_ask_is_relayed_as_a_refusal(tmp_path: Path) -> None:
    """The contract refuses an empty target set; the loop relays that instead of sending."""
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    gates = _gates(tmp_path, kernel)
    assert gates == {"invariant_state": "fail"}

    payload = trigger_reverification(
        project_root=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel},
        gates=gates,
        round_number=1,
        planned_dimensions=[],
    )
    assert payload is not None
    assert payload["spawned"] is False
    assert payload["refusal"]["code"] == "INVARIANT_REPORT_E_TARGETS"
    assert payload["refusal"]["remedy"]


# --------------------------------------------------------------------- consumers of the state


async def test_compaction_survives_the_published_reverification(tmp_path: Path) -> None:
    """The state rides the channel compaction reads; an unverified run must not read as green."""
    from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
    from iterate_harness.services.compact import create_iterate_review_attachment_if_needed

    (tmp_path / "README.md").write_text("# real project\n", encoding="utf-8")
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    result, context = await _aggregate(tmp_path, kernel)
    assert result.is_error is False
    state = context.metadata[ITERATE_STATE_KEY]
    assert state[REVERIFICATION_STATE_KEY]["attempted"] is False

    attachment = create_iterate_review_attachment_if_needed(state)
    assert attachment is not None
    text = attachment.body
    assert "invariant_state=fail" in text, "the measured gate still travels across the boundary"
    assert "converged=True" not in text, "the reviewer's claim is never relayed as a kernel verdict"


def test_every_name_the_lazy_export_map_advertises_actually_resolves() -> None:
    """A string-based loader can advertise a name that no longer exists, and only a reader trips.

    Measured on 2026-10-25: ``MAX_REQUESTED_RECIPE_STEPS`` was still mapped (and still in ``__all__``)
    after the constant was renamed to ``MAX_REQUESTED_OPERATIONS``, so
    ``iterate_harness.iterate.MAX_REQUESTED_RECIPE_STEPS`` raised
    ``AttributeError: module 'iterate_harness.iterate.reverify_trigger' has no attribute …`` — with a
    fully green suite, because nothing in the suite imported it. Counting static imports cannot see
    these names, so the map needs its own reachability check: every entry resolves in the module it
    points at, and every name the package advertises answers.
    """
    import importlib

    from iterate_harness import iterate as iterate_pkg

    for name, target in iterate_pkg._EXPORTS.items():  # noqa: SLF001 — the map itself is the subject
        module = importlib.import_module(f"iterate_harness.iterate.{target.lstrip('.')}")
        assert hasattr(module, name), f"{name!r} is mapped to {target!r}, which does not define it"

    missing = [name for name in iterate_pkg.__all__ if not hasattr(iterate_pkg, name)]
    assert missing == [], f"advertised by __all__ but unresolvable: {missing}"


def test_the_new_names_resolve_through_the_lazy_export_map() -> None:
    """``iterate/__init__.py`` re-exports lazily; an unregistered name would be invisible."""
    from iterate_harness import iterate as iterate_pkg

    for name in (
        "GLASSPANE_TRANSPORT_KEY",
        "LEDGER_GATE_DATA_KIND",
        "REVERIFICATION_STATE_KEY",
        "record_gate_without_send",
        "record_reverification_run",
        "recorded_operation_ids",
        "reverification_targets",
        "trigger_reverification",
    ):
        assert getattr(iterate_pkg, name) is getattr(reverify_trigger, name)
        assert name in iterate_pkg.__all__


# ---------------------------------------------------------------- the parts-returning transport

#: The two side items the shipped shell sends beside the answer — the shapes pinned in
#: ``test_call_tool_parts.py`` / ``test_glasspane_reverification.py``: the ``update state:``
#: block (update-state.test.mjs) and the 2026-10-20 ``provider:`` attribution line.
UPDATE_BLOCK = (
    "update state: status=needs-consent current=1.3.1 latest=1.4.0"
    " updatedAt=2026-09-29T04:00:00.000Z autoUpdate=on\n"
    "ca trust bundle: this install has never recorded an export."
    " NOT the same as \"the bundle is fine\"."
)
PROVIDER_LINE = (
    "provider: glasspane-engine-ae version 0.1.9 method diagnose"
    " | declared prerequisites: evidencePack with at least one non-empty signal field"
)


async def test_a_parts_transport_reaches_the_round_with_its_annotations(tmp_path: Path) -> None:
    """The host contract this trigger now documents: a transport may answer with ``list[str]``.

    ``gp_last_evidence`` gets the pack frame plus the attribution; ``gp_diagnose`` gets the
    coded refusal plus the update block. Both answers must classify (this is what the joined
    transport used to die on) and both side items must surface in the round's lines exactly
    once, verbatim.
    """
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    gates = _gates(tmp_path, kernel)

    def call_tool_parts(tool: str, _arguments: dict[str, Any]) -> list[str]:
        if tool == "gp_last_evidence":
            return [json.dumps({"evidencePack": pack}), PROVIDER_LINE]
        return [ENGINE_REFUSAL, UPDATE_BLOCK]

    payload = trigger_reverification(
        project_root=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel, GLASSPANE_TRANSPORT_KEY: call_tool_parts},
        gates=gates,
        round_number=1,
        planned_dimensions=["security"],
    )
    assert payload is not None and payload["attempted"] is True
    states = {answer["tool"]: answer["state"] for answer in payload["answers"]}
    assert states == {"gp_last_evidence": "answered", "gp_diagnose": "refused"}, (
        "the parts reply must classify per item, never over a join"
    )
    by_tool = {answer["tool"]: answer for answer in payload["answers"]}
    assert by_tool["gp_last_evidence"]["annotations"] == [PROVIDER_LINE]
    assert by_tool["gp_diagnose"]["annotations"] == [UPDATE_BLOCK]
    assert by_tool["gp_diagnose"]["remedy"] == "call attach first"
    text = "\n".join(payload["lines"])
    assert text.count(PROVIDER_LINE) == 1 and text.count(UPDATE_BLOCK) == 1
    assert payload["verified"] is True and payload["clean"] is False

    # The ledger cites the annotations too — a side item that never reaches the record is a
    # fact about the call that the audit trail lost.
    entry = read_entries(tmp_path)[-1]
    recorded = {answer["tool"]: answer for answer in entry.data["answers"]}
    assert recorded["gp_last_evidence"]["annotations"] == [PROVIDER_LINE]
    assert recorded["gp_diagnose"]["annotations"] == [UPDATE_BLOCK]


async def test_a_str_transport_still_works_unchanged(tmp_path: Path) -> None:
    """Backwards surface: a host that only has the joined view keeps the old behaviour.

    One item per call, nothing annotated; a single frame or refusal classifies exactly as
    before this fix, and a joined multi-item blob is still honestly `unparseable`.
    """
    pack = _real_pack()
    kernel = await _project(tmp_path, ["README.md", "not-a-real-file.md"])
    _record_an_operation(tmp_path, pack["operationId"])
    gates = _gates(tmp_path, kernel)
    transport, asked = _evidence_transport(pack)

    payload = trigger_reverification(
        project_root=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel, GLASSPANE_TRANSPORT_KEY: transport},
        gates=gates,
        round_number=1,
        planned_dimensions=["security"],
    )
    assert payload is not None and asked
    assert all("annotations" not in answer for answer in payload["answers"])
    by_tool = {answer["tool"]: answer for answer in payload["answers"]}
    assert by_tool["gp_last_evidence"]["state"] == "answered"
    assert by_tool["gp_diagnose"]["state"] == "refused"
