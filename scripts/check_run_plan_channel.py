"""Proof that the run-plan channel works end-to-end across the two projects.

WHY A SEPARATE PROOF. Each side's tests assert its own half: iterate-harness digests a plan
the same way the kernel does, and the fork's `readRunPlan` refuses paths it cannot honour.
Neither of those statements is the integration. The integration is: **the bytes this
project's real producer writes are the bytes the other project reads, and both compute the
same digest.** A shared contract that has never been run across the boundary is a claim, so
this script runs it — with the repo's own `iterate.config.yaml` as input, not a fixture.

Operator command, not a pytest: the fork may legitimately not be on this machine.

    python3 scripts/check_run_plan_channel.py                     # default config + harness
    python3 scripts/check_run_plan_channel.py --config ... --harness ...

Exit codes: 0 = both sides read the same plan and agree on the digest;
1 = they disagree (the channel is broken — this is the red that matters);
2 = nothing was measured (no bun, no fork checkout, the plan could not be written).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO.parents[1] / "iterate-skill" / "config" / "iterate.config.yaml"
DEFAULT_HARNESS = REPO.parents[1] / "GlassPane" / "harness" / "glasspane-harness"

#: Run inside the harness with bun, so the fork's own TypeScript doorway is what answers.
#: It imports the product's module — not a copy — because the point is to measure the code
#: that ships, and `readRunPlan` is where the path rules and the digest live.
DRIVER = """
import { readRunPlan } from "%KERNEL%"
const read = readRunPlan(process.env)
if (!read.ok) {
  process.stdout.write(JSON.stringify({ ok: false, error: read.error }))
  process.exit(0)
}
process.stdout.write(JSON.stringify({ ok: true, plan: read.plan }))
"""


def _produce_plan(config: Path, scopes: tuple[str, ...], outdir: Path) -> tuple[Path, str, list[str]]:
    """Write the plan with this project's real producer; return (path, digest, ids)."""
    sys.path.insert(0, str(REPO / "src"))
    from iterate_harness.iterate.config_loader import load_config
    from iterate_harness.iterate.run_plan import build_run_plan, run_plan_digest, write_run_plan

    # `load_config` takes a project ROOT and returns None for a missing or
    # non-mapping file — so the None has to be refused here. Passing it on would
    # surface as an AttributeError inside the producer, which reads like a bug in
    # the producer rather than the honest "this config did not load".
    loaded = load_config(config.parent)
    if loaded is None:
        raise RuntimeError(f"{config} did not load as a mapping (missing file, bad YAML, or not a dict)")
    plan = build_run_plan(loaded, scopes=scopes, source_name=config.name)
    written = write_run_plan(plan, outdir / "run-plan.json")
    payload = json.loads(Path(written.path).read_text(encoding="utf-8"))
    ids = [str(d["id"]) for d in payload["dimensions"]]
    return Path(written.path), run_plan_digest(payload), ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="the iterate config to plan from")
    parser.add_argument("--harness", default=str(DEFAULT_HARNESS), help="path to harness/glasspane-harness")
    parser.add_argument("--scope", action="append", default=[], help="restrict to a configured dimension_sets scope")
    args = parser.parse_args(argv)

    config = Path(args.config)
    if not config.is_file():
        print(f"check_run_plan_channel: no config at {config} — nothing to plan from.")
        return 2
    harness = Path(args.harness)
    if not harness.is_dir():
        print(f"check_run_plan_channel: no GlassPane harness at {harness}.")
        print("  Remedy: --harness /path/to/harness/glasspane-harness, or clone GlassPane. Not measuring is not agreeing.")
        return 2
    bun = shutil.which("bun")
    if bun is None:
        print("check_run_plan_channel: bun is not on PATH, so the fork side cannot run.")
        return 2

    with tempfile.TemporaryDirectory(prefix="gp-plan-channel-") as tmp:
        outdir = Path(tmp)
        try:
            path, our_digest, our_ids = _produce_plan(config, tuple(args.scope), outdir)
        except Exception as error:  # noqa: BLE001 — producer failure is a real outcome, say so
            print(f"check_run_plan_channel: this project's own producer could not make a plan: {type(error).__name__}: {error}")
            return 2

        # Absolute module path: the driver itself lives in a temp dir, and a relative
        # import would resolve against that dir instead of the harness — which is how the
        # first run of this proof died (`Cannot find module`). Writing the driver into the
        # product tree was rejected: this script must not leave a file inside another
        # checkout, and an untracked file there is exactly how a later `add -A` misfires.
        selftest = os.environ.get("GP_CHANNEL_SELFTEST", "")
        if selftest == "corrupt":
            # The red the digest exists for: a plan that is still perfectly valid, in a
            # different order. A schema check cannot see this and neither can a reader who
            # only counts ids — so if this control does not go red, the digest is decoration.
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["dimensions"] = list(reversed(tampered["dimensions"]))
            path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")

        driver = outdir / "driver.mjs"
        kernel = (harness / "packages/opencode/src/tool/glasspane/kernel").resolve()
        driver.write_text(DRIVER.replace("%KERNEL%", str(kernel)), encoding="utf-8")
        proc = subprocess.run(
            [bun, str(driver)],
            cwd=str(harness),
            env={**os.environ, "GLASSPANE_RUN_PLAN": str(path)},
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            print("check_run_plan_channel: the fork's readRunPlan did not run, so the channel was NOT measured.")
            print((proc.stderr or proc.stdout).strip()[-1500:])
            return 2
        try:
            their = json.loads(proc.stdout)
        except json.JSONDecodeError:
            print("check_run_plan_channel: the driver printed something that is not JSON; nothing was compared.")
            print(proc.stdout[:400])
            return 2

        print(f"plan produced here : {path}")
        print(f"  dimensions       : {', '.join(our_ids)}")
        print(f"  our digest       : {our_digest}")
        if not their.get("ok"):
            # Named apart from the `except Exception as error` binding above: Python
            # unbinds an except-clause name at the end of its block, and reusing it here is
            # both a mypy error and a readability trap.
            refusal = their.get("error") or {}
            print(f"fork refused     : {refusal.get('code')}: {refusal.get('message')}")
            print(f"  remedy         : {refusal.get('remedy')}")
            print("\ncheck_run_plan_channel: the two sides do not agree — the plan this project writes is not a plan the fork will read.")
            return 1
        plan = their.get("plan") or {}
        their_digest = str(plan.get("digest", ""))
        their_ids = [str(i) for i in plan.get("ids", [])]
        print(f"  their digest     : {their_digest}")
        print(f"  their ids        : {', '.join(their_ids)}")
        if selftest == "mismatch":
            # A second shape of red, and the one a digest alone would mask: the other side
            # answers with the same bytes but one id dropped. If this does not go red, the
            # script is comparing a hash and calling that agreement.
            their_ids = their_ids[:-1]
        if their_ids != our_ids or their_digest != our_digest:
            print("\ncheck_run_plan_channel: MISMATCH — ids or digest differ across the boundary.")
            print("  A digest difference means one side is canonicalising different bytes; an id difference")
            print("  means the plan the fork holds is not the plan this project wrote. Either way the")
            print("  coverage line two shells print would be about two different runs.")
            return 1
        print("\ncheck_run_plan_channel: the same bytes, the same ids in the same order, the same digest.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
