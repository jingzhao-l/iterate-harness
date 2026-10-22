"""The host binds the synchronous read-only GlassPane transport — and every guard can fail.

Scenario ①'s last half: ``reverify_trigger`` documents rule 5 (the transport is the host's),
and ``glasspane_host_transport`` is that host. These cases drive the *real* chain —

    real DefensiveKernel with a really-failed invariant
      -> bind_host_transport(run's MCP client)
      -> trigger_reverification
      -> run_reverification
      -> relay_answer

against a stub MCP client whose replies are the shapes the shipped ``glasspane-mcp`` answers
with today (daemon frame + ``update state:`` block + ``provider:`` attribution line, pinned in
``tests/test_mcp/test_call_tool_parts.py``).

Two of these cases cannot be written in one shape, and that IS the re-entrancy decision: the
no-running-loop path is a plain ``def`` test (pytest-asyncio gives it no loop, so the synchronous
transport really drives its coroutine), and the already-inside-a-loop path is an ``async def``
test — which is where the production publish site sits, ``engine/query.py`` awaiting
``tool.execute`` on the loop that connected the MCP sessions. The first version of this file
asserted that the second case reads ``GP_E_HOST_LOOP_BUSY``, and that assertion was the defect
recorded as expected behaviour: every real round refused, so the accepting branch never ran. It
now asserts the awaited transport sends and the engine's answers arrive, while the synchronous
transport keeps its named refusal for a host that has only ever that.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY, DefensiveKernel
from iterate_harness.iterate import reverify_trigger
from iterate_harness.iterate.decision_log import LOG_DIR, LOG_FILE, append_entry, make_entry, read_entries
from iterate_harness.iterate.glasspane_host_transport import (
    GLASSPANE_ACALL_KEY,
    GLASSPANE_SERVER_NAME_KEY,
    LOOP_BUSY_CODE,
    MCP_MANAGER_METADATA_KEY,
    WRITE_TOOL_CODE,
    bind_host_transport,
    find_glasspane_server,
    host_transport,
    make_host_acall,
    make_host_transport,
)
from iterate_harness.iterate.glasspane_reverification import (
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    relay_answer,
    run_reverification,
)
from iterate_harness.iterate.loop_gates import GATE_KEYS, measured_gates
from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
from iterate_harness.iterate.reverify_trigger import (
    GLASSPANE_ACALL_KEY as ACALL_METADATA_KEY,
    GLASSPANE_TRANSPORT_ABSENT_KEY,
    GLASSPANE_TRANSPORT_KEY,
    REVERIFICATION_STATE_KEY,
    trigger_reverification,
    trigger_reverification_async,
)
from iterate_harness.mcp.client import McpServerNotConnectedError, McpServerTimeoutError
from iterate_harness.mcp.types import McpToolInfo

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"

#: The shipped three-item ``gp_diagnose`` reply, verbatim from test_call_tool_parts.py.
DAEMON_FRAME = json.dumps(
    {"operationId": "op_20261021_0001", "diagnosis": {"class": "T0", "report": "the GUI claim still does not hold"}}
)
UPDATE_BLOCK = (
    "update state: status=needs-consent current=1.3.1 latest=1.4.0"
    " updatedAt=2026-09-29T04:00:00.000Z autoUpdate=on\n"
    'ca trust bundle: this install has never recorded an export. NOT the same as "the bundle is fine".'
)
PROVIDER_LINE = (
    "provider: glasspane-engine-ae version 0.1.9 method diagnose"
    " | declared prerequisites: evidencePack with at least one non-empty signal field"
)

SERVER = "glasspane-mcp"

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


class _StatusRow:
    """Attribute-shaped row: what ``McpClientManager.list_statuses()`` really returns."""

    def __init__(self, name: str, state: str, tools: tuple[str, ...], detail: str) -> None:
        self.name = name
        self.state = state
        self.detail = detail
        self.transport = "stdio"
        self.tools = [
            McpToolInfo(server_name=name, name=tool_name, description="", input_schema={})
            for tool_name in tools
        ]
        self.resources: list[Any] = []


def _real_pack() -> dict[str, Any]:
    corpus = json.loads((CORPUS / "evidence-decision.ok-01.json").read_text(encoding="utf-8"))
    for case in corpus["cases"]:
        candidate = case.get("input")
        if isinstance(candidate, dict) and isinstance(candidate.get("operationId"), str):
            return candidate
    raise AssertionError("the corpus carries no pack; nothing here would be production-shaped")


def _reply_for(pack: dict[str, Any]) -> Any:
    """The shipped reply per tool: a pack frame + attribution; a diagnosis frame + both side items."""

    def parts(tool: str) -> list[str]:
        if tool == "gp_last_evidence":
            return [json.dumps({"evidencePack": pack}), PROVIDER_LINE]
        return [DAEMON_FRAME, UPDATE_BLOCK, PROVIDER_LINE]

    return parts


class StubClient:
    """The minimum an ``McpClientManager`` must offer: statuses, parts, and the joined view.

    ``call_tool`` is present and booby-trapped: a transport that consumes it instead of
    ``call_tool_parts`` destroys the item boundary, and the trap makes that visible as a
    failure rather than as an answer that happens to parse.
    """

    def __init__(
        self,
        parts: Any = None,
        *,
        error: BaseException | None = None,
        state: str = "connected",
        tools: tuple[str, ...] = READ_ONLY_TOOLS,
        delay: float = 0.0,
    ) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []
        self.joined_calls: list[str] = []
        self._parts = parts
        self._error = error
        self._state = state
        self._tools = tools
        self._delay = delay

    def list_statuses(self) -> list[Any]:
        return [_StatusRow(SERVER, self._state, self._tools, "process exited")]

    async def call_tool_parts(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> list[str]:
        self.frames.append((tool_name, dict(arguments)))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        items = self._parts(tool_name) if callable(self._parts) else self._parts
        return list(items or [])

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        self.joined_calls.append(tool_name)
        raise AssertionError("the re-verification transport must consume call_tool_parts, never the join")

    def joined(self, tool_name: str) -> str:
        """What ``call_tool`` would have returned — built here so a test can name the defect."""
        items = self._parts(tool_name) if callable(self._parts) else self._parts
        return "\n".join(list(items or []))




async def _failing_kernel(tmp_path: Path) -> DefensiveKernel:
    """A real kernel that really measured a failed invariant for this project."""
    (tmp_path / "README.md").write_text("# real project\n", encoding="utf-8")
    (tmp_path / "iterate.config.yaml").write_text(
        "invariants:\n  ensure:\n    - README.md\n    - not-a-real-file.md\n"
        "reviewer:\n  evidence_validation: false\n",
        encoding="utf-8",
    )
    from iterate_harness.iterate.config_loader import load_effective_config

    kernel = DefensiveKernel(tmp_path, load_effective_config(tmp_path))
    await kernel.check_invariants()
    assert kernel.last_invariant_report is not None
    return kernel


def _run_kernel(tmp_path: Path) -> DefensiveKernel:
    """The same kernel, built from a synchronous test body (which owns no loop)."""
    return asyncio.run(_failing_kernel(tmp_path))


def _gates(tmp_path: Path, kernel: DefensiveKernel) -> dict[str, object]:
    return measured_gates(
        evidence_validation=False,
        findings=[],
        cwd=tmp_path,
        metadata={DEFENSIVE_KERNEL_KEY: kernel},
    )


def _record_an_operation(tmp_path: Path, operation_id: str) -> None:
    append_entry(
        tmp_path,
        make_entry(
            entry_type="decision",
            round_number=1,
            data={"kind": "evidence", "operationId": operation_id, "summary": "engine recorded"},
        ),
    )


def _log_bytes(tmp_path: Path) -> bytes:
    log_file = tmp_path / LOG_DIR / LOG_FILE
    return log_file.read_bytes() if log_file.exists() else b""


def _ledger_entry_count(tmp_path: Path) -> int:
    return len(read_entries(tmp_path))


def _send(tmp_path: Path, kernel: DefensiveKernel, metadata: dict[str, Any]) -> dict[str, Any] | None:
    """The trigger, fed exactly what the publish site would feed it after the host binding."""
    bound = {**metadata, **bind_host_transport(metadata)}
    return trigger_reverification(
        project_root=tmp_path,
        metadata=bound,
        gates=_gates(tmp_path, kernel),
        round_number=1,
        planned_dimensions=["security"],
    )


# ------------------------------------------------------------------ the send that really works


def test_a_bound_host_transport_sends_and_the_frame_classifies_answered(tmp_path: Path) -> None:
    """No running loop ⇒ the host drives the coroutine; the shipped reply relays answered."""
    pack = _real_pack()
    kernel = _run_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    client = StubClient(parts=_reply_for(pack))

    payload = _send(tmp_path, kernel, {DEFENSIVE_KERNEL_KEY: kernel, MCP_MANAGER_METADATA_KEY: client})
    assert payload is not None
    assert payload["attempted"] is True, "a bound host transport is an attempted send, not a stated absence"
    assert payload["verified"] is True

    by_tool = {answer["tool"]: answer for answer in payload["answers"]}
    assert by_tool["gp_diagnose"]["state"] == "answered", (
        "the daemon frame alone must classify; a joined reply measured unparseable"
    )
    assert by_tool["gp_diagnose"]["outcome"] == "T0"
    assert by_tool["gp_last_evidence"]["state"] == "answered"
    assert client.joined_calls == [], "the joined view was used; the boundary is gone"


def test_both_side_items_survive_as_annotations(tmp_path: Path) -> None:
    """The boundary fix is the point: update block and provider line reach the round verbatim."""
    pack = _real_pack()
    kernel = _run_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    client = StubClient(parts=_reply_for(pack))

    payload = _send(tmp_path, kernel, {DEFENSIVE_KERNEL_KEY: kernel, MCP_MANAGER_METADATA_KEY: client})
    assert payload is not None
    by_tool = {answer["tool"]: answer for answer in payload["answers"]}
    assert by_tool["gp_diagnose"]["annotations"] == [UPDATE_BLOCK, PROVIDER_LINE]
    assert by_tool["gp_last_evidence"]["annotations"] == [PROVIDER_LINE]
    text = "\n".join(payload["lines"])
    assert text.count(UPDATE_BLOCK) == 1 and text.count(PROVIDER_LINE) == 2

    # Control for the whole claim: the same bytes joined are unreadable, so an answered
    # frame here is proof that relay_answer got a list, not call_tool's join.
    assert relay_answer("gp_diagnose", {}, client.joined("gp_diagnose")).state == "unparseable"


def test_the_ledger_gains_exactly_one_attributed_entry_on_a_real_send(tmp_path: Path) -> None:
    pack = _real_pack()
    kernel = _run_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    client = StubClient(parts=_reply_for(pack))
    before = _log_bytes(tmp_path)

    payload = _send(tmp_path, kernel, {DEFENSIVE_KERNEL_KEY: kernel, MCP_MANAGER_METADATA_KEY: client})
    assert payload is not None and "ledgerError" not in payload

    entries = read_entries(tmp_path)
    assert len(entries) == 2, "the seeded operation plus exactly one re-verification entry"
    entry = entries[-1]
    assert entry.data["kind"] == "reverification"
    assert entry.data["attempted"] is True
    assert entry.data["digest"] == payload["digest"]
    assert [answer["tool"] for answer in entry.data["answers"]] == ["gp_last_evidence", "gp_diagnose"]
    recorded = {answer["tool"]: answer for answer in entry.data["answers"]}
    assert recorded["gp_diagnose"]["annotations"] == [UPDATE_BLOCK, PROVIDER_LINE]
    assert _log_bytes(tmp_path).startswith(before), "the ledger is append-only; a rewrite is not a record"


# -------------------------------------------------------------------- read-only by construction


@pytest.mark.parametrize("tool", [*MUTATING_TOOLS, "gp_teleport", "mcp__glasspane__gp_act"])
def test_a_write_tool_is_refused_before_anything_is_sent(tool: str) -> None:
    """Zero frames on the wire — the refusal is the guard, and a stub proves it."""
    client = StubClient(parts=[DAEMON_FRAME])
    transport = make_host_transport(client, SERVER)

    raw = transport(tool, {"target": "the user's app"})
    assert isinstance(raw, str) and raw.startswith(f"{WRITE_TOOL_CODE}:"), raw
    answer = relay_answer(tool, {}, raw)
    assert answer.state == "refused" and answer.code == WRITE_TOOL_CODE
    assert answer.remedy and ", ".join(READ_ONLY_TOOLS) in answer.remedy
    assert client.frames == [], f"{tool} reached the session; the read-only check is decorative"


def test_the_read_only_set_is_imported_not_restated() -> None:
    """A transport with its own copy of the tool names would drift from the contract silently."""
    from iterate_harness.iterate import glasspane_host_transport

    assert glasspane_host_transport.READ_ONLY_TOOLS is READ_ONLY_TOOLS


# --------------------------------------------------------------------- the re-entrancy decision


async def test_inside_a_running_loop_the_send_is_refused_and_no_gate_is_measured(tmp_path: Path) -> None:
    """Case (b): the publish site runs in the session's loop, so the host must refuse out loud."""
    pack = _real_pack()
    kernel = await _failing_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    client = StubClient(parts=_reply_for(pack))
    gates = _gates(tmp_path, kernel)
    assert gates == {"invariant_state": "fail"}

    payload = _send(tmp_path, kernel, {DEFENSIVE_KERNEL_KEY: kernel, MCP_MANAGER_METADATA_KEY: client})
    assert payload is not None
    assert payload["attempted"] is True, "a refusal is a stated answer about our own action"
    assert payload["verified"] is False and payload["clean"] is False
    assert payload["answers"], "every planned call has to be named, not filed as silence"
    for answer in payload["answers"]:
        assert answer["state"] == "refused"
        assert answer["code"] == LOOP_BUSY_CODE
        assert answer["remedy"], "the remedy is the half an agent can execute"
        assert "run_reverification_async" in answer["remedy"] or "stdio session" in answer["remedy"]
    assert client.frames == [], "the loop-busy path must not create the coroutine, let alone run it"

    # The honesty rule proper, asserted on state rather than on the absence of a crash: a
    # refusal measures no gate, so the published round carries exactly the gates loop_gates
    # produced and nothing else.
    published: dict[str, Any] = dict(gates)
    published[REVERIFICATION_STATE_KEY] = payload
    assert set(published) & set(GATE_KEYS) == {"invariant_state"}
    assert published["invariant_state"] == "fail"
    assert set(payload) & set(GATE_KEYS) == set(), "a re-verification may not publish a gate"
    # The shipped ledger rule is unchanged: a spawned send is recorded. What must be true of
    # that record is that it cites the refusal instead of inventing an answer.
    assert _ledger_entry_count(tmp_path) == 2, "the seeded operation plus this one stated refusal"
    entry = read_entries(tmp_path)[-1]
    assert entry.data["kind"] == "reverification" and entry.data["attempted"] is True
    assert entry.data["verified"] is False and entry.data["clean"] is False
    assert {answer["code"] for answer in entry.data["answers"]} == {LOOP_BUSY_CODE}
    assert all("outcome" not in answer for answer in entry.data["answers"]), (
        "a send the host refused records no engine outcome"
    )


