#!/usr/bin/env python3
"""Live check: does this machine's GlassPane install answer iterate-harness over MCP?

Scenario 1 of 综述 §8.5 (an iterate invariant failing → GlassPane re-verifies) does not
need a fork patch or a kernel release: iterate-harness is already an MCP client
(`mcp/config.py` merges settings and plugin servers) and `glasspane-mcp` is already a
stdio server exposing the whole GlassPane tool surface. This script exercises that path
with the harness's own `McpClientManager` — the class the product uses at runtime — so a
green run means the wiring works through the real code path, not a hand-rolled handshake.

It is an operator command, deliberately NOT a pytest case. A test that skips when the
product is not installed is the failure mode this repo just spent a batch removing
(`test_matches_the_kernel_fixture` had been skipping forever). Here the absence of
GlassPane is a loud non-zero exit, and the CI lane that needs no product is
`tests/test_mcp/test_stdio_flow.py`, which drives the same client against a fake server.

Usage:
    uv run python scripts/check_glasspane_channel.py
    ITERATE_GLASSPANE_MCP=/path/to/glasspane-mcp uv run python scripts/check_glasspane_channel.py

Exit codes: 0 the surface is reachable and answered, 1 the server started but a required
check failed, 2 the server binary is missing (nothing was proven either way).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from iterate_harness.mcp.client import McpClientManager
from iterate_harness.mcp.types import McpStdioServerConfig

_MCP_PIN_RE = re.compile(r'"mcp==([^"]+)"')

#: The tool names the GlassPane MCP shell advertises. A missing one means the harness's
#: view of GlassPane has lost a capability, which is a product regression, not a flake.
REQUIRED_TOOLS = (
    "gp_probe_status",
    "gp_attach",
    "gp_observe",
    "gp_act",
    "gp_diagnose",
    "gp_last_evidence",
    "gp_snapshot",
    "gp_restore",
)

#: Read-only calls: they must answer even when no app is attached, and they must answer
#: with the engine's own words rather than a wrapper's summary.
READ_ONLY_CALLS = ("gp_probe_status", "gp_last_evidence")


def server_command() -> str | None:
    override = os.environ.get("ITERATE_GLASSPANE_MCP")
    if override:
        return override
    return shutil.which("glasspane-mcp")


class _ChannelUnmeasured(Exception):
    """Control flow: the connection failed, so stop measuring and say that."""


def _sdk_pin_hint() -> str | None:
    """Name an off-pin MCP SDK, because that is what this failure actually was.

    The client reads `tool.input_schema`, which exists on the pinned `mcp` and not on the
    1.x spelling (`inputSchema`). The mismatch is invisible unless the versions are
    printed, and it looks exactly like a server that advertises nothing.
    """
    try:
        from importlib.metadata import version

        installed = version("mcp")
    except Exception:  # noqa: BLE001 — the hint is optional, the failure is not
        return None
    pinned: str | None = None
    try:
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        # Regex, not line-splitting: the dependency line is `"mcp==2.0.0",`, and stripping
        # quotes without the comma yields a pinned version of `2.0.0",` — a hint with a
        # syntax error in it, which is worse than no hint.
        match = _MCP_PIN_RE.search(text)
        if match is not None:
            pinned = match.group(1)
    except OSError:
        pass
    if pinned is not None and pinned != installed:
        return (
            f"likely cause: the installed `mcp` is {installed} but pyproject pins {pinned}; "
            "the client speaks one spelling and the other SDK releases the field differently"
        )
    return None



async def run(command: str) -> int:
    manager = McpClientManager({"glasspane": McpStdioServerConfig(type="stdio", command=command, args=[])})
    await manager.connect_all()
    failures: list[str] = []
    try:
        statuses = manager.list_statuses()
        for status in statuses:
            line = f"  server {status.name}: state={status.state} tools={len(status.tools)}"
            if status.detail:
                line += f" detail={status.detail}"
            print(line)
        dead = [status for status in statuses if status.state != "connected"]
        tools = manager.list_tools()
        names = {tool.name for tool in tools}
        print(f"glasspane-mcp at {command}: {len(names)} tool(s) visible through McpClientManager")
        if dead:
            # A server that never came up says nothing about what it advertises. Reporting
            # "required tool not advertised" here would be a false statement about
            # GlassPane — measured 2026-10-12, when an off-pin `mcp` made every stdio
            # server fail on a field rename and this script blamed the tool surface.
            details = "; ".join(f"{d.name}: {d.detail or d.state}" for d in dead)
            failures.append(f"server(s) did not connect, so the tool surface was NOT measured — {details}")
            hint = _sdk_pin_hint()
            if hint:
                failures.append(hint)
            raise _ChannelUnmeasured()
        for required in REQUIRED_TOOLS:
            if required not in names:
                failures.append(f"required tool not advertised: {required}")

        for call in READ_ONLY_CALLS:
            if call not in names:
                continue
            raw: Any = await manager.call_tool("glasspane", call, {})
            text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            print(f"\n--- {call} ---\n{text[:600]}")
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                failures.append(f"{call} did not return JSON: {text[:120]!r}")
                continue
            if not isinstance(payload, dict):
                failures.append(f"{call} returned {type(payload).__name__}, expected an object")
                continue
            if call == "gp_last_evidence":
                # The pack must arrive as the engine wrote it: a consumer that cannot see
                # schemaVersion/operationId/attribution has nothing to transcribe.
                pack = payload.get("evidencePack")
                if isinstance(pack, dict):
                    for field in ("schemaVersion", "operationId", "attribution", "circuitBreaker"):
                        if field not in pack:
                            failures.append(f"gp_last_evidence pack is missing {field}")
                elif "code" in payload:
                    print(
                        f"  note: engine answered {payload.get('code')} — structured refusal, "
                        "which is a valid answer, not a transport failure"
                    )
                else:
                    failures.append("gp_last_evidence returned neither an evidencePack nor a coded refusal")
    except _ChannelUnmeasured:
        pass
    finally:
        await manager.close()

    if failures:
        print("\nFAILURES:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nglasspane channel OK: the harness can see and call the GlassPane tool surface.")
    return 0


def main() -> int:
    command = server_command()
    if command is None:
        print(
            "cannot measure: glasspane-mcp is not installed and ITERATE_GLASSPANE_MCP is unset.\n"
            "  Install GlassPane (installer/cli.js) or point the variable at the binary.",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(run(command))


if __name__ == "__main__":
    raise SystemExit(main())
