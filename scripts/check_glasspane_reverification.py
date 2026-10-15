"""Prove scenario 1's sending line end to end, against the live engine.

WHAT IS ACTUALLY BEING PROVEN. The kernel's `iterate.reverification/0.1` request is built
by this project from a **real** invariant run (`check_invariants` on a directory that
really fails), then sent through the harness's own `McpClientManager` to the same
`glasspane-mcp` the product ships, and the answer is relayed by
`iterate.glasspane_reverification`. Nothing in the middle is faked: the operation ids
come from the engine's own recent reports, and the verdict printed at the end is the
transcription of the pack the engine returned.

Operator command, not a pytest: it needs a running glasspane-mcp. Exit codes:
0 the request went and the engine answered in a shape the relay understood;
1 it went and something about the answer does not hold up (that is a real finding);
2 nothing was measured (no server, no channel, no operations to ask about).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iterate_harness.defensive.invariants import check_invariants  # noqa: E402
from iterate_harness.iterate.glasspane_reverification import run_reverification_async  # noqa: E402
from iterate_harness.iterate.invariant_report import (  # noqa: E402
    from_defensive_report,
    re_verification_request_from_targets,
)
from iterate_harness.mcp.client import McpClientManager  # noqa: E402
from iterate_harness.mcp.types import McpStdioServerConfig  # noqa: E402


def _server_command() -> str | None:
    return os.environ.get("ITERATE_GLASSPANE_MCP") or shutil.which("glasspane-mcp")


async def _run(command: str) -> int:
    manager = McpClientManager({"glasspane": McpStdioServerConfig(type="stdio", command=command, args=[])})
    await manager.connect_all()
    statuses = manager.list_statuses()
    for status in statuses:
        print(f"  server {status.name}: state={status.state} tools={len(status.tools)}" + (f" detail={status.detail}" if status.detail else ""))
    dead = [status for status in statuses if status.state != "connected"]
    if dead:
        print(f"check_glasspane_reverification: no channel came up ({dead[0].detail or dead[0].state}); nothing was sent.")
        return 2

    async def call_tool(name: str, arguments: dict[str, Any]) -> object:
        return await manager.call_tool("glasspane", name, arguments)

    try:
        root = Path(__file__).resolve().parents[1]
        # A real failing run, not a staged one: README.md exists here, the second
        # assertion points at a file this repo does not have, and the command is a
        # process that really exits 1.
        ensure = ["README.md", "not-a-real-file-in-this-repo.md"]
        commands = {"python": ["python3 -c 'raise SystemExit(1)'"]}
        defensive = check_invariants(root, ensure=ensure, commands=commands)
        report = from_defensive_report(ensure, commands, defensive)
        print(f"real invariant run: outcome={report.outcome} failing={len(report.checks) - sum(1 for c in report.checks if c.ok)} of {len(report.checks)}")

        # Which operations to ask about comes from the engine, not from a fixture.
        recent_raw = await call_tool("gp_recent_reports", {})
        recent = recent_raw if isinstance(recent_raw, str) else json.dumps(recent_raw)
        operation_ids: list[str] = []
        try:
            payload = json.loads(recent)
            entries = payload.get("reports") or payload.get("operations") or []
            if isinstance(entries, list):
                for entry in entries:
                    candidate = entry.get("operationId") if isinstance(entry, dict) else None
                    if isinstance(candidate, str) and candidate.startswith("op_"):
                        operation_ids.append(candidate)
        except json.JSONDecodeError:
            pass
        if not operation_ids:
            single = await call_tool("gp_last_evidence", {})
            text = single if isinstance(single, str) else json.dumps(single)
            try:
                pack = (json.loads(text) or {}).get("evidencePack") or {}
                if isinstance(pack.get("operationId"), str):
                    operation_ids = [pack["operationId"]]
            except json.JSONDecodeError:
                pass
        if not operation_ids:
            print("check_glasspane_reverification: the engine reported no recorded operation to re-verify; nothing was sent.")
            print(f"  gp_recent_reports said: {text_head(recent)}")
            return 2

        operation_ids = operation_ids[:2]
        request = re_verification_request_from_targets(report, {"operationIds": operation_ids})
        if request is None:
            print("check_glasspane_reverification: the invariant report passed, so the kernel built no request — send nothing.")
            return 2
        print(f"request built: trigger={request['trigger']['kind']} digest={request['trigger']['reportDigest'][:20]}…")
        print(f"targets      : operationIds={', '.join(operation_ids)}")

        run = await run_reverification_async(request, call_tool)
        for line in run.to_model_lines():
            print(line)
        if not run.attempted:
            print("check_glasspane_reverification: the runner reported not-attempted; nothing was measured.")
            return 2
        relayed = [answer for answer in run.answers if answer.state in ("answered", "refused")]
        if not relayed:
            print("check_glasspane_reverification: the engine answered in a shape the relay could not classify.")
            return 1
        answered = [answer for answer in relayed if answer.state == "answered"]
        refused = [answer for answer in relayed if answer.state == "refused"]
        print(
            f"\ncheck_glasspane_reverification: sent 1 request, {len(relayed)}/{len(run.answers)} answer(s) relayed "
            f"({len(answered)} evidence/diagnosis, {len(refused)} coded refusal(s))."
        )
        return 0
    finally:
        await manager.close()


def text_head(value: object, limit: int = 240) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit] + ("…" if len(text) > limit else "")


def main() -> int:
    command = _server_command()
    if command is None:
        print("check_glasspane_reverification: glasspane-mcp is not installed and ITERATE_GLASSPANE_MCP is unset.")
        print("  Remedy: `npm i -g glasspane-mcp`, or ITERATE_GLASSPANE_MCP=/path/to/mcp-shell/dist/index.js.")
        return 2
    return asyncio.run(_run(command))


if __name__ == "__main__":
    raise SystemExit(main())