def test_a_refusal_path_writes_zero_ledger_bytes(tmp_path: Path) -> None:
    """Requirement C: the trigger's own refusals record nothing — not even a line.

    Two refusal paths reach the round before any send exists: the contract's refusal of an
    empty target set, and the absence of a measured verdict. Both must leave the ledger
    byte-identical, or a decision that was never made becomes an audit entry.
    """
    kernel = _run_kernel(tmp_path)
    before = _log_bytes(tmp_path)
    metadata = {DEFENSIVE_KERNEL_KEY: kernel, MCP_MANAGER_METADATA_KEY: StubClient()}

    # No recorded operation and no planned dimension: the contract refuses to build a request.
    payload = trigger_reverification(
        project_root=tmp_path,
        metadata=metadata,
        gates={"invariant_state": "fail"},
        round_number=1,
        planned_dimensions=[],
    )
    assert payload is not None and payload["spawned"] is False
    assert payload["refusal"]["code"] == "INVARIANT_REPORT_E_TARGETS"
    assert payload["refusal"]["remedy"]
    assert _log_bytes(tmp_path) == before, "a refused request is not a recorded decision"
    assert hashlib.sha256(_log_bytes(tmp_path)).hexdigest() == hashlib.sha256(before).hexdigest()

    # And rule 1: no verdict at all publishes nothing at all.
    assert trigger_reverification(
        project_root=tmp_path,
        metadata=metadata,
        gates={},
        round_number=1,
        planned_dimensions=["security"],
    ) is None
    assert _log_bytes(tmp_path) == before, "no verdict means no words and no bytes"


