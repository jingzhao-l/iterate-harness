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
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

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
                lines.append(
                    f"  {answer.tool}({arguments}): engine outcome={answer.outcome} "
                    f"operationId={answer.operation_id or 'none'} — {answer.summary or ''}".rstrip()
                )
            elif answer.state == "refused":
                lines.append(f"  {answer.tool}({arguments}): {answer.code}: {answer.message} remedy: {answer.remedy}")
            else:
                lines.append(f"  {answer.tool}({arguments}): answer was not the engine's documented shape; not interpreted")
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
    """
    payload: object = raw
    if isinstance(payload, str):
        structured = _refusal_from_text(payload)
        if structured is not None:
            return RelayedAnswer(tool=tool, arguments=dict(arguments), state="refused", **structured)
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
    answer type that has to reach the model with its remedy attached.
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
    )


def run_reverification(
    request: Mapping[str, Any],
    call_tool: Callable[[str, dict[str, Any]], object] | None,
    *,
    not_attempted_reason: str | None = None,
) -> ReverificationRun:
    """Send the request over a **synchronous** tool call and relay the answers.

    ``call_tool`` is (tool, arguments) -> the engine's reply text. A host whose MCP client
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
