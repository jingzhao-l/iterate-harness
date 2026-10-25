"""The trigger half of scenario ①: an invariant failure asks the engine again.

``iterate/glasspane_reverification.py`` can already send an ``iterate.reverification/0.1``
request and relay whatever the engine said; until this module nothing in the loop ever handed
it one. This is that missing hand-off, and it is deliberately the *only* place the decision is
made, because every other candidate site would have to invent a fact to do the same job:

- ``loop_policy.IterateLoopPolicy`` is consulted at the round boundary, but it only ever sees
  the *already-relayed* verdict string inside the published ``iterate_state`` — never the
  invariant report the request's ``trigger.reportDigest`` is a citation *of*. Building a request
  there would mean re-deriving a report nobody handed it, i.e. minting a digest for a run it
  cannot see. It also has no project root, which is where the ledger and the recorded
  operations live.
- ``engine/query.py`` could reach both, but it deliberately does not own iterate semantics
  (it merges tool metadata and explicitly refuses to carry the defensive kernel across turns:
  ``DEFENSIVE_KERNEL_KEY`` is skipped when tool metadata is folded back).

What remains is the round's publish site — the ``iterate_review`` aggregate call, where
``loop_gates.measured_gates`` has just *measured* the invariant verdict from the defensive
kernel in the tool metadata, where the round's state object is about to be published, and where
the run's ledger has its root. This module takes those three things and adds nothing to them.

THE RULES THIS MODULE IS BOUND BY (all of them pre-existing, restated here because this is the
site where they can actually be broken):

1. **No verdict, no words.** When the round published no ``invariant_state`` gate, this returns
   ``None`` and the caller publishes nothing. Not a ``False``, not an empty run, not a
   "nothing to re-verify" line: the loop did not measure anything, so it may not say anything.
2. **The judgement is not ours.** Whether the engine's answers add up to anything is read out of
   :attr:`ReverificationRun.any_verified` / ``all_clean`` and relayed. Nothing here compares
   counts, inspects outcomes, or decides that a failure is now cleared.
3. **A gate never becomes measured on the strength of a re-verification.** This module writes
   into no gate key; a missing/unreachable/refusing engine leaves the round's gates exactly as
   ``loop_gates`` produced them (see its docstring on why absent beats a default).
4. **A refusal is relayed, never re-decoded** — the contract's ``InvariantReportError`` and the
   engine's coded refusals travel with their code/message/remedy intact.
5. **The transport is the host's, and its absence is stated.** The harness's own MCP client
   (``McpClientManager``) is async; ``run_reverification`` is the synchronous sender and the
   module it lives in refuses an awaitable with ``GP_E_ASYNC_HOST`` rather than filing a
   coroutine as an answer. So the host must hand over a *synchronous* read-only transport under
   :data:`GLASSPANE_TRANSPORT_KEY`; when it has not, the send is recorded as not-attempted —
   which is a stated state, not a silence (and
   :attr:`GLASSPANE_TRANSPORT_ABSENT_KEY` is where a host says *why* it has not, instead of
   letting the sender guess a generic reason). The harness now is such a host:
   ``iterate/glasspane_host_transport.py`` builds the read-only transport from the run's own
   ``McpClientManager`` and binds it at the publish site. **It binds two, and the round uses the
   awaitable one** (:data:`GLASSPANE_ACALL_KEY` → :func:`trigger_reverification_async`), because
   the publish site's shape is fixed by the engine rather than chosen here: ``engine/query.py``
   awaits ``tool.execute`` and ``ui/runtime.py`` connects the MCP manager on that same loop, so a
   synchronous send at that site always read ``GP_E_HOST_LOOP_BUSY`` and the accepting branch
   never ran. The synchronous transport stays for hosts that own no loop, and a host that hands
   over only a synchronous transport while a loop runs still gets that named refusal — a stated
   state, never an invented answer. It answers with
   ``call_tool_parts``' ``list[str]`` for exactly the reason below. The transport's reply shape is the engine's own
   boundary: prefer ``McpClientManager.call_tool_parts``, whose ``list[str]`` keeps one entry
   per content item (the shipped ``gp_diagnose`` answers with the daemon frame *plus* an
   ``update state:`` block *plus* a ``provider:`` line, and relaying that as a joined string
   measured ``unparseable``); a transport that hands back a plain ``str`` still works, it just
   keeps only the single-item and already-parsed shapes. :func:`relay_answer` classifies per
   item and keeps the side items verbatim as annotations.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .decision_log import DECISION_LOG_REFUSALS, append_entry, make_entry, read_entries
from .dimension_ledger import ECHOED_DIMENSION_DATA_KEY
from .glasspane_recipe import (
    RECIPE_GATE_DATA_KEY,
    RECIPE_E_READ,
    RecipeError,
    RecipeRead,
    StepGate,
    read_recipe_or_refusal,
    recipe_path_from_project_list,
    recipe_step_gate,
)
from .glasspane_reverification import (
    CONFIG_READ_TOOLS,
    RelayedAnswer,
    ReverificationRun,
    run_reverification,
    run_reverification_async,
)
from .invariant_report import (
    OPERATION_ID_PATTERN,
    InvariantReportError,
    from_defensive_report,
    re_verification_request_from_targets,
)
from .loop_gates import GATE_KEYS

log = logging.getLogger(__name__)

#: The gate this trigger keys on, read from the producer's own key list: a trigger that
#: re-spelled ``invariant_state`` by hand would be a second contract that can drift.
INVARIANT_GATE_KEY = GATE_KEYS[0]

#: Where the round's answer is published inside ``iterate_state`` (and therefore survives the
#: compaction attachment, which reads that same dict).
REVERIFICATION_STATE_KEY = "reverification"

#: Metadata key a host uses to hand this loop a **synchronous** GlassPane transport, called as
#: ``call_tool(tool_name, arguments) -> the engine's reply`` — either the boundary-preserving
#: ``list[str]`` of content items (``McpClientManager.call_tool_parts``, what the shipped shell
#: actually sends) or a plain ``str`` for a host that has only the joined view. Absent (or not
#: callable) is not an error: it is recorded as a send that was never attempted.
GLASSPANE_TRANSPORT_KEY = "glasspane_reverify_transport"

#: Metadata key a host uses to say *why* it handed over no transport. Rule 5 is unchanged — an
#: absent transport is still a send that was never attempted — this only replaces the sender's
#: generic reason with the one the host actually measured (no MCP plane, no GlassPane server,
#: a server that never connected). Absent or blank keeps :func:`.glasspane_reverification.run_reverification`'s
#: default text, so a host that binds nothing says nothing beyond "no channel".
GLASSPANE_TRANSPORT_ABSENT_KEY = "glasspane_reverify_transport_absent"

#: The awaitable twin of :data:`GLASSPANE_TRANSPORT_KEY`: ``await call_tool(tool, arguments) ->
#: the engine's reply``. :func:`trigger_reverification_async` prefers it, and the round's publish
#: site is asynchronous, so this is the key that makes scenario ① actually send. Kept separate
#: from the synchronous key because the two are different contracts: a synchronous caller cannot
#: await a session bound to its own loop, and an awaitable transport handed to the synchronous
#: sender would be a coroutine filed as an answer.
GLASSPANE_ACALL_KEY = "glasspane_reverify_acall"

#: The ledger kind this decision is filed under. Shape-checked by the kernel, not by a list.
LEDGER_DECISION_TYPE = "decision"
LEDGER_DATA_KIND = "reverification"

#: How many recorded operations one round re-asks. The cap is the ledger's own: one entry has to
#: carry the citation of every answer it relays under ``PAYLOAD_MAX_CANONICAL_LENGTH``, and
#: asking without saying so is the lie — the remainder is counted in the published payload.
MAX_REQUESTED_OPERATIONS = 4

#: Where ④'s answer is published next to ①'s, in the same ``iterate_state`` payload, in the ledger
#: line that accompanies the send, and in the reader that prints it later. The string is defined once,
#: in :mod:`.glasspane_recipe`; this is an alias, not a second spelling — a key written twice is a
#: state that stops being findable the day one of them is renamed.
RECIPE_GATE_STATE_KEY = RECIPE_GATE_DATA_KEY

#: The registry read, taken from the producer's own list rather than re-spelled here — a second
#: literal for one tool is the drift this harness keeps getting fined for.
REGISTRY_READ_TOOL = CONFIG_READ_TOOLS[0]



def _sync_transport(metadata: Mapping[str, Any] | None) -> Callable[[str, dict[str, Any]], object] | None:
    """The host's synchronous transport, or ``None`` when the host has not provided one.

    A transport may answer with ``list[str]`` (one entry per MCP content item, the
    shape ``McpClientManager.call_tool_parts`` preserves) or with a plain ``str``;
    :func:`.glasspane_reverification.relay_answer` classifies both, per item where
    there are items. The missing-verdict rule is the caller's and is unchanged: when
    the round measured no invariant verdict this trigger says nothing and publishes
    nothing.

    An *awaitable* transport is not a synchronous one, and is filtered here rather than handed
    to the sender to be refused as ``GP_E_ASYNC_HOST`` after the fact: a run that bound only
    :data:`GLASSPANE_ACALL_KEY` and calls the synchronous trigger gets the honest
    "no transport was handed over" state, not a refusal that reads as an engine answer.
    """
    # Typed as `object` rather than left as the `Any` a `Mapping[str, Any].get()`
    # yields: `callable()` narrows `object` down to a callable, but does not narrow a
    # value that is already `Any`, and returning that `Any` past the declared
    # signature was the unchecked hole mypy was reporting here.
    candidate: object = (metadata or {}).get(GLASSPANE_TRANSPORT_KEY)
    if not callable(candidate) or inspect.iscoroutinefunction(candidate):
        return None
    return candidate


def _acall_transport(metadata: Mapping[str, Any] | None) -> Callable[[str, dict[str, Any]], Any] | None:
    """The host's awaitable transport, or ``None``.

    Only a coroutine function counts: ``inspect.iscoroutinefunction`` sees through
    ``functools.partial``, which is the one wrapping style this harness uses. A callable that
    merely *returns* an awaitable is not accepted, because telling it apart from a synchronous
    transport would mean calling it first — and a send made to discover the shape of the channel
    is the thing rule 5 exists to stop.
    """
    candidate = (metadata or {}).get(GLASSPANE_ACALL_KEY)
    return candidate if inspect.iscoroutinefunction(candidate) else None


def _not_attempted_reason(metadata: Mapping[str, Any] | None) -> str | None:
    """The host's stated reason for having no transport, or ``None`` to keep the default.

    Read-only plumbing for rule 5: this never turns a not-attempted send into an attempted
    one, and it carries no verdict — only the sentence naming what the host could not bind.
    """
    value = (metadata or {}).get(GLASSPANE_TRANSPORT_ABSENT_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _copied_mapping(value: object) -> dict[str, Any]:
    """A JSON-safe copy of one contract sub-object, or ``{}`` when it is not a mapping.

    The request is the kernel's own frame; relaying it means carrying its keys, not re-shaping
    them, and a copy keeps the published payload from aliasing the request the sender read.
    """
    return dict(value) if isinstance(value, Mapping) else {}


def recorded_operation_ids(project_root: Path | str) -> list[str]:
    """Operation ids the run actually recorded, newest first, deduplicated.

    The ledger is the only place this harness can cite an engine operation from: an id typed
    here, or read off a report the reviewer self-declared, would be a claim about the engine
    that nothing measured.
    """
    ids: list[str] = []
    for entry in read_entries(project_root):
        data = entry.data if isinstance(entry.data, Mapping) else {}
        value = data.get("operationId")
        if isinstance(value, str) and OPERATION_ID_PATTERN.match(value) and value not in ids:
            ids.append(value)
    return list(reversed(ids))


def reverification_targets(
    project_root: Path | str,
    planned_dimensions: Sequence[str] = (),
) -> tuple[dict[str, object], int]:
    """The targets this round may honestly name, plus how many ids it could not ask about.

    ``recipeSteps`` are deliberately never produced here. ④ asks that the recipe move only after the
    invariant re-check passes, and the contract this request is built under refuses to build one from
    a passing report (both implementations: ``reVerificationRequestFrom`` returns null on ``pass``).
    So the two conditions never coexist in one send: there is no request that may name steps *and*
    carries permission to run them. The recipe is still held, validated and digested —
    :func:`.glasspane_recipe.read_recipe` and the gate publish what it contains — and the steps stay
    the engine's own deferral when a caller names them some other way. Naming them from an error path
    would be this harness asking the engine to move the app on the strength of a check that did not
    pass, which is the opposite of the row this module exists to close.

    Dimensions come from the report the reviewer aggregated, so the list is what this run planned
    rather than what a config once said.
    """
    recorded = recorded_operation_ids(project_root)
    asked = recorded[:MAX_REQUESTED_OPERATIONS]
    targets: dict[str, object] = {}
    if asked:
        targets["operationIds"] = list(asked)
    dimensions = [str(dimension) for dimension in planned_dimensions if str(dimension).strip()]
    if dimensions:
        targets["dimensions"] = dimensions
    return targets, max(0, len(recorded) - len(asked))


def _registry_refusal(error: Exception, *, how: str) -> RecipeError:
    """One channel failure, in the hand-off's own refusal shape."""
    return RecipeError(
        RECIPE_E_READ,
        f"the registry read for the recipe {how}: {type(error).__name__}: {error}",
        "the recipe is what ④ gates on, so a channel that cannot answer is not a run with no "
        "recipe: repair the GlassPane MCP server (or set GLASSPANE_RECIPE to the absolute path "
        "this run is under) and re-run the round",
    )