def test_no_running_loop_drives_the_coroutine_to_completion() -> None:
    """Case (a) in isolation: the same transport, on a thread that owns no loop, really sends."""
    pack = _real_pack()
    client = StubClient(parts=_reply_for(pack))
    transport = make_host_transport(client, SERVER)

    raw = transport("gp_diagnose", {"operationId": pack["operationId"]})
    assert raw == [DAEMON_FRAME, UPDATE_BLOCK, PROVIDER_LINE]
    assert isinstance(raw, list) and all(isinstance(item, str) for item in raw)
    assert client.frames == [("gp_diagnose", {"operationId": pack["operationId"]})]


def test_a_transport_that_raises_is_relayed_as_the_channel_refusal(tmp_path: Path) -> None:
    """Requirement 4: 'could not ask' keeps GP_E_CHANNEL and its remedy, never an empty answer."""
    pack = _real_pack()
    kernel = _run_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    error = McpServerNotConnectedError(f"MCP server '{SERVER}' is not connected: process exited")
    client = StubClient(error=error)
    before = _log_bytes(tmp_path)

    payload = trigger_reverification(
        project_root=tmp_path,
        metadata={
            DEFENSIVE_KERNEL_KEY: kernel,
            GLASSPANE_TRANSPORT_KEY: make_host_transport(client, SERVER),
        },
        gates=_gates(tmp_path, kernel),
        round_number=1,
        planned_dimensions=["security"],
    )
    assert payload is not None and payload["attempted"] is True
    assert payload["verified"] is False and payload["clean"] is False
    for answer in payload["answers"]:
        assert answer["state"] == "refused" and answer["code"] == "GP_E_CHANNEL"
        assert "process exited" in answer["message"]
        assert answer["remedy"] == (
            "check that glasspane-mcp is running and that the harness's mcp config points at it"
        )
    assert client.frames, "the call was sent; the failure is the transport's, not our silence"
    assert _log_bytes(tmp_path) != before, "a real send with a relayed transport failure is recorded"


