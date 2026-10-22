"""The host half of scenario ①: a synchronous, read-only GlassPane transport.

``iterate/reverify_trigger.py`` documents rule 5 — the transport is the *host's*, and its
absence must be stated. ``iterate/glasspane_reverification.py`` is the sender, and
``mcp/client.py`` is the client the run already configured. This module is the one missing
thing between them: a callable the synchronous publish site can hand to
``run_reverification`` that drives the run's own async ``McpClientManager``.

WHAT IT GUARANTEES, in the order the guarantees matter:

1. **Read-only by construction.** The tool set is imported from the contract
   (:data:`.glasspane_reverification.READ_ONLY_TOOLS`), never restated here, and the check
   happens before any coroutine exists — so a write tool is refused without a frame ever
   reaching the session. A name outside the read-only set is refused *including* the ones
   this module has never heard of; deny-by-default is the only shape that stays true when
   the shell adds a tool.
2. **The item boundary survives.** It consumes ``McpClientManager.call_tool_parts`` and
   hands the sender a ``list[str]`` — one entry per content item. Feeding it ``call_tool``'s
   join would put this harness back to the measured defect: the shipped ``gp_diagnose``
   answers with the daemon frame *plus* an ``update state:`` block *plus* a ``provider:``
   line, and a frame joined with anything else classifies ``unparseable``.
3. **Re-entrancy is decided per case, and each case has a working send.** ``run_reverification``
   is called from the round's publish site (``iterate_review``'s aggregate), and that site's
   shape is not a guess: ``engine/query.py`` runs tools with ``await tool.execute(...)`` and
   ``ui/runtime.py`` builds the run's ``McpClientManager`` with ``await connect_all()`` on the
   engine loop, then puts it in the tool metadata. So the publish site is **always** inside the
   loop that owns the session, and a synchronous caller there would block the very loop its own
   MCP reply needs. That is why this module binds two transports and the publish site awaits one:

   - :func:`make_host_acall` — awaited on the loop that owns the client, which is the only loop
     that *can* drive those sessions. This is the production path.
   - :func:`make_host_transport` — a synchronous transport for a host that owns no loop
     (:data:`HOST_TRANSPORT_TIMEOUT_SECONDS` is its backstop window). Awaiting a client bound to
     *another* loop would be unsound rather than merely blocked, so this one keeps
     :data:`LOOP_BUSY_CODE` when a loop is running and names the awaitable transport as the
     remedy — the refusal is a stated state, not the end of the channel.

   Both refuse a tool outside the read-only set *before* a coroutine exists, so a refused call
   is a call that never was.

4. **Transport errors keep their shapes.** ``McpServerNotConnectedError`` and
   ``McpServerTimeoutError`` propagate to the sender, which relays them as ``GP_E_CHANNEL``
   with its remedy; this module never converts "could not ask" into "the engine said
   nothing", and it never invents a verdict.

:func:`bind_host_transport` is the wiring seam: it produces the metadata additions the
publish site merges, and when the run has no GlassPane MCP server configured it says so in
:data:`.reverify_trigger.GLASSPANE_TRANSPORT_ABSENT_KEY` rather than staying silent — a
host-side absence reads as *not attempted, because X*, never as an empty success.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..mcp.client import McpServerTimeoutError
from .glasspane_reverification import READ_ONLY_TOOLS
from .reverify_trigger import GLASSPANE_ACALL_KEY, GLASSPANE_TRANSPORT_ABSENT_KEY, GLASSPANE_TRANSPORT_KEY

log = logging.getLogger(__name__)

#: Where the run's MCP client lives in the tool metadata — the same key
#: ``tools/mcp_auth_tool.py`` reads, so the harness has one name for one object.
MCP_MANAGER_METADATA_KEY = "mcp_manager"

#: Optional override: name the server the re-verification channel must use. Discovery is
#: the default, but a run with two GlassPane-shaped servers must be able to say which one
#: it means instead of letting dict order decide.
GLASSPANE_SERVER_NAME_KEY = "glasspane_mcp_server_name"

#: Backstop window for driving one async MCP call from a synchronous publish site. The
#: client clamps its own await at ``MCP_CALL_TIMEOUT_SECONDS`` (120s) and raises
#: ``McpServerTimeoutError`` from inside, so this value only fires when the client's own
#: timeout machinery never returns; keeping it larger preserves the client's error shape.
HOST_TRANSPORT_TIMEOUT_SECONDS = 150.0

#: Refusal codes this module emits. Written in the shell's own relayed-refusal shape so the
#: sender relays them verbatim instead of re-decoding anything.
WRITE_TOOL_CODE = "GP_E_HOST_WRITE_TOOL"
LOOP_BUSY_CODE = "GP_E_HOST_LOOP_BUSY"

#: A synchronous transport: ``(tool, arguments) -> the engine's reply``, one ``list[str]``
#: entry per MCP content item (or the shell's coded-refusal text for a refusal this module
#: made on the host's behalf).
GlasspaneTransport = Callable[[str, dict[str, Any]], object]

#: The same contract, awaitable: ``(tool, arguments) -> awaitable of the engine's reply``.
#: This is the shape the round's publish site needs, because that site is already inside the
#: loop that owns the MCP session.
GlasspaneAsyncTransport = Callable[[str, dict[str, Any]], Awaitable[object]]


def _refusal(code: str, message: str, remedy: str) -> str:
    """One refusal, in the shape ``glasspane_reverification`` already parses.

    ``CODE: message | remedy: R`` is the shipped ``glasspane-mcp`` error line
    (``mcp-shell/src/errors.ts:formatToolError``), so a host-side refusal classifies as
    ``refused`` with its remedy intact exactly like an engine one — and it cannot be
    mistaken for an evidence pack or a diagnosis.
    """
    return f"{code}: {message} | remedy: {remedy}"


def _read_only_remedy() -> str:
    return (
        "re-verification may only read; ask one of "
        + ", ".join(READ_ONLY_TOOLS)
        + " — a tool that moves the app belongs behind the host's permission prompt, "
        "not inside an error path"
    )


def _loop_is_running() -> bool:
    """True when this thread is inside an event loop (the normal case at the publish site)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _drive(coro: Any, *, tool: str, window: float) -> object:
    """Drive one async MCP call from a thread that owns no loop, under a bounded window."""
    try:
        return asyncio.run(asyncio.wait_for(coro, timeout=window))
    except asyncio.TimeoutError as exc:
        # Deliberately the client's own timeout type: the sender relays a transport failure
        # as GP_E_CHANNEL with its remedy, and a host-side backstop must not look like a
        # different accident.
        raise McpServerTimeoutError(
            f"GlassPane re-verification call '{tool}' did not finish within "
            f"{window:g}s of the host's synchronous window"
        ) from exc