def _decode_registry_reply(raw: object) -> tuple[str | None, RecipeError | None]:
    try:
        return recipe_path_from_project_list(raw), None
    except RecipeError as refusal:
        return None, refusal


async def _recipe_read_via_acall(acall: Callable[[str, dict[str, Any]], Any]) -> tuple[str | None, RecipeError | None]:
    """Await the registry read on the caller's loop and decode it; never raise out of here."""
    try:
        raw = await acall(REGISTRY_READ_TOOL, {})
    except Exception as error:  # noqa: BLE001 — a channel that throws is a channel that did not answer
        return None, _registry_refusal(error, how="raised an exception")
    return _decode_registry_reply(raw)


def _recipe_read_via_transport(
    transport: Callable[[str, dict[str, Any]], object],
) -> tuple[str | None, RecipeError | None]:
    """The same read over a synchronous transport, refusing rather than blocking."""
    try:
        raw = transport(REGISTRY_READ_TOOL, {})
    except Exception as error:  # noqa: BLE001 — same rule: the reply is a value, not an exception
        return None, _registry_refusal(error, how="raised an exception")
    return _decode_registry_reply(raw)


def _relay_answer(answer: RelayedAnswer, *, full: bool) -> dict[str, Any]:
    """One engine answer, in the engine's own words — including the refusal fields verbatim."""
    relayed: dict[str, Any] = {
        "tool": answer.tool,
        "state": answer.state,
        "operationId": answer.operation_id,
        "code": answer.code,
        # Provenance, not a verdict: which registered AE method licensed this reading.
        "method": answer.method,
    }
    # Side items are cited verbatim where they exist; an empty tuple adds nothing, so the
    # payload of a single-item answer keeps the byte length it had before multi-item relay.
    if answer.annotations:
        relayed["annotations"] = list(answer.annotations)
    # The shell's echoed declaration is carried as a structured field *in addition to* staying
    # verbatim in the annotations above, because the consumer (:mod:`.dimension_ledger`) must be
    # able to cross-check it without scraping prose it did not write. Absent means "this reply
    # echoed nothing" — which a reader must not read as "the call served no dimension", since a
    # call through the other surface declares it in metadata.
    if answer.echoed_dimension is not None:
        relayed[ECHOED_DIMENSION_DATA_KEY] = answer.echoed_dimension
    if full:
        relayed["arguments"] = dict(answer.arguments)
        relayed["outcome"] = answer.outcome
        relayed["summary"] = answer.summary
        relayed["message"] = answer.message
        relayed["remedy"] = answer.remedy
    return relayed