def test_the_host_timeout_keeps_the_clients_timeout_shape(tmp_path: Path) -> None:
    """The backstop window fires as McpServerTimeoutError and the sender relays it, not silence."""
    client = StubClient(parts=[DAEMON_FRAME], delay=0.30)
    transport = make_host_transport(client, SERVER, timeout=0.01)

    with pytest.raises(McpServerTimeoutError):
        transport("gp_diagnose", {"operationId": "op_1"})
    assert client.frames == [("gp_diagnose", {"operationId": "op_1"})]

    request = {"trigger": {"reportDigest": "iv_testdigest_test"}, "targets": {"operationIds": ["op_1"]}}

    def flaky(_tool: str, _arguments: dict[str, Any]) -> object:
        raise McpServerTimeoutError("MCP call tool 'gp_diagnose' timed out after 0.01s")

    run = run_reverification(request, flaky)
    assert run.attempted is True and run.any_verified is False and run.all_clean is False
    assert all(answer.code == "GP_E_CHANNEL" and answer.remedy for answer in run.answers)
    assert not any(answer.state == "unparseable" for answer in run.answers), (
        "a timeout is a stated transport failure, never an empty answer"
    )


# ----------------------------------------------------------------------- the stated absence


def test_no_mcp_server_configured_reads_as_not_attempted_because(tmp_path: Path) -> None:
    """Requirement B: the wiring itself is observable, not silence and not an empty success."""
    pack = _real_pack()
    kernel = _run_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    client = StubClient(parts=_reply_for(pack), tools=("some_other_tool",), state="pending")

    metadata = {DEFENSIVE_KERNEL_KEY: kernel, MCP_MANAGER_METADATA_KEY: client}
    additions = bind_host_transport(metadata)
    assert GLASSPANE_TRANSPORT_KEY not in additions
    reason = additions[GLASSPANE_TRANSPORT_ABSENT_KEY]
    assert "advertises a GlassPane read-only tool" in reason

    payload = _send(tmp_path, kernel, metadata)
    assert payload is not None
    assert payload["attempted"] is False
    assert payload["notAttemptedReason"] == reason
    assert any("NOT attempted" in line for line in payload["lines"])
    assert payload["verified"] is False and payload["clean"] is False
    assert payload["spawned"] is True, "the request was real; only the send was not attempted"
    assert set(payload) & set(GATE_KEYS) == set(), "a not-attempted send may not publish a gate"
    assert _ledger_entry_count(tmp_path) == 2, "the seeded operation plus one stated not-attempted"
    entry = read_entries(tmp_path)[-1]
    assert entry.data["attempted"] is False and entry.data["notAttemptedReason"] == reason
    assert entry.data["verified"] is False and entry.data["clean"] is False
    assert entry.data["answers"] == [], "a send that never happened records no answers"


