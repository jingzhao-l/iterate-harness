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
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _first_existing(candidates: list[Path]) -> Path | None:
    """The first candidate that exists, or ``None`` — never a guessed absolute path."""
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _candidates(name: str) -> list[Path]:
    """Every layout this checkout can live in: main checkout, worktree, subtree copy.

    The old default was one guess — ``REPO.parents[1] / "iterate-skill" / "config"`` — which
    resolves to ``<repo>/iterate-skill/config/…``, a path that exists in no layout anyone uses.
    So the proof refused with "no config" unless an operator typed ``--config``, which is how a
    cross-boundary measurement ends up never run: the producer's own default pointed nowhere.
    """
    roots = [REPO, *REPO.parents]
    return [root / name for root in roots if (root / name).exists()] or [REPO.parents[1] / name]


_DEFAULT_CONFIG = _first_existing(_candidates("config/iterate.config.yaml"))
_DEFAULT_HARNESS = _first_existing(_candidates("GlassPane/harness/glasspane-harness"))
DEFAULT_CONFIG = _DEFAULT_CONFIG if _DEFAULT_CONFIG is not None else REPO.parents[1] / "iterate-skill" / "config" / "iterate.config.yaml"
DEFAULT_HARNESS = _DEFAULT_HARNESS if _DEFAULT_HARNESS is not None else REPO.parents[1] / "GlassPane" / "harness" / "glasspane-harness"

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

#: The second door: no environment variable at all, just a project directory, so the fork's
#: `readRunPlan(env, projectDirectory)` fallback is what answers — the branch that reads
#: `<dir>/.iterate/run-plan.json`, which is where this project's own producer writes when nobody has
#: handed a plan in. `readRunPlan({}, …)` rather than `process.env`, because inheriting the parent's
#: environment would make this channel measure the other one.
DRIVER_PROJECT = """
import { readRunPlan } from "%KERNEL%"
const read = readRunPlan({}, process.argv[2])
if (!read.ok) {
  process.stdout.write(JSON.stringify({ ok: false, error: read.error }))
  process.exit(0)
}
process.stdout.write(JSON.stringify({ ok: true, plan: read.plan }))
"""


def _produce_plan(config: Path, scopes: tuple[str, ...], target: Path) -> tuple[Path, str, list[str]]:
    """Write the plan with this project's real producer, at `target`; return (path, digest, ids).

    `target` is the whole point of the second channel below: the producer must be able to write the
    path the reader looks in, so the file this proof compares is one the real code found by the real
    rule, not one the harness handed to it through an environment variable.
    """
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
    written = write_run_plan(plan, str(target))
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
        kernel = (harness / "packages/opencode/src/tool/glasspane/kernel").resolve()
        selftest = os.environ.get("GP_CHANNEL_SELFTEST", "")

        # Two channels, one expectation. The environment hand-off is the older one; the project file is
        # what a run configured by whoever wrote `iterate.config.yaml` actually gets — and until now
        # nothing had measured it across the boundary: each side's own tests feed it its own bytes, so
        # the only untested claim was the one that matters, that Python's writer and the fork's reader
        # agree on the path, the shape and the digest.
        try:
            env_path, our_digest, our_ids = _produce_plan(config, tuple(args.scope), outdir / "run-plan.json")
            project_root = outdir / "project"
            (project_root / ".iterate").mkdir(parents=True, exist_ok=True)
            file_path, digest_again, ids_again = _produce_plan(
                config, tuple(args.scope), project_root / ".iterate" / "run-plan.json"
            )
        except Exception as error:  # noqa: BLE001 — producer failure is a real outcome, say so
            print(f"check_run_plan_channel: this project's own producer could not make a plan: {type(error).__name__}: {error}")
            return 2
        if (digest_again, ids_again) != (our_digest, our_ids):
            print("check_run_plan_channel: this project's producer wrote two different plans to two paths;")
            print("  the two shells would not even be planning the same run. Nothing to compare across the boundary yet.")
            return 1

        if selftest == "corrupt":
            # The red the digest exists for: a plan that is still perfectly valid, in a different
            # order. A schema check cannot see this and neither can a reader that only counts ids, so
            # if this control does not go red on BOTH channels the digest is decoration.
            for produced in (env_path, file_path):
                tampered = json.loads(produced.read_text(encoding="utf-8"))
                tampered["dimensions"] = list(reversed(tampered["dimensions"]))
                produced.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")

        print(f"plan produced here : {', '.join(our_ids)}  digest {our_digest}")
        for channel in (
            Channel("env hand-off (GLASSPANE_RUN_PLAN)", DRIVER, env_path, {"GLASSPANE_RUN_PLAN": str(env_path)}, [], "env"),
            Channel("project file (<dir>/.iterate/run-plan.json)", DRIVER_PROJECT, file_path, {}, [str(project_root)], "project"),
        ):
            rc = _measure_channel(
                bun=bun,
                harness=harness,
                kernel=kernel,
                outdir=outdir,
                channel=channel,
                our_ids=our_ids,
                our_digest=our_digest,
                selftest=selftest,
            )
            if rc != 0:
                return rc
        print("\ncheck_run_plan_channel: both channels agree — the same bytes, the same ids in the same order, the same digest.")
        return 0