def _refusal_payload(error: InvariantReportError, verdict: str) -> dict[str, Any]:
    """A contract refusal, relayed: the loop did not send, and says which rule stopped it."""
    return {
        "spawned": False,
        "invariantState": verdict,
        "refusal": {"code": error.code, "message": error.message, "remedy": error.remedy},
    }


def record_reverification_run(
    project_root: Path | str,
    round_number: int,
    request: Mapping[str, object],
    run: ReverificationRun,
    gate: StepGate | None = None,
) -> str | None:
    """Append the one ledger entry this send earns; return the refusal text, never raise.

    Mirrors ``tools/iterate_tools._publish_run_plan``: a ledger that will not take the entry does
    not cancel the send (the engine already answered), and it does not get swallowed either —
    the caller puts the returned text into the round's payload.
    """
    data: dict[str, object] = {
        "kind": LEDGER_DATA_KIND,
        "summary": f"re-verification of {run.request_digest}: attempted={run.attempted}",
        "schemaVersion": request.get("schemaVersion"),
        "trigger": _copied_mapping(request.get("trigger")),
        "targets": _copied_mapping(request.get("targets")),
        "digest": run.request_digest,
        "attempted": run.attempted,
        # Relayed from the run's own judgement properties; this module computes nothing.
        "verified": run.any_verified,
        "clean": run.all_clean,
        "answers": [_relay_answer(answer, full=False) for answer in run.answers],
    }
    if run.not_attempted_reason is not None:
        data["notAttemptedReason"] = run.not_attempted_reason
    # The ④ answer travels with the send it accompanied. Without this the gate exists only in the
    # round's transient payload and in tests: `/iterate gate` would have nothing to read afterwards,
    # and a published state that evaporates with the round is one step from a claim nobody checks.
    if gate is not None:
        data[RECIPE_GATE_STATE_KEY] = gate.to_payload()
    try:
        append_entry(
            project_root,
            make_entry(
                entry_type=LEDGER_DECISION_TYPE,
                round_number=round_number,
                data=data,
            ),
        )
    except (OSError, *DECISION_LOG_REFUSALS) as error:
        log.warning("iterate re-verification decision could not be logged: %s", error, exc_info=True)
        return f"{getattr(error, 'code', type(error).__name__)}: {error}"
    return None