def test_a_configured_but_unconnected_server_names_itself(tmp_path: Path) -> None:
    pack = _real_pack()
    kernel = _run_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    client = StubClient(parts=_reply_for(pack), state="failed")
    payload = _send(tmp_path, kernel, {DEFENSIVE_KERNEL_KEY: kernel, MCP_MANAGER_METADATA_KEY: client})
    assert payload is not None and payload["attempted"] is False
    reason = payload["notAttemptedReason"]
    assert SERVER in reason and "failed" in reason
    assert _ledger_entry_count(tmp_path) == 2, "the send was not attempted, and that is what is recorded"
    assert read_entries(tmp_path)[-1].data["answers"] == []


def test_a_run_with_no_manager_at_all_stays_on_the_default_reason(tmp_path: Path) -> None:
    """No mcp plane ⇒ nothing bound and nothing added: the sender's default is already true."""
    pack = _real_pack()
    kernel = _run_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    metadata = {DEFENSIVE_KERNEL_KEY: kernel}
    assert bind_host_transport(metadata) == {}
    payload = _send(tmp_path, kernel, metadata)
    assert payload is not None and payload["attempted"] is False
    assert "no GlassPane MCP channel is configured" in payload["notAttemptedReason"]
    assert _ledger_entry_count(tmp_path) == 2