@dataclass(frozen=True)
class Channel:
    """One way the fork can be handed a plan, and what it should say it came from."""

    label: str
    driver: str
    produced: Path
    env: dict[str, str]
    argv: list[str]
    expect_source: str


def _measure_channel(
    *,
    bun: str,
    harness: Path,
    kernel: Path,
    outdir: Path,
    channel: Channel,
    our_ids: list[str],
    our_digest: str,
    selftest: str,
) -> int:
    """Run the product's own reader once, and compare what it found with what this project wrote.

    Returns 0 when they agree, 1 when they disagree (the finding that matters), 2 when nothing was
    measured — the reader not running is never reported as agreement.
    """
    # Absolute module path: the driver lives in a temp dir, and a relative import would resolve
    # against that dir instead of the harness — which is how the first run of this proof died
    # (`Cannot find module`). Writing the driver into the product tree was rejected: this script must
    # not leave a file inside another checkout, and an untracked file there is exactly how a later
    # `add -A` misfires.
    driver = outdir / f"driver-{channel.expect_source}.mjs"
    driver.write_text(channel.driver.replace("%KERNEL%", str(kernel)), encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key != "GLASSPANE_RUN_PLAN"}
    environment.update(channel.env)
    proc = subprocess.run(
        [bun, str(driver), *channel.argv],
        cwd=str(harness),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    print(f"\n--- channel: {channel.label}")
    print(f"  read from        : {channel.produced}")
    if proc.returncode != 0 or not proc.stdout.strip():
        print("check_run_plan_channel: the fork's readRunPlan did not run, so this channel was NOT measured.")
        print((proc.stderr or proc.stdout).strip()[-1500:])
        return 2
    try:
        their = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("check_run_plan_channel: the driver printed something that is not JSON; nothing was compared.")
        print(proc.stdout[:400])
        return 2

    if not their.get("ok"):
        # Named apart from the `except Exception as error` binding above: Python unbinds an
        # except-clause name at the end of its block, and reusing it here is both a mypy error and a
        # readability trap.
        refusal = their.get("error") or {}
        print(f"  fork refused     : {refusal.get('code')}: {refusal.get('message')}")
        print(f"  remedy           : {refusal.get('remedy')}")
        print("check_run_plan_channel: the two sides do not agree — the plan this project writes is not a plan the fork will read.")
        return 1
    plan = their.get("plan") or {}
    their_digest = str(plan.get("digest", ""))
    their_ids = [str(i) for i in plan.get("ids", [])]
    their_source = str(plan.get("source", ""))
    print(f"  fork says source : {their_source or '(absent)'}")
    print(f"  their digest     : {their_digest}")
    print(f"  their ids        : {', '.join(their_ids)}")
    if their_source != channel.expect_source:
        print(f"check_run_plan_channel: the fork read this plan but named its source {their_source!r}, not "
              f"{channel.expect_source!r} — the channel that answered is not the one being measured.")
        return 1
    if selftest == "mismatch":
        # A second shape of red, and the one a digest alone would mask: the other side answers with
        # the same bytes but one id dropped. If this does not go red, the script is comparing a hash
        # and calling that agreement.
        their_ids = their_ids[:-1]
    if their_ids != our_ids or their_digest != our_digest:
        print("check_run_plan_channel: MISMATCH — ids or digest differ across the boundary.")
        print("  A digest difference means one side is canonicalising different bytes; an id difference")
        print("  means the plan the fork holds is not the plan this project wrote. Either way the")
        print("  coverage line two shells print would be about two different runs.")
        return 1
    print("  channel agrees   : same bytes, same ids in the same order, same digest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