def _prepare(
    *,
    project_root: Path | str,
    metadata: Mapping[str, Any] | None,
    gates: Mapping[str, object],
    planned_dimensions: Sequence[str] = (),
    read: RecipeRead | None = None,
) -> tuple[str, Mapping[str, object] | None, int, dict[str, Any] | None, StepGate | None]:
    """What this round may honestly ask, decided once and shared by both senders.

    Returns ``(verdict, request, not_asked, outcome)``. ``outcome`` is the payload to publish when
    the round sends nothing — a contract refusal relayed as it came, or the kernel's own "the
    report passed, no request is built" line. ``request`` is ``None`` exactly when nothing may be
    sent, and when the round measured no invariant verdict at all both are ``None``, which is
    rule 1: the caller publishes nothing.

    ``read`` is the recipe hand-off, resolved by the sender — only a sender can await the registry
    read that answers "what does the other product have configured". ``None`` means this host asked
    nothing of the registry, and the gate publishes that as a named refusal rather than as an empty
    configuration.

    Split from the send so the synchronous trigger and the awaitable trigger cannot grow into two
    contracts: everything about *what may be asked* lives in this one body, and the only thing
    that differs between them is how the ask is driven.
    """
    verdict = gates.get(INVARIANT_GATE_KEY)
    if not isinstance(verdict, str) or not verdict:
        return "", None, 0, None, None

    from ..defensive.kernel import DEFENSIVE_KERNEL_KEY

    kernel = (metadata or {}).get(DEFENSIVE_KERNEL_KEY)
    report = getattr(kernel, "last_invariant_report", None)
    if report is None:
        # A verdict with no report behind it is a producer bug, and the honest answer about our
        # own action is "nothing was asked": no request, no ledger line, no published key.
        log.warning("iterate invariant gate %r carries no report; no re-verification was sent", verdict)
        return "", None, 0, None, None

    # ④'s gate, computed once for every outcome this round can publish — the passing one, the
    # contract refusals, and the send. It used to be computed only on the passing branch, which made
    # its non-pass refusals unreachable: a guard nobody can reach is not a delivered control, and the
    # publish site is the only place that knows the verdict, the recipe and the ledger together.
    gate = recipe_step_gate(
        project_root=project_root,
        invariant_verdict=verdict,
        read=read if read is not None else RecipeRead(registry_asked=False),
    )

    ensure = getattr(kernel, "invariant_ensure", None)
    commands = getattr(kernel, "invariant_commands", None)
    if ensure is None or commands is None:
        return (
            verdict,
            None,
            0,
            _with_gate(_refusal_payload(
                InvariantReportError(
                    "INVARIANT_REPORT_E_CONFIGURED",
                    f"the invariant verdict {verdict!r} came from a kernel that does not name the "
                    "checks it ran",
                    "the request's digest is a citation of the report, and the report has to carry "
                    "every configured check; expose the runner's own ensure list and command map "
                    "(defensive.kernel.DefensiveKernel.invariant_ensure / invariant_commands) "
                    "instead of letting the loop guess them",
                    "checks",
                ),
                verdict,
            ), gate),
            gate,
        )

    try:
        contract_report = from_defensive_report(list(ensure), dict(commands), report)
    except InvariantReportError as error:
        return verdict, None, 0, _with_gate(_refusal_payload(error, verdict), gate), gate

    targets, not_asked = reverification_targets(project_root, planned_dimensions)
    try:
        request = re_verification_request_from_targets(contract_report, targets)
    except InvariantReportError as error:
        return verdict, None, not_asked, _with_gate(_refusal_payload(error, verdict), gate), gate
    if request is None:
        # The kernel's own rule: a passing report earns no request. Nothing is sent, and the round
        # records that it sent nothing because nothing was broken — alongside ④'s answer to the
        # question the pass actually raises: may the recipe move forward now?
        outcome = _with_gate(
            {
                "spawned": False,
                "invariantState": verdict,
                "reason": "the invariant report passed; the kernel builds no re-verification request",
            },
            gate,
        )
        return verdict, None, not_asked, outcome, gate
    return verdict, request, not_asked, None, gate