def test_an_explicitly_bound_transport_wins_and_is_left_alone(tmp_path: Path) -> None:
    """A host that already bound its own transport is not overwritten — the seam is additive."""
    pack = _real_pack()
    kernel = _run_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    seen: list[str] = []

    def host_tool(tool: str, _arguments: dict[str, Any]) -> object:
        seen.append(tool)
        return [DAEMON_FRAME]

    metadata = {
        DEFENSIVE_KERNEL_KEY: kernel,
        GLASSPANE_TRANSPORT_KEY: host_tool,
        MCP_MANAGER_METADATA_KEY: StubClient(parts=[DAEMON_FRAME]),
    }
    assert bind_host_transport(metadata) == {}
    payload = _send(tmp_path, kernel, metadata)
    assert payload is not None and payload["attempted"] is True
    assert seen == ["gp_last_evidence", "gp_diagnose"], "the host's own transport must be the one called"
    assert metadata[MCP_MANAGER_METADATA_KEY].frames == [], (
        "the run's client was driven anyway; a bound transport must not double-send"
    )


# --------------------------------------------------------------------- discovery + export wiring


def test_find_glasspane_server_discovery_is_honest_in_every_case() -> None:
    connected, reason = find_glasspane_server(StubClient(parts=[DAEMON_FRAME]))
    assert (connected, reason) == (SERVER, "")
    assert host_transport({MCP_MANAGER_METADATA_KEY: StubClient(parts=[DAEMON_FRAME])})[1] is None

    absent_name, absent_reason = find_glasspane_server(StubClient(parts=[DAEMON_FRAME]), "not-configured")
    assert absent_name is None and "not-configured" in absent_reason

    override, _ = find_glasspane_server(StubClient(parts=[DAEMON_FRAME]), SERVER)
    assert override == SERVER

    no_manager, manager_reason = find_glasspane_server(None)
    assert no_manager is None and MCP_MANAGER_METADATA_KEY in manager_reason

    not_a_manager, why = find_glasspane_server(object())
    assert not_a_manager is None and "not an MCP client manager" in why

    # A metadata override is honoured by the binding seam, not only by discovery.
    transport, reason = host_transport(
        {MCP_MANAGER_METADATA_KEY: StubClient(parts=[DAEMON_FRAME]), GLASSPANE_SERVER_NAME_KEY: SERVER}
    )
    assert transport is not None and reason is None


def test_the_new_names_resolve_through_the_lazy_export_map() -> None:
    from iterate_harness import iterate as iterate_pkg
    from iterate_harness.iterate import glasspane_host_transport

    for name in (
        "GLASSPANE_SERVER_NAME_KEY",
        "HOST_TRANSPORT_TIMEOUT_SECONDS",
        "MCP_MANAGER_METADATA_KEY",
        "bind_host_transport",
        "find_glasspane_server",
        "host_transport",
        "make_host_transport",
    ):
        assert getattr(iterate_pkg, name) is getattr(glasspane_host_transport, name)
        assert name in iterate_pkg.__all__
    assert iterate_pkg.GLASSPANE_TRANSPORT_ABSENT_KEY is reverify_trigger.GLASSPANE_TRANSPORT_ABSENT_KEY
    assert "GLASSPANE_TRANSPORT_ABSENT_KEY" in iterate_pkg.__all__