def find_glasspane_server(manager: object, server_name: object = None) -> tuple[str | None, str]:
    """The server this run can re-verify through, plus the reason it cannot.

    Discovery is by *advertised tool*, not by name: the re-verification channel exists only
    where the shell's read-only tools actually live. ``server_name`` overrides discovery when
    a run names the server itself. A ``None`` answer always comes with a reason an operator
    can act on — the second value is never empty.
    """
    if manager is None:
        return None, (
            f"the run has no MCP client manager in its tool metadata "
            f"({MCP_MANAGER_METADATA_KEY!r}), so no GlassPane channel is reachable from the "
            "publish site"
        )
    list_statuses = getattr(manager, "list_statuses", None)
    if not callable(list_statuses):
        return None, "the object under the harness's mcp_manager key is not an MCP client manager"

    statuses: list[Any] = list(list_statuses())
    if not statuses:
        return None, (
            "the run configured no MCP servers at all; add the glasspane-mcp server to the "
            "harness's mcp config to make re-verification possible"
        )

    named = str(server_name) if isinstance(server_name, str) and server_name.strip() else None
    if named is not None:
        for status in statuses:
            if getattr(status, "name", None) == named:
                if getattr(status, "state", None) == "connected":
                    return named, ""
                return None, (
                    f"the named GlassPane MCP server '{named}' has state "
                    f"{getattr(status, 'state', 'unknown')!r}"
                    + (f": {getattr(status, 'detail', '')}" if getattr(status, "detail", "") else "")
                )
        return None, f"the named GlassPane MCP server '{named}' is not configured in this run"

    read_only = set(READ_ONLY_TOOLS)
    candidates: list[Any] = []
    for status in statuses:
        tools = {getattr(tool, "name", "") for tool in getattr(status, "tools", []) or []}
        if tools & read_only:
            candidates.append(status)
    if not candidates:
        return None, (
            "no configured MCP server advertises a GlassPane read-only tool ("
            + ", ".join(READ_ONLY_TOOLS)
            + "); configured servers: "
            + ", ".join(f"{getattr(s, 'name', '?')}={getattr(s, 'state', '?')}" for s in statuses)
        )
    connected = [status for status in candidates if getattr(status, "state", None) == "connected"]
    if connected:
        return str(getattr(connected[0], "name", "")), ""
    # A server that advertises the tools but never connected: say which one, and why, rather
    # than reporting an absence we did not measure.
    status = candidates[0]
    detail = getattr(status, "detail", "")
    return None, (
        f"MCP server '{getattr(status, 'name', '?')}' advertises the GlassPane read-only tools "
        f"but has state {getattr(status, 'state', 'unknown')!r}"
        + (f": {detail}" if detail else "")
    )


