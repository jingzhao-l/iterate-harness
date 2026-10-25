#!/usr/bin/env python3
"""Proof that ④'s gate answer survives the round it was made in, and that both doors can read it.

WHY A SEPARATE PROOF. The unit tests pin the shape: `test_reverify_wiring` drives the real aggregate
call site and asserts the ledger row exists, `test_glasspane_ledger_link` asserts the row is not read
as fix evidence. Neither of those statements is the integration the README makes — **"the answer is
recorded, and `ih iterate gate` reads it back"** — because a test that imports the package is not the
installed package, and a reader that lives in the same process as the writer proves nothing about the
terminal. So this script does what the operator does: run a real round, then ask the shipped CLI.

Run it against the wheel the user actually has:

    ~/.iterate-harness-npm/venv/bin/python scripts/check_gate_answer_reader.py
    PYTHONPATH=src python3 scripts/check_gate_answer_reader.py        # the checkout instead

Exit codes: 0 the round passed, its answer reached the ledger, and the CLI printed it;
1 it reached the ledger but a door could not read it, or the row was misread as fix evidence
(that is the README claim breaking, which is the red that matters);
2 nothing was measured (the project could not be built, or no `ih` is on PATH and none was given).
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from iterate_harness.defensive.kernel import DEFENSIVE_KERNEL_KEY, DefensiveKernel
from iterate_harness import __version__ as package_version
from iterate_harness.iterate.config_loader import load_effective_config
from iterate_harness.iterate.decision_log import read_entries
from iterate_harness.iterate.glasspane_recipe import render_gate_state
from iterate_harness.iterate.reverify_trigger import (
    RECIPE_GATE_STATE_KEY,
    REVERIFICATION_STATE_KEY,
)

GATE_ROW_KIND = "recipe-gate"
ABSENCE_SENTENCE = "no recipe gate recorded yet"


def _build_passing_project(root: Path) -> None:
    """A project whose invariants really pass, so the round sends nothing and answers ④ anyway."""
    (root / "README.md").write_text("# real project\n", encoding="utf-8")
    (root / "iterate.config.yaml").write_text(
        "invariants:\n  ensure:\n    - README.md\nreviewer:\n  evidence_validation: false\n",
        encoding="utf-8",
    )


async def _run_one_round(root: Path) -> dict[str, object]:
    from iterate_harness.iterate.loop_policy import ITERATE_STATE_KEY
    from iterate_harness.tools.base import ToolExecutionContext
    from iterate_harness.tools.iterate_tools import IterateReviewInput, IterateReviewTool

    kernel = DefensiveKernel(root, load_effective_config(root))
    report = await kernel.check_invariants()
    if not report.passed:
        raise SystemExit(
            "check_gate_answer_reader: the fixture project's invariants did not pass, so this run "
            "cannot show the passing branch. Nothing was measured."
        )

    context = ToolExecutionContext(cwd=root)
    context.metadata[DEFENSIVE_KERNEL_KEY] = kernel
    await IterateReviewTool().execute(
        IterateReviewInput(operation="aggregate", rounds=[{"round": 1, "findings": []}], max_review_rounds=2),
        context,
    )
    published: dict[str, object] = context.metadata[ITERATE_STATE_KEY][REVERIFICATION_STATE_KEY]
    print(f"round published     : spawned={published.get('spawned')} reason={published.get('reason')!r}")
    gate = published.get(RECIPE_GATE_STATE_KEY)
    if isinstance(gate, dict):
        refusal = gate.get("refusal") if isinstance(gate.get("refusal"), dict) else {}
        print(
            "④'s answer (transient): "
            f"allowed={gate.get('allowed')} verdict={gate.get('invariantState')} "
            f"refusal={(refusal or {}).get('code')}"
        )
    return published


def _gate_rows(root: Path) -> list[dict[str, object]]:
    return [entry.data for entry in read_entries(root) if entry.data.get("kind") == GATE_ROW_KIND]


def _ask_cli(project: Path, cli: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            [cli, "iterate", "gate"],
            cwd=str(project),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        # A named CLI that cannot be executed is "nothing measured about the door", not a red door:
        # reporting it as a refusal would blame the reader for a PATH problem.
        raise _MissingCli(f"{cli!r} could not be executed: {error}") from error
    return proc.returncode, (proc.stdout or proc.stderr).strip()


class _MissingCli(Exception):
    """The terminal door could not be asked at all — the exit code for that is 2, not 1."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ih",
        default=shutil.which("ih"),
        help="the shipped CLI to ask (default: `ih` on PATH; the venv's own bin/ih counts)",
    )
    args = parser.parse_args()

    print(f"iterate_harness in use : {package_version}")
    print(f"CLI asked             : {args.ih or '(none on PATH)'}")

    with tempfile.TemporaryDirectory(prefix="gp-gate-reader-") as tmp:
        project = Path(tmp)
        _build_passing_project(project)
        asyncio.run(_run_one_round(project))

        rows = _gate_rows(project)
        if len(rows) != 1:
            print(
                f"\ncheck_gate_answer_reader: expected exactly one `{GATE_ROW_KIND}` row for one passing "
                f"round, found {len(rows)}.\n"
                "  A round that answered ④ without recording it is the 2.11.0 shape: the answer lived only "
                "in that round's payload, and both doors kept answering "
                f"“{ABSENCE_SENTENCE}…” for a project that had already answered."
            )
            return 1
        row = rows[0]
        if "operationId" in row or "outcome" in row:
            print(
                "\ncheck_gate_answer_reader: the gate row carries a field `fix_proof` reads as proof "
                "(operationId / outcome). The gate would open itself — the next round would cite the "
                "loop's own answer as evidence that a fix happened."
            )
            return 1
        print(f"ledger row recorded     : kind={row.get('kind')} spawned={row.get('spawned')} "
              f"verdict={row.get('invariantState')}")

        session_door = render_gate_state(project)
        if ABSENCE_SENTENCE in session_door:
            print("\ncheck_gate_answer_reader: the session door could not read back what the round recorded.")
            print(f"  it said: {session_door.splitlines()[0]}")
            return 1
        print("door: /iterate gate   : reads it back")

        if not args.ih:
            print(
                "\ncheck_gate_answer_reader: no `ih` on PATH, so the terminal door was NOT measured. "
                "Install the published wrapper (npm i -g iterate-harness) or pass --ih <path>; "
                "this exit code means nothing was proven about the CLI, not that the CLI is fine."
            )
            return 2
        try:
            rc, text = _ask_cli(project, args.ih)
        except _MissingCli as missing:
            print(f"\ncheck_gate_answer_reader: {missing} — the terminal door was not measured.")
            return 2
        print(f"door: ih iterate gate   : rc={rc}")
        for line in text.splitlines():
            print(f"  {line}")
        if rc != 0 or ABSENCE_SENTENCE in text:
            print("\ncheck_gate_answer_reader: the terminal door did not read the recorded answer back.")
            return 1
        print("\ncheck_gate_answer_reader: a passing round's ④ answer is recorded, and both doors read it.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