def _with_gate(payload: dict[str, Any], gate: StepGate) -> dict[str, Any]:
    """Publish ④'s answer next to whatever else this round is saying about itself."""
    payload[RECIPE_GATE_STATE_KEY] = gate.to_payload()
    return payload


def _finalize(
    *,
    project_root: Path | str,
    round_number: int,
    verdict: str,
    request: Mapping[str, object],
    not_asked: int,
    run: ReverificationRun,
    gate: StepGate | None,
) -> dict[str, Any]:
    """One send, published: the round's payload plus the single ledger entry it earns."""
    payload: dict[str, Any] = {
        "spawned": True,
        "invariantState": verdict,
        "schemaVersion": request["schemaVersion"],
        "trigger": _copied_mapping(request["trigger"]),
        "targets": _copied_mapping(request["targets"]),
        "digest": run.request_digest,
        "attempted": run.attempted,
        "notAttemptedReason": run.not_attempted_reason,
        # Judgement relayed from the run, never recomputed here.
        "verified": run.any_verified,
        "clean": run.all_clean,
        "answers": [_relay_answer(answer, full=True) for answer in run.answers],
        "deferred": [
            {"kind": target.kind, "detail": target.detail, "reason": target.reason}
            for target in run.deferred
        ],
        "lines": run.to_model_lines(),
    }
    if not_asked:
        payload["operationIdsNotRequested"] = not_asked
    if gate is not None:
        payload[RECIPE_GATE_STATE_KEY] = gate.to_payload()
    ledger_error = record_reverification_run(project_root, round_number, request, run, gate)
    if ledger_error is not None:
        payload["ledgerError"] = ledger_error
    return payload