def make_host_transport(manager: object, server_name: str, *, timeout: float | None = None) -> GlasspaneTransport:
    """A synchronous read-only transport over ``manager.call_tool_parts``.

    The returned callable is what :data:`.reverify_trigger.GLASSPANE_TRANSPORT_KEY` wants.
    ``timeout`` overrides :data:`HOST_TRANSPORT_TIMEOUT_SECONDS` (tests use it to exercise
    the backstop window without waiting for it).
    """
    window = HOST_TRANSPORT_TIMEOUT_SECONDS if timeout is None else timeout

    def call_tool(tool: str, arguments: dict[str, Any]) -> object:
        if tool not in READ_ONLY_TOOLS:
            # Before the coroutine exists: a refused call must be a call that never was.
            return _refusal(
                WRITE_TOOL_CODE,
                f"the re-verification transport does not send {tool!r}; it is not one of the "
                "engine's read-only tools",
                _read_only_remedy(),
            )
        if _loop_is_running():
            return _refusal(
                LOOP_BUSY_CODE,
                "the re-verification publish site is running inside the event loop that owns "
                f"the MCP session for server '{server_name}', and a synchronous caller cannot "
                "await it without blocking that loop",
                "give the re-verification channel its own stdio session (a McpClientManager "
                "connected on a thread with no running loop), or await "
                "iterate.glasspane_reverification.run_reverification_async from this loop "
                "before the round publishes — which is what the publish site does through "
                f"the awaitable transport this module binds under {GLASSPANE_ACALL_KEY!r}",
            )
        parts = getattr(manager, "call_tool_parts", None)
        if not callable(parts):
            return _refusal(
                "GP_E_HOST_NO_PARTS_PATH",
                "the run's MCP client exposes no call_tool_parts, so the item boundary of the "
                "engine's reply cannot be carried to the relay",
                "run the harness with iterate_harness.mcp.client.McpClientManager (or a client "
                "whose call_tool_parts returns one entry per content item)",
            )
        return _drive(parts(server_name, tool, dict(arguments)), tool=tool, window=window)

    return call_tool