def test_the_host_transport_is_reachable_from_the_publish_site_metadata(tmp_path: Path) -> None:
    """End-to-end through the tool: the aggregate binds the transport from context.metadata."""
    from iterate_harness.tools.base import ToolExecutionContext
    from iterate_harness.tools.iterate_tools import IterateReviewInput, IterateReviewTool

    pack = _real_pack()

    async def drive() -> tuple[Any, Any]:
        kernel = await _failing_kernel(tmp_path)
        _record_an_operation(tmp_path, pack["operationId"])
        context = ToolExecutionContext(
            cwd=tmp_path,
            metadata={
                DEFENSIVE_KERNEL_KEY: kernel,
                MCP_MANAGER_METADATA_KEY: StubClient(parts=_reply_for(pack)),
            },
        )
        result = await IterateReviewTool().execute(
            IterateReviewInput(
                operation="aggregate",
                rounds=[{"round": 1, "findings": [FINDING]}],
                max_review_rounds=2,
            ),
            context,
        )
        return result, context

    result, context = asyncio.run(drive())
    assert result.is_error is False, result.output
    state = context.metadata[ITERATE_STATE_KEY]
    published = state[REVERIFICATION_STATE_KEY]
    client = context.metadata[MCP_MANAGER_METADATA_KEY]
    # Inside asyncio.run the loop IS running — re-entrancy case (b), asserted where production
    # actually sits. This is the case that used to read LOOP_BUSY for every round: the awaiting
    # publish site sends on the loop that owns the session, so the engine's answers arrive.
    assert published["attempted"] is True
    assert published["notAttemptedReason"] is None
    assert {answer["code"] for answer in published["answers"]} == {None}, (
        "a host-side refusal relayed as an answer is not a re-verification; the publish site "
        "must reach the engine, and this is the one site that decides whether it can"
    )
    assert [answer["tool"] for answer in published["answers"]] == ["gp_last_evidence", "gp_diagnose"]
    assert [answer["state"] for answer in published["answers"]] == ["answered", "answered"]
    # The frames went through the item-preserving path, never the join.
    assert [tool for tool, _ in client.frames] == ["gp_last_evidence", "gp_diagnose"]
    assert client.joined_calls == []
    diagnose = next(answer for answer in published["answers"] if answer["tool"] == "gp_diagnose")
    assert UPDATE_BLOCK in diagnose["annotations"] and PROVIDER_LINE in diagnose["annotations"]
    # The judgement stays the engine's: ``verified`` is relayed from an answered state, ``clean``
    # is not earned by a diagnosis that says the claim still does not hold.
    assert published["verified"] is True
    assert published["clean"] is False
    # One ledger line for one send, written by the awaited path.
    assert _ledger_entry_count(tmp_path) == 2
    assert GLASSPANE_TRANSPORT_KEY not in state and GLASSPANE_ACALL_KEY not in state
    assert GLASSPANE_TRANSPORT_KEY not in context.metadata[ITERATE_STATE_KEY]
    assert GLASSPANE_ACALL_KEY not in context.metadata[ITERATE_STATE_KEY], (
        "a bound callable must not be published into the round's durable state"
    )
    assert set(state) & set(GATE_KEYS) == {"invariant_state"}
    assert state["invariant_state"] == "fail", "a relayed engine answer does not upgrade the measured verdict"


# ----------------------------------------------------- the awaitable transport (the production path)


class _LoopPinningClient(StubClient):
    """A client whose sessions belong to the loop that connected it, exactly like the run's.

    ``ui/runtime.py`` builds the ``McpClientManager`` and ``await connect_all()`` on the engine
    loop, and ``engine/query.py`` awaits the tool on that same loop. So the only loop that can
    drive these sessions is the one already running at the publish site — and a transport that
    awaited on a *different* loop would be a bug this class turns into a failed assertion rather
    than a hang.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.loop_at_connect = asyncio.get_running_loop()
        self.loop_at_call: asyncio.AbstractEventLoop | None = None

    async def call_tool_parts(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> list[str]:
        self.loop_at_call = asyncio.get_running_loop()
        return await super().call_tool_parts(server_name, tool_name, arguments)


def test_the_publish_site_binds_both_transports_and_awaits_one() -> None:
    """One discovery pass binds the sync transport for a loop-free host and the acall for this one."""

    async def drive() -> dict[str, Any]:
        return bind_host_transport({MCP_MANAGER_METADATA_KEY: StubClient(parts=[DAEMON_FRAME])})

    additions = asyncio.run(drive())
    assert set(additions) == {GLASSPANE_TRANSPORT_KEY, ACALL_METADATA_KEY}
    assert asyncio.iscoroutinefunction(additions[GLASSPANE_TRANSPORT_KEY]) is False
    assert asyncio.iscoroutinefunction(additions[ACALL_METADATA_KEY]) is True


async def test_the_acall_awaits_on_the_loop_that_owns_the_sessions(tmp_path: Path) -> None:
    """The awaited send runs on the connected loop, so the engine's answers reach the relay."""
    pack = _real_pack()
    client = _LoopPinningClient(parts=_reply_for(pack))
    kernel = await _failing_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    metadata = {DEFENSIVE_KERNEL_KEY: kernel, MCP_MANAGER_METADATA_KEY: client}
    bound = {**metadata, **bind_host_transport(metadata)}

    published = await trigger_reverification_async(
        project_root=tmp_path,
        metadata=bound,
        gates=_gates(tmp_path, kernel),
        round_number=1,
        planned_dimensions=["security"],
    )

    assert published is not None
    assert client.loop_at_call is client.loop_at_connect, (
        "driving those sessions from another loop is what GP_E_HOST_LOOP_BUSY exists to refuse"
    )
    assert published["attempted"] is True and published["verified"] is True
    assert {answer["code"] for answer in published["answers"]} == {None}


async def test_the_acall_refuses_a_write_tool_before_the_session_is_touched() -> None:
    """Deny-by-default survives the awaitable path: an unlisted name never reaches the client."""
    client = StubClient(parts=[DAEMON_FRAME])
    acall = make_host_acall(client, SERVER)

    async def drive() -> object:
        return await acall("gp_act", {"action": "click"})

    answer = await drive()
    assert isinstance(answer, str) and answer.startswith(WRITE_TOOL_CODE)
    assert client.frames == []


