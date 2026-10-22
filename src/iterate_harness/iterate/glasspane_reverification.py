"""Send an invariant failure's re-verification to the engine that can answer it.

WHY THIS MODULE EXISTS. When an invariant breaks, this harness knows *its own* rule
failed; it does not know whether the GUI claim that motivated the rule is still false.
Only the engine can say that, and the two products share no process — so the ask has to
travel as the kernel's `iterate.reverification/0.1` value over the MCP channel that
`iterate-harness` already uses for GlassPane. The kernel builds the request
(`invariant_report.re_verification_request_from_targets`); this module is the half that
sends it and relays what came back.

WHAT IT IS ALLOWED TO DO: read. Every call it makes is one the engine itself documents as
read-only, and the answer it reports is the engine's — transcribed through
``evidence_transcription``, the same mapping the kernel applies, never re-derived here.

WHAT IT REFUSES TO DO, and why each refusal is the point:

- **It never performs a mutation.** A recipe step like `act` is a click on a real app;
  re-running it is a decision about the user's machine, not a measurement, and it belongs
  behind the host's permission plane. Such targets come back as `deferred` with the reason.
  Silently dropping them would make a partial re-verification read as a complete one.
- **It never answers for an unreachable engine.** No channel, or a server that did not
  connect, is `not-attempted` — a different word from "verified" and from "failed". The
  accident this whole path exists to prevent is a loop reporting a verdict it never got.
- **It never re-decodes a refusal.** When the engine answers with a coded refusal, the
  code/message/remedy are relayed verbatim; the remedy is written for an agent to execute.
- **It never reads a verdict out of a pack the registered provider will not price.** Whether a
  pack may be diagnosed at all is answered through the kernel's provider contract
  (:func:`.provider_producers.ae_method_for` asks ``iterate-engine-ae`` which method this pack
  carries), and that refusal is relayed in the same verbatim shape as an engine one. A pack
  whose signal fields are all present and all empty would otherwise reach the transcription,
  which answers `inconclusive` for nothing measured.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .provider_producers import ae_method_for
from .provider_registry import ProviderRegistryError

#: Read-only tools by name. The set is small on purpose: anything that moves the app,
#: captures a screenshot into a caller's model, or re-attaches is a decision, not a
#: measurement. ``gp_attach`` is excluded for the same reason — it drops the evidence the
#: previous attachment built.
READ_ONLY_TOOLS = ("gp_probe_status", "gp_observe", "gp_diagnose", "gp_last_evidence", "gp_recent_reports")

#: Tools that would mutate. Named so the refusal is testable rather than implied by absence.
MUTATING_TOOLS = ("gp_act", "gp_snapshot", "gp_restore", "gp_attach", "gp_export_evidence")

EngineCall = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class RelayedAnswer:
    """One engine answer, as the engine said it."""

    tool: str
    arguments: Mapping[str, Any]
    state: str  # answered | refused | unparseable
    operation_id: str | None = None
    outcome: str | None = None  # the transcription's verdict, the engine's own words
    summary: str | None = None
    code: str | None = None
    message: str | None = None
    remedy: str | None = None
    #: Which registered AE method this answer was read under, when the relay had to ask one.
    #: Provenance, not a verdict: it says which registration licensed the reading.
    method: str | None = None
    #: Content items the shell sent *besides* the answer item, kept verbatim. The shipped
    #: shell answers ``gp_diagnose`` with the daemon frame plus an ``update state:`` block
    #: plus a ``provider:`` attribution line; those side items are user-visible facts about
    #: the call, and silently dropping one is exactly as wrong as failing to read the frame.
    annotations: tuple[str, ...] = ()
    #: The dimension 面 A says this call served, read out of the shell's own echo item
    #: (``mcp-shell/src/dimension.ts``). ``None`` means the reply carried no such line — which
    #: is NOT "the call served no dimension": a call through 面 B puts the declaration in tool
    #: metadata instead of in the text, and this field says nothing about it. It is a citation
    #: of what the shell said, used by :mod:`.dimension_ledger` to cross-check a self-declared
    #: ledger attribution; the annotation itself stays in ``annotations`` regardless, verbatim.
    echoed_dimension: str | None = None


@dataclass(frozen=True)
class DeferredTarget:
    """A target this module will not act on, and the reason it says so."""

    kind: str
    detail: str
    reason: str


@dataclass(frozen=True)
class ReverificationRun:
    """The whole sending: what was asked, what came back, what was left undone."""

    request_digest: str
    attempted: bool
    answers: tuple[RelayedAnswer, ...] = ()
    deferred: tuple[DeferredTarget, ...] = ()
    not_attempted_reason: str | None = None

    @property
    def any_verified(self) -> bool:
        return any(answer.state == "answered" for answer in self.answers)

    @property
    def all_clean(self) -> bool:
        """True when every answer the engine gave says the claim holds.

        `answered` here means the *evidence* was returned; whether the evidence supports
        the claim is the transcription's outcome, which is the engine's verdict relayed —
        this module adds no judgement of its own to it.
        """
        return self.attempted and bool(self.answers) and all(
            answer.state == "answered" and answer.outcome in ("pass", "verified") for answer in self.answers
        )

    def to_model_lines(self) -> list[str]:
        """The text handed to the model, built only from what the engine said."""
        if not self.attempted:
            return [f"re-verification NOT attempted — {self.not_attempted_reason or 'no GlassPane channel'}"]
        lines = [f"re-verification of {self.request_digest}:"]
        for answer in self.answers:
            arguments = ", ".join(f"{key}={value}" for key, value in sorted(answer.arguments.items())) or "-"
            if answer.state == "answered":
                asked = f" via {answer.method}" if answer.method else ""
                lines.append(
                    f"  {answer.tool}({arguments}): engine outcome={answer.outcome}{asked} "
                    f"operationId={answer.operation_id or 'none'} — {answer.summary or ''}".rstrip()
                )
            elif answer.state == "refused":
                lines.append(f"  {answer.tool}({arguments}): {answer.code}: {answer.message} remedy: {answer.remedy}")
            else:
                lines.append(f"  {answer.tool}({arguments}): answer was not the engine's documented shape; not interpreted")
            # Side items travel verbatim: the update block and the provider attribution are
            # facts about this call, so a relayed or compacted round still shows each once,
            # with no verdict wording added by us.
            for annotation in answer.annotations:
                lines.append(f"  {annotation}")
        for target in self.deferred:
            lines.append(f"  deferred {target.kind} ({target.detail}): {target.reason}")
        if not self.answers:
            lines.append("  no read-only call was possible for these targets; nothing was verified")
        return lines


def plan_calls(request: Mapping[str, Any]) -> tuple[list[EngineCall], list[DeferredTarget]]:
    """Translate the kernel's targets into read-only calls plus what must be deferred.

    Only `operationIds` can be re-verified by reading: the engine already holds the pack
    for that operation, and `gp_last_evidence` returns it unchanged. A recipe step or a
    dimension says *what to do again*, which is a mutation plan — never executed here.
    """
    targets = request.get("targets")
    if not isinstance(targets, Mapping):
        return [], [DeferredTarget("targets", "the request carries no targets object", "a re-verification with nothing to ask is not a re-verification")]

    calls: list[EngineCall] = []
    deferred: list[DeferredTarget] = []

    operation_ids = targets.get("operationIds")
    if isinstance(operation_ids, Sequence) and not isinstance(operation_ids, (str, bytes)):
        for operation_id in operation_ids:
            calls.append(("gp_last_evidence", {"operationId": str(operation_id)}))
            calls.append(("gp_diagnose", {"operationId": str(operation_id)}))

    steps = targets.get("recipeSteps")
    if isinstance(steps, Sequence) and not isinstance(steps, (str, bytes)):
        for step in steps:
            detail = json.dumps(step, ensure_ascii=False, sort_keys=True)[:160] if isinstance(step, Mapping) else str(step)[:160]
            deferred.append(
                DeferredTarget(
                    "recipeStep",
                    detail,
                    "re-running a recipe step acts on the real app; that belongs behind the host's permission prompt, not inside an error path",
                )
            )

    dimensions = targets.get("dimensions")
    if isinstance(dimensions, Sequence) and not isinstance(dimensions, (str, bytes)):
        for dimension in dimensions:
            deferred.append(
                DeferredTarget(
                    "dimension",
                    str(dimension),
                    "a dimension is re-covered by the review round that owns it; the engine has no read-only call that answers 'is this dimension verified'",
                )
            )

    if not calls and not deferred:
        deferred.append(DeferredTarget("targets", "none of the three target kinds were recognised", "nothing was asked of the engine, so nothing may be reported about it"))
    return calls, deferred


def relay_answer(tool: str, arguments: Mapping[str, Any], raw: object) -> RelayedAnswer:
    """Turn one engine reply into a relayed answer, without interpreting anything.

    The three shapes are the engine's own: an evidence pack, a coded refusal, or
    something this harness cannot classify. The last one is reported as such — a
    best-effort guess would be this module inventing a verdict.

    ``raw`` arrives in one of the host's three shapes: a single item's text, one
    already-decoded ``Mapping``, or the boundary-preserving ``Sequence[str]`` from
    ``McpClientManager.call_tool_parts`` — which is what the shipped shell actually
    produces: ``gp_diagnose`` answers with the daemon frame *plus* an ``update state:``
    block *plus* a ``provider:`` attribution line, and measured 2026-10-21 against this
    exact code a frame joined with ANY second item classified as ``unparseable``. So a
    sequence is classified per item: the first item that is the engine's answer — it
    parses as a JSON object, or matches the shell's coded-refusal line — is relayed, and
    every other item is kept verbatim in ``RelayedAnswer.annotations``.

    A joined ``str`` stays supported (it is still what ``call_tool`` returns for other
    callers), and a joined string that cannot be parsed is ``unparseable`` as before:
    it is deliberately NOT re-split on newlines, because a legitimate frame's text may
    itself contain newlines and re-splitting would be guessing at the boundary.
    """
    if isinstance(raw, (str, Mapping)):
        return _relay_item(tool, arguments, raw)
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        items = list(raw)
        if not items:
            return _relay_item(tool, arguments, "")
        index = next((position for position, item in enumerate(items) if _is_answer_item(item)), 0)
        answer = _relay_item(tool, arguments, items[index])
        annotations = tuple(item if isinstance(item, str) else str(item) for position, item in enumerate(items) if position != index)
        return replace(
            answer,
            annotations=annotations,
            echoed_dimension=_echoed_dimension(annotations),
        )
    return RelayedAnswer(tool=tool, arguments=dict(arguments), state="unparseable")


#: 面 A's echo item, in the shape the shell writes it:
#: ``dimension: <id> | declared by this call; …``. Only the id is read; the sentence after the
#: pipe stays where it is — this parses a declaration, it does not re-summarise a remedy.
_DIMENSION_ECHO = re.compile(r"^dimension:[ \t]*(?P<id>[^\s|]+)")


def _echoed_dimension(items: Sequence[str]) -> str | None:
    """The dimension id the shell echoed, or ``None`` when no item is an echo it owns.

    Deliberately conservative in three ways, because this value is going to be used to
    *doubt* a ledger attribution:

    - the id is checked with the kernel's own validator (via
      :func:`.dimension_ledger.is_valid_dimension_id`), so a truncated or malformed echo is
      read as "no echo", not as a competing claim;
    - only the first echo wins, and an item that merely contains the word is not one — the
      line must start with it, which is the shape the shell emits;
    - a joined string never reaches here (``relay_answer`` classifies sequences per item), so
      this is not guessing at a boundary the transport already destroyed.

    Drift in the shell's wording costs a cross-check, never a wrong verdict: that asymmetry is
    why the reader is lenient about the sentence and strict about the id.
    """
    from .dimension_ledger import is_valid_dimension_id

    for item in items:
        match = _DIMENSION_ECHO.match(item)
        if match is not None and is_valid_dimension_id(match.group("id")):
            return str(match.group("id"))
    return None


def _is_answer_item(item: object) -> bool:
    """True when one content item IS the engine's answer, not a note beside it.

    The answer is an item that parses as a JSON object (a frame, a pack — whatever
    shape it then decodes to) or matches the shell's coded-refusal line. Everything
    else — the ``update state:`` block, the ``provider:`` attribution — annotates
    the call without being it.
    """
    if isinstance(item, Mapping):
        return True
    if not isinstance(item, str):
        return False
    if _refusal_from_text(item) is not None:
        return True
    try:
        parsed = json.loads(item)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, Mapping)


def _relay_item(tool: str, arguments: Mapping[str, Any], payload: object) -> RelayedAnswer:
    """Classify exactly ONE engine item. Never call this on a join of items."""
    if isinstance(payload, str):
        structured = _refusal_from_text(payload)
        if structured is not None:
            return RelayedAnswer(
                tool=tool,
                arguments=dict(arguments),
                state="refused",
                code=structured["code"],
                message=structured["message"],
                remedy=structured["remedy"],
            )
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return RelayedAnswer(tool=tool, arguments=dict(arguments), state="unparseable")
    if not isinstance(payload, Mapping):
        return RelayedAnswer(tool=tool, arguments=dict(arguments), state="unparseable")

    pack = payload.get("evidencePack")
    if isinstance(pack, Mapping):
        return _relay_pack(tool, arguments, pack)
    if "code" in payload:
        return RelayedAnswer(
            tool=tool,
            arguments=dict(arguments),
            state="refused",
            code=str(payload.get("code")),
            message=str(payload.get("message", "")),
            remedy=str(payload.get("remedy", "")),
        )
    diagnosis = payload.get("diagnosis")
    if isinstance(diagnosis, Mapping):
        # gp_diagnose answers with a class and a four-part report. Both are the engine's
        # words; they are relayed, not scored.
        return RelayedAnswer(
            tool=tool,
            arguments=dict(arguments),
            state="answered",
            operation_id=str(payload["operationId"]) if payload.get("operationId") else None,
            outcome=str(diagnosis.get("class", "")),
            summary=str(diagnosis.get("report", ""))[:400],
        )
    return RelayedAnswer(tool=tool, arguments=dict(arguments), state="unparseable")


def _refusal_from_text(text: str) -> dict[str, str] | None:
    """Parse the shell's own refusal line: ``CODE: message | remedy: R``.

    Measured 2026-10-12 against the shipped `glasspane-mcp`: `gp_diagnose` on a daemon
    with nothing attached answers with exactly that text (the format lives in
    `mcp-shell/src/errors.ts:formatToolError`), so a relay that only understood a JSON
    object with a `code` field would file a legitimate engine refusal as noise — the one
    answer type that has to reach the model with its remedy attached. Measured 2026-10-21
    against the same shell's multi-item replies (the daemon frame plus an ``update state:``
    block plus a ``provider:`` line): this parse is only ever applied to ONE content item,
    because fed a join of the frame and a second item it matches nothing and files a
    legitimate refusal — or the frame beside it — as noise.
    """
    head, separator, tail = text.partition(" | remedy: ")
    if not separator or ":" not in head:
        return None
    code, _, message = head.partition(": ")
    if not code or not code.isupper() or not all(token in "._-" or token.isalnum() for token in code):
        return None
    return {"code": code.strip(), "message": message.strip(), "remedy": tail.strip()}


#: The fields the transcription reads. A pack without them yields `inconclusive` from the
#: transcription by design — "no assertion, no diagnosis" is its honest answer about an
#: absent measurement — so the relay must not forward that as a verdict on the app.
TRANSCRIBABLE_FIELDS = ("schemaVersion", "operationId", "attribution", "circuitBreaker", "signals")


def _relay_pack(tool: str, arguments: Mapping[str, Any], pack: Mapping[str, Any]) -> RelayedAnswer:
    from .evidence_transcription import decision_outcome_from_evidence, decision_summary_from_evidence

    absent = [field for field in TRANSCRIBABLE_FIELDS if field not in pack]
    if absent:
        return RelayedAnswer(
            tool=tool,
            arguments=dict(arguments),
            state="unparseable",
            code="GP_E_PACK_INCOMPLETE",
            message=f"the pack carries none of {', '.join(absent)}",
            remedy="re-verification needs a pack the engine actually recorded; the transcription answers 'inconclusive' for absent fields, and that is not a verdict on the app",
        )
    # Whether this pack may be read for a verdict is the provider contract's question, not
    # this module's: `iterate-engine-ae` registers `diagnose` as requiring an evidence pack,
    # and a mapping whose signal fields are present but all empty carries none. The refusal is
    # relayed in the engine-refusal shape -- code, message, remedy verbatim -- because "this
    # pack records nothing" must not arrive at the model as "the engine looked and could not
    # tell", which is the accident the check above exists to prevent.
    try:
        method = ae_method_for(pack)
    except ProviderRegistryError as error:
        return RelayedAnswer(
            tool=tool,
            arguments=dict(arguments),
            state="refused",
            code=error.code,
            message=error.message,
            remedy=error.remedy,
        )
    try:
        outcome = decision_outcome_from_evidence(pack)
        summary = decision_summary_from_evidence(pack)
    except Exception as error:  # noqa: BLE001 — a pack we cannot transcribe is not a verdict
        return RelayedAnswer(
            tool=tool,
            arguments=dict(arguments),
            state="unparseable",
            message=f"the transcription refused this pack: {type(error).__name__}",
        )
    return RelayedAnswer(
        tool=tool,
        arguments=dict(arguments),
        state="answered",
        operation_id=str(pack["operationId"]) if pack.get("operationId") else None,
        outcome=str(outcome),
        summary=str(summary),
        method=method,
    )


def run_reverification(
    request: Mapping[str, Any],
    call_tool: Callable[[str, dict[str, Any]], object] | None,
    *,
    not_attempted_reason: str | None = None,
) -> ReverificationRun:
    """Send the request over a **synchronous** tool call and relay the answers.

    ``call_tool`` is (tool, arguments) -> the engine's reply: either the boundary-preserving
    ``list[str]`` of content items that ``McpClientManager.call_tool_parts`` produces, or a
    plain ``str`` for a host that only has the joined view. A host whose MCP client
    is async (which is what ``McpClientManager`` is) must use
    :func:`run_reverification_async`; calling this one with an async callable hands back
    coroutine objects that are not answers, and those land as ``unparseable`` — measured,
    not hypothetical. It is refused loudly here rather than awaited by surprise.
    """
    digest = str((request.get("trigger") or {}).get("reportDigest", "")) or "unreported-digest"
    calls, deferred = plan_calls(request)
    if call_tool is None:
        return ReverificationRun(
            request_digest=digest,
            attempted=False,
            deferred=tuple(deferred),
            not_attempted_reason=not_attempted_reason or "no GlassPane MCP channel is configured",
        )

    answers: list[RelayedAnswer] = []
    for tool, arguments in calls:
        if tool in MUTATING_TOOLS:  # unreachable by construction; guards the plan function
            deferred.append(DeferredTarget("call", tool, "this module does not perform mutating calls"))
            continue
        try:
            raw = call_tool(tool, arguments)
            if inspect.isawaitable(raw):
                # The bug this guard catches: an async host wired to the sync runner. The
                # coroutine is not an answer, and reporting it as one would be reporting a
                # verdict nobody received.
                answers.append(
                    RelayedAnswer(
                        tool=tool,
                        arguments=arguments,
                        state="unparseable",
                        code="GP_E_ASYNC_HOST",
                        message="the host's tool call returned an awaitable; the sync runner cannot relay it",
                        remedy="call run_reverification_async from the event loop that owns the MCP client",
                    )
                )
                close = getattr(raw, "close", None)
                if callable(close):
                    close()  # a coroutine left unawaited is a warning that hides the misuse
                continue
        except Exception as error:  # noqa: BLE001 — an unreachable engine is a stated state
            answers.append(
                RelayedAnswer(
                    tool=tool,
                    arguments=arguments,
                    state="refused",
                    code="GP_E_CHANNEL",
                    message=f"the call did not complete: {type(error).__name__}: {error}",
                    remedy="check that glasspane-mcp is running and that the harness's mcp config points at it",
                )
            )
            continue
        answers.append(relay_answer(tool, arguments, raw))

    return ReverificationRun(request_digest=digest, attempted=True, answers=tuple(answers), deferred=tuple(deferred))


async def run_reverification_async(
    request: Mapping[str, Any],
    acall_tool: Callable[[str, dict[str, Any]], Any] | None,
    *,
    not_attempted_reason: str | None = None,
) -> ReverificationRun:
    """The async host's version: await each read-only call, relay whatever the engine said."""
    digest = str((request.get("trigger") or {}).get("reportDigest", "")) or "unreported-digest"
    calls, deferred = plan_calls(request)
    if acall_tool is None:
        return ReverificationRun(
            request_digest=digest,
            attempted=False,
            deferred=tuple(deferred),
            not_attempted_reason=not_attempted_reason or "no GlassPane MCP channel is configured",
        )
    answers: list[RelayedAnswer] = []
    for tool, arguments in calls:
        if tool in MUTATING_TOOLS:
            deferred.append(DeferredTarget("call", tool, "this module does not perform mutating calls"))
            continue
        try:
            raw = await acall_tool(tool, arguments)
        except Exception as error:  # noqa: BLE001 — an unreachable engine is a stated state
            answers.append(
                RelayedAnswer(
                    tool=tool,
                    arguments=arguments,
                    state="refused",
                    code="GP_E_CHANNEL",
                    message=f"the call did not complete: {type(error).__name__}: {error}",
                    remedy="check that glasspane-mcp is running and that the harness's mcp config points at it",
                )
            )
            continue
        answers.append(relay_answer(tool, arguments, raw))
    return ReverificationRun(request_digest=digest, attempted=True, answers=tuple(answers), deferred=tuple(deferred))


__all__ = [
    "MUTATING_TOOLS",
    "READ_ONLY_TOOLS",
    "DeferredTarget",
    "RelayedAnswer",
    "ReverificationRun",
    "plan_calls",
    "run_reverification_async",
    "relay_answer",
    "run_reverification",
]