def make_host_acall(manager: object, server_name: str) -> GlasspaneAsyncTransport:
    """An asynchronous read-only transport over ``manager.call_tool_parts``.

    This is the transport the round's publish site uses: it awaits on the caller's own loop,
    which is the loop the run's MCP sessions were connected on. No second ``asyncio`` window is
    wrapped around the await here on purpose — ``McpClientManager._bounded_await`` already
    clamps the call at ``MCP_CALL_TIMEOUT_SECONDS`` and raises the client's own error type, and
    a host-side timer around it would decide which of two timeouts the engine reports.
    """

    async def acall_tool(tool: str, arguments: dict[str, Any]) -> object:
        if tool not in READ_ONLY_TOOLS:
            # Before the await: the session is never touched by a tool this channel may not send.
            return _refusal(
                WRITE_TOOL_CODE,
                f"the re-verification transport does not send {tool!r}; it is not one of the "
                "engine's read-only tools",
                _read_only_remedy(),
            )
        parts = getattr(manager, "call_tool_parts", None)
        if not callable(parts):
            return _refusal(
                "GP_E_HOST_NO_PARTS_PATH",
                "the run's MCP client exposes no call_tool_parts, so the item boundary of the "
                "engine's reply cannot be carried to the relay",
                "run the harness with iterate_harness.mcp.client.McpClientManager (or a client "
                "whose call_tool_parts returns one entry per content item)",
            )
        return await parts(server_name, tool, dict(arguments))

    return acall_tool


def host_transport(metadata: Mapping[str, Any] | None) -> tuple[GlasspaneTransport | None, str | None]:
    """The transport this run can honestly send through, or ``None`` plus the reason.

    Never raises: a run without a GlassPane channel is a state to report, not an error to
    file, and the publish site must keep working.
    """
    meta: Mapping[str, Any] = metadata or {}
    manager = meta.get(MCP_MANAGER_METADATA_KEY)
    server_name, reason = find_glasspane_server(manager, meta.get(GLASSPANE_SERVER_NAME_KEY))
    if server_name is None:
        return None, reason
    return make_host_transport(manager, server_name), None


def bind_host_transport(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Metadata additions for the publish site: both transports, or a stated absence.

    The round's own metadata is never mutated — the trigger is handed the merged copy, so a
    callable never leaks into the durable ``tool_metadata`` the engine folds across turns.
    An empty result means "this run already says what it means" (a host-bound transport, or
    no manager key at all where the default reason is already the accurate one).

    Both transports are bound from one discovery pass because they are the same channel seen
    from two hosts: the awaitable one is what the publish site uses, the synchronous one is
    what a loop-free host (a CLI call, a test body) uses. Binding only the synchronous one is
    how scenario ① shipped with a send that could never happen — the publish site always runs
    inside the loop, so every round read :data:`LOOP_BUSY_CODE` and the accepting branch stayed
    unexercised in production.
    """
    meta: Mapping[str, Any] = metadata or {}
    additions: dict[str, Any] = {}

    if callable(meta.get(GLASSPANE_TRANSPORT_KEY)) or callable(meta.get(GLASSPANE_ACALL_KEY)):
        # A host that bound its own transport wins; this module adds nothing and says nothing.
        return additions

    manager = meta.get(MCP_MANAGER_METADATA_KEY)
    server_name, reason = find_glasspane_server(manager, meta.get(GLASSPANE_SERVER_NAME_KEY))
    if server_name is None:
        if MCP_MANAGER_METADATA_KEY in meta:
            # The run *has* an MCP plane and it cannot serve re-verification: that reason is more
            # specific than the sender's default, and it is the difference between "the host never
            # wired this" and "this run has no GlassPane server, because X".
            additions[GLASSPANE_TRANSPORT_ABSENT_KEY] = reason or "no GlassPane MCP channel is reachable"
        return additions

    additions[GLASSPANE_TRANSPORT_KEY] = make_host_transport(manager, server_name)
    additions[GLASSPANE_ACALL_KEY] = make_host_acall(manager, server_name)
    return additions


__all__ = [
    "GLASSPANE_SERVER_NAME_KEY",
    "HOST_TRANSPORT_TIMEOUT_SECONDS",
    "LOOP_BUSY_CODE",
    "MCP_MANAGER_METADATA_KEY",
    "READ_ONLY_TOOLS",
    "WRITE_TOOL_CODE",
    "GlasspaneAsyncTransport",
    "GlasspaneTransport",
    "bind_host_transport",
    "find_glasspane_server",
    "host_transport",
    "make_host_acall",
    "make_host_transport",
]