async def test_the_acall_carries_the_item_boundary_to_the_relay(tmp_path: Path) -> None:
    """A three-item reply relays as one answered frame plus two verbatim annotations."""
    pack = _real_pack()
    client = StubClient(parts=_reply_for(pack))
    acall = make_host_acall(client, SERVER)

    async def drive() -> object:
        return await acall("gp_diagnose", {"operationId": pack["operationId"]})

    parts = await drive()
    assert isinstance(parts, list) and len(parts) == 3
    answer = relay_answer("gp_diagnose", {"operationId": pack["operationId"]}, parts)
    assert answer.state == "answered"
    assert list(answer.annotations) == [UPDATE_BLOCK, PROVIDER_LINE]
    assert client.joined_calls == []


def test_the_sync_trigger_does_not_read_the_acall_as_a_transport(tmp_path: Path) -> None:
    """An awaitable handed over under the *synchronous* key is not sent through synchronously.

    This is the mis-wiring the filter exists for: a host that binds a coroutine function under
    ``GLASSPANE_TRANSPORT_KEY`` would otherwise have it called, and the sender would answer with
    ``GP_E_ASYNC_HOST`` — a refusal that arrives in the relayed shape and reads like an engine
    reply. The sync trigger must report "no synchronous transport was handed over" instead.
    """
    pack = _real_pack()
    kernel = _run_kernel(tmp_path)
    client = StubClient(parts=_reply_for(pack))
    metadata = {
        DEFENSIVE_KERNEL_KEY: kernel,
        GLASSPANE_TRANSPORT_KEY: make_host_acall(client, SERVER),
    }

    published = trigger_reverification(
        project_root=tmp_path,
        metadata=metadata,
        gates=_gates(tmp_path, kernel),
        round_number=1,
        planned_dimensions=["security"],
    )

    assert published is not None
    assert published["attempted"] is False
    assert published["answers"] == []
    assert client.frames == []
    assert "no GlassPane MCP channel" in published["notAttemptedReason"]


async def test_the_async_trigger_falls_back_to_a_host_bound_sync_transport(tmp_path: Path) -> None:
    """A host that binds only the synchronous transport still gets sent — through that transport."""
    pack = _real_pack()
    kernel = await _failing_kernel(tmp_path)
    _record_an_operation(tmp_path, pack["operationId"])
    client = StubClient(parts=_reply_for(pack))
    sent: list[str] = []

    def sync_transport(tool: str, arguments: dict[str, Any]) -> object:
        sent.append(tool)
        return [json.dumps({"evidencePack": pack}), PROVIDER_LINE]

    published = await trigger_reverification_async(
        project_root=tmp_path,
        metadata={
            DEFENSIVE_KERNEL_KEY: kernel,
            GLASSPANE_TRANSPORT_KEY: sync_transport,
        },
        gates=_gates(tmp_path, kernel),
        round_number=1,
        planned_dimensions=["security"],
    )

    assert published is not None and published["attempted"] is True
    assert sent and client.frames == []


def test_the_acall_metadata_key_is_defined_exactly_once() -> None:
    """One module owns the key string; a second spelling is a contract that can drift."""
    src = Path(__file__).resolve().parents[2] / "src" / "iterate_harness"
    hits = [
        f"{path.relative_to(src)}:{number}"
        for path in sorted(src.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if '"glasspane_reverify_acall"' in line
    ]
    assert len(hits) == 1, f"the key is spelled in {len(hits)} places: {hits}"
    assert hits[0].startswith("iterate/reverify_trigger.py"), (
        f"the trigger owns the metadata-key contract, not the host: {hits}"
    )


def test_the_async_names_resolve_through_the_lazy_export_map() -> None:
    from iterate_harness import iterate as iterate_pkg
    from iterate_harness.iterate import glasspane_host_transport, reverify_trigger

    assert iterate_pkg.make_host_acall is glasspane_host_transport.make_host_acall
    assert iterate_pkg.GlasspaneAsyncTransport is glasspane_host_transport.GlasspaneAsyncTransport
    assert iterate_pkg.trigger_reverification_async is reverify_trigger.trigger_reverification_async
    assert iterate_pkg.GLASSPANE_ACALL_KEY is reverify_trigger.GLASSPANE_ACALL_KEY
    for name in (
        "GLASSPANE_ACALL_KEY",
        "GlasspaneAsyncTransport",
        "make_host_acall",
        "trigger_reverification_async",
    ):
        assert name in iterate_pkg.__all__