def _resolve_recipe_read(
    *,
    shell_reported_path: str | None,
    channel_refusal: RecipeError | None,
    registry_asked: bool,
) -> RecipeRead:
    """One hand-off, from whatever the channel and the environment each said.

    A recipe named by ``GLASSPANE_RECIPE`` is honored even when the registry could not answer —
    the operator handed this run a recipe, which is a fact about the run. With nothing handed, the
    channel's own refusal is what gets published, because "the registry refused" and "nobody
    configured a recipe" are different sentences and only the first is true.
    """
    read = read_recipe_or_refusal(shell_reported_path=shell_reported_path, registry_asked=registry_asked)
    if read.recipe is None and channel_refusal is not None:
        return RecipeRead(refusal=channel_refusal, registry_asked=registry_asked)
    return read


def _verdict_present(gates: Mapping[str, object]) -> bool:
    """Whether this round measured an invariant verdict at all — the only gate on spending a read.

    Rule 1 says a round with no verdict publishes nothing, so asking the registry in that case
    would cost a call to learn something the round is not allowed to say.
    """
    verdict = gates.get(INVARIANT_GATE_KEY)
    return isinstance(verdict, str) and bool(verdict)


def trigger_reverification(
    *,
    project_root: Path | str,
    metadata: Mapping[str, Any] | None,
    gates: Mapping[str, object],
    round_number: int,
    planned_dimensions: Sequence[str] = (),
) -> dict[str, Any] | None:
    """Send the failing invariant's re-verification through a synchronous transport.

    Returns ``None`` when the round measured no invariant verdict at all — the caller then
    publishes nothing, which is the difference between "not needed" and "not known". Otherwise a
    JSON-safe payload for the round's ``iterate_state`` and the tool output.

    A host whose publish site runs inside the event loop that owns its MCP sessions must call
    :func:`trigger_reverification_async` instead; this function is for a host that owns no loop.
    """
    read: RecipeRead | None = None
    if _verdict_present(gates):
        transport = _sync_transport(metadata)
        if transport is not None:
            path, refusal = _recipe_read_via_transport(transport)
            read = _resolve_recipe_read(
                shell_reported_path=path, channel_refusal=refusal, registry_asked=True
            )
        else:
            read = _resolve_recipe_read(shell_reported_path=None, channel_refusal=None, registry_asked=False)
    verdict, request, not_asked, outcome, gate = _prepare(
        project_root=project_root,
        metadata=metadata,
        gates=gates,
        planned_dimensions=planned_dimensions,
        read=read,
    )
    if request is None or outcome is not None:
        return outcome
    run = run_reverification(
        request,
        _sync_transport(metadata),
        not_attempted_reason=_not_attempted_reason(metadata),
    )
    return _finalize(
        project_root=project_root,
        round_number=round_number,
        verdict=verdict,
        request=request,
        not_asked=not_asked,
        run=run,
        gate=gate,
    )


async def trigger_reverification_async(
    *,
    project_root: Path | str,
    metadata: Mapping[str, Any] | None,
    gates: Mapping[str, object],
    round_number: int,
    planned_dimensions: Sequence[str] = (),
) -> dict[str, Any] | None:
    """The same trigger, awaited: the transport is driven on the caller's own loop.

    This is what the round's publish site calls, because that site is already inside the loop its
    MCP client was connected on (rule 5). The awaitable transport wins; a host that handed over
    only a synchronous one falls back to :func:`.glasspane_reverification.run_reverification`, so
    the outcome is the same payload either way and a refusal still arrives relayed rather than as
    an exception. Nothing about which checks to ask, or what is published, differs between the
    two triggers — that is why both call :func:`_prepare` and :func:`_finalize`.
    """
    read: RecipeRead | None = None
    acall = _acall_transport(metadata)
    transport = _sync_transport(metadata)
    if _verdict_present(gates):
        if acall is not None:
            path, refusal = await _recipe_read_via_acall(acall)
            read = _resolve_recipe_read(shell_reported_path=path, channel_refusal=refusal, registry_asked=True)
        elif transport is not None:
            path, refusal = _recipe_read_via_transport(transport)
            read = _resolve_recipe_read(shell_reported_path=path, channel_refusal=refusal, registry_asked=True)
        else:
            read = _resolve_recipe_read(shell_reported_path=None, channel_refusal=None, registry_asked=False)
    verdict, request, not_asked, outcome, gate = _prepare(
        project_root=project_root,
        metadata=metadata,
        gates=gates,
        planned_dimensions=planned_dimensions,
        read=read,
    )
    if request is None or outcome is not None:
        return outcome
    if acall is not None:
        run = await run_reverification_async(request, acall)
    else:
        run = run_reverification(
            request,
            transport,
            not_attempted_reason=_not_attempted_reason(metadata),
        )
    return _finalize(
        project_root=project_root,
        round_number=round_number,
        verdict=verdict,
        request=request,
        not_asked=not_asked,
        run=run,
        gate=gate,
    )


__all__ = [
    "GLASSPANE_ACALL_KEY",
    "GLASSPANE_TRANSPORT_ABSENT_KEY",
    "GLASSPANE_TRANSPORT_KEY",
    "INVARIANT_GATE_KEY",
    "MAX_REQUESTED_OPERATIONS",
    "RECIPE_GATE_STATE_KEY",
    "REGISTRY_READ_TOOL",
    "REVERIFICATION_STATE_KEY",
    "record_reverification_run",
    "recorded_operation_ids",
    "reverification_targets",
    "trigger_reverification",
    "trigger_reverification_async",
]
