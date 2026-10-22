#!/usr/bin/env python3
"""Run the same fixture through both implementations and compare the answers.

WHY THIS EXISTS. ``tests/kernel_fixtures`` pins the corpus bytes, and each side's own
tests assert against the expectations the fixture states. That catches a implementation
drifting from the *fixture*, but not two implementations drifting from each other on a
case the fixture never wrote down — and the interesting failures are exactly the ones
nobody wrote a case for yet. So this script takes the shipped corpus, drives the Python
modules here, drives the published TypeScript package's built ``dist`` through node, and
compares verdict-by-verdict. Agreement is required on: which cases are refused, the
decision each accepted case produces, and every digest.

This is an operator command, not a pytest. When node or the package is missing it exits
2 and says so — a cross-implementation comparison that quietly skips is how "both sides
agree" stops being checked. A build whose version is not the one the corpus pins is refused
the same way: it is a different contract, not a disagreement about this one.

    PYTHONPATH=src python3 scripts/diff_kernel_implementations.py
    PYTHONPATH=src python3 scripts/diff_kernel_implementations.py --kernel /path/to/kernel

The second form points at a canonical kernel checkout (``src/`` or ``dist/``); without it
the script resolves ``node_modules/iterate-kernel`` from this repo or from the GlassPane
harness install recorded in ``tests/kernel_fixtures/manifest.json``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from iterate_harness.iterate.convergence_policy import (  # noqa: E402
    RoundState,
    convergence_decision,
)
from iterate_harness.iterate.invariant_report import (  # noqa: E402
    invariant_report_digest,
    invariant_report_from_json,
    failing_checks,
    re_verification_request_from_targets,
)
from iterate_harness.iterate.provider_registry import (  # noqa: E402
    empty_registry,
    provider_from_json,
    provider_ids_in_common,
    register_provider,
    require_method,
)
from iterate_harness.iterate.kernel_decision_record import (  # noqa: E402
    KernelDecisionTranscribeError,
    decision_log_draft_from_record,
)

MANIFEST = REPO / "tests" / "kernel_fixtures" / "manifest.json"
CORPUS = REPO / "tests" / "kernel_fixtures"

#: The node driver, as a string so this script stays one file. It reads the fixture paths
#: from argv, prints one JSON document, and uses only the package's public exports.
NODE_DRIVER = r"""
const fs = require("fs");
const [kernelDir, convPath, invPath, provPath, recPath] = process.argv.slice(2);
const conv = require(kernelDir + "/dist/convergence.js");
const inv = require(kernelDir + "/dist/invariant-report.js");
const prov = require(kernelDir + "/dist/provider-registration.js");
const rec = require(kernelDir + "/dist/decision-log-transcribe.js");
const J = (x) => JSON.stringify(x === undefined ? null : x);
const out = { convergence: [], invariant: [], reverify: [], refs: {}, provider: {}, transcribe: [] };

const convFx = JSON.parse(fs.readFileSync(convPath, "utf8"));
const CONV_KEYS = { maxRounds: "max_rounds", invariantState: "invariant_state",
  openFindings: "open_findings", evidenceComplete: "evidence_complete",
  breakerLevel: "breaker_level", schemaVersion: "schema_version" };
for (const item of convFx.cases) {
  let rec;
  try {
    const d = conv.convergenceDecision(item.state);
    rec = { name: item.name, verdict: "accepted",
      answer: J({ action: d.action, reason: d.reason, round: d.round, maxRounds: d.maxRounds }) };
  } catch (e) { rec = { name: item.name, verdict: "refused" }; }
  out.convergence.push(rec);
}

const invFx = JSON.parse(fs.readFileSync(invPath, "utf8"));
for (const item of invFx.cases) {
  let rec;
  try {
    const r = inv.InvariantReportSchema.parse(item.input);
    rec = { name: item.name, verdict: "accepted", digest: inv.invariantReportDigest(r),
      failing: J(inv.failingChecks(r)) };
  } catch (e) { rec = { name: item.name, verdict: "refused" }; }
  out.invariant.push(rec);
}
const base = inv.InvariantReportSchema.parse(invFx.input);
for (const item of invFx.reverificationCases) {
  let rec;
  try {
    const req = inv.reVerificationRequestFrom(base, item.targets);
    rec = { name: item.name, verdict: "accepted", answer: req === null ? "null" : J(req) };
  } catch (e) { rec = { name: item.name, verdict: "refused" }; }
  out.reverify.push(rec);
}
for (const [label, key] of [["fail", "input"], ["pass", "passInput"], ["error", "errorInput"]]) {
  const r = inv.InvariantReportSchema.parse(invFx[key]);
  out.refs[label] = { digest: inv.invariantReportDigest(r), failing: J(inv.failingChecks(r)) };
}
out.passRequest = J(inv.reVerificationRequestFrom(
  inv.InvariantReportSchema.parse(invFx.passInput), { dimensions: ["security"] }));
out.request = J(inv.reVerificationRequestFrom(base, invFx.request.targets));

const provFx = JSON.parse(fs.readFileSync(provPath, "utf8"));
out.provider.cases = provFx.cases.map((item) => {
  try { prov.ProviderRegistrationSchema.parse(item.input); return { name: item.name, verdict: "accepted" }; }
  catch (e) { return { name: item.name, verdict: "refused" }; }
});
const build = (list) => (list || []).reduce((acc, p) => prov.registerProvider(acc, p), prov.emptyProviderRegistry());
const g = build(provFx.registry.glasspane), i = build(provFx.registry.iterate);
out.provider.disjoint = J(prov.providerIdsInCommon(g, i));
const a = build(provFx.registry.collision.a), b = build(provFx.registry.collision.b);
out.provider.collision = J(prov.providerIdsInCommon(a, b));
out.provider.duplicate = (() => {
  try { prov.registerProvider(g, provFx.registry.glasspane[0]); return "accepted"; }
  catch (e) { return "refused:" + (e.code || e.name); }
})();
out.provider.offers = J([
  prov.requireMethod(a, "glasspane-ae", "diagnose").requires,
  prov.requireMethod(b, "glasspane-ae", "diagnose").requires,
]);
out.provider.input = (() => {
  try {
    const p = prov.ProviderRegistrationSchema.parse(provFx.input);
    return { verdict: "accepted", methods: p.methods.map((m) => m.name) };
  } catch (e) { return { verdict: "refused" }; }
})();
const recFx = JSON.parse(fs.readFileSync(recPath, "utf8"));
for (const item of recFx.cases) {
  // Rule 10 of the fixture: size cases repeat a character here rather than carry a literal.
  const record = JSON.parse(JSON.stringify(item.record));
  if (typeof item.blobRepeat === "number") record.data[item.blobKey ?? "blob"] = "x".repeat(item.blobRepeat);
  if (typeof item.summaryRepeat === "number") record.data.summary = "s".repeat(item.summaryRepeat);
  try {
    out.transcribe.push({ name: item.id, verdict: "accepted",
      answer: J(rec.decisionLogDraftFromRecord(record, item.entryId)) });
  } catch (e) {
    // Refusal codes here are KERNEL_E_* on BOTH sides by contract (unlike the provider
    // module's per-language codes), so the code itself is part of what must agree.
    out.transcribe.push({ name: item.id, verdict: "refused", answer: J({ code: e.code }) });
  }
}
process.stdout.write(JSON.stringify(out));
"""


def _artifact_version(kernel: Path) -> str:
    """The version string inside the built artifact the driver is about to require."""
    try:
        return str(json.loads((kernel / "package.json").read_text(encoding="utf-8")).get("version", ""))
    except (OSError, ValueError):
        return ""


def _reject_stale_artifact(kernel: Path, want: str) -> str | None:
    """Return why this build must not be judged against the corpus, or None when it is the pin.

    `tests/kernel_fixtures` is pinned to one published version. Running the ruler against a
    different build does not compare two implementations of one contract — it compares the contract
    at two versions, so every case the newer one added shows up as `TS refused, Python accepted`.
    That reads exactly like a cross-language divergence and invites somebody to "fix" the side that
    is actually right, which is how a publish debt turns into a shipped bug.
    """
    if not want:
        return None
    got = _artifact_version(kernel)
    if not got:
        return (
            f"{kernel} carries no readable version and the corpus pins iterate-kernel@{want}, so "
            f"this build cannot be shown to be the pinned artifact"
        )
    if got != want:
        return (
            f"resolved {kernel} is iterate-kernel@{got} but the vendored corpus pins {want} — the "
            f"wrong artifact, not a disagreement. Publish {want} from the kernel line and re-pin, "
            f"or pass --kernel pointing at a checkout built from it."
        )
    return None


def _kernel_dir(explicit: str | None) -> Path | None:
    """Find a kernel whose *built* dist the driver can require, at the version the corpus pins."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    version = ""
    if MANIFEST.is_file():
        version = str(json.loads(MANIFEST.read_text()).get("version", ""))
    for root in (
        REPO,
        REPO.parent.parent.parent,  # <monorepo>/harness/iterate-harness -> monorepo
        REPO.parent.parent.parent.parent / "GlassPane",
    ):
        for tail in (
            "node_modules/iterate-kernel",
            "harness/glasspane-harness/node_modules/iterate-kernel",
            "harness/glasspane-harness/packages/opencode/node_modules/iterate-kernel",
            "kernel",
        ):
            candidates.append(root / tail)
    stale: list[str] = []
    for cand in candidates:
        if not (cand / "dist" / "convergence.js").is_file():
            continue
        reason = _reject_stale_artifact(cand, version)
        if reason is None:
            return cand
        stale.append(reason)
    for reason in stale:
        print(f"  refused: {reason}", file=sys.stderr)
    print(f"  looked for a built dist under: {', '.join(str(c) for c in candidates[:8])}…", file=sys.stderr)
    if version:
        print(f"  manifest pins iterate-kernel@{version}; a checkout on another ref is not the artifact.", file=sys.stderr)
    return None


def _norm(value: object) -> object:
    """Compare *content*, not two hosts' JSON spacing.

    The answers are carried as JSON text on both sides, and `JSON.stringify` writes
    `{"a":1}` where `json.dumps` writes `{"a": 1}`. Comparing those strings would
    report a disagreement on every single case and teach nothing, so anything that
    parses as JSON is re-rendered in one canonical form before it is compared.
    Something that does not parse (a refusal code, a digest) is compared verbatim.
    """
    if not isinstance(value, str):
        return json.dumps(value, sort_keys=True)
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return value
    return json.dumps(parsed, sort_keys=True)


def _python_answers(conv_fx: dict[str, Any], inv_fx: dict[str, Any], prov_fx: dict[str, Any]) -> dict[str, Any]:
    """Drive the three contracts through the modules in this repo."""
    out: dict[str, Any] = {"convergence": [], "invariant": [], "reverify": [], "refs": {}, "provider": {}}
    keys = {
        "maxRounds": "max_rounds",
        "invariantState": "invariant_state",
        "openFindings": "open_findings",
        "evidenceComplete": "evidence_complete",
        "breakerLevel": "breaker_level",
        "schemaVersion": "schema_version",
    }
    for item in conv_fx["cases"]:
        state = {keys.get(k, k): v for k, v in item["state"].items()}
        try:
            d = convergence_decision(RoundState(**state))
            out["convergence"].append(
                {
                    "name": item["name"],
                    "verdict": "accepted",
                    "answer": json.dumps(
                        {
                            "action": d.action,
                            "reason": d.reason,
                            "round": d.round,
                            "maxRounds": d.max_rounds,
                        },
                        sort_keys=True,
                    ),
                }
            )
        except Exception:  # noqa: BLE001 - the comparison is about *that* it refused
            out["convergence"].append({"name": item["name"], "verdict": "refused"})

    base = invariant_report_from_json(inv_fx["input"])
    for item in inv_fx["cases"]:
        try:
            report = invariant_report_from_json(item["input"])
            out["invariant"].append(
                {
                    "name": item["name"],
                    "verdict": "accepted",
                    "digest": invariant_report_digest(report),
                    "failing": json.dumps(failing_checks(report)),
                }
            )
        except Exception:  # noqa: BLE001
            out["invariant"].append({"name": item["name"], "verdict": "refused"})
    for item in inv_fx["reverificationCases"]:
        try:
            req = re_verification_request_from_targets(base, item["targets"])
            out["reverify"].append(
                {
                    "name": item["name"],
                    "verdict": "accepted",
                    "answer": "null" if req is None else json.dumps(req, sort_keys=True),
                }
            )
        except Exception:  # noqa: BLE001
            out["reverify"].append({"name": item["name"], "verdict": "refused"})
    for label, key in (("fail", "input"), ("pass", "passInput"), ("error", "errorInput")):
        report = invariant_report_from_json(inv_fx[key])
        out["refs"][label] = {
            "digest": invariant_report_digest(report),
            "failing": json.dumps(failing_checks(report)),
        }
    passing = invariant_report_from_json(inv_fx["passInput"])
    out["passRequest"] = json.dumps(re_verification_request_from_targets(passing, {"dimensions": ["security"]}), sort_keys=True)
    out["request"] = json.dumps(re_verification_request_from_targets(base, inv_fx["request"]["targets"]), sort_keys=True)

    def build(items: list[dict[str, Any]]) -> Any:
        acc = empty_registry()
        for raw in items or []:
            acc = register_provider(acc, provider_from_json(raw))
        return acc

    out["provider"]["cases"] = []
    for item in prov_fx["cases"]:
        try:
            provider_from_json(item["input"])
            out["provider"]["cases"].append({"name": item["name"], "verdict": "accepted"})
        except Exception:  # noqa: BLE001
            out["provider"]["cases"].append({"name": item["name"], "verdict": "refused"})
    reg = prov_fx["registry"]
    glasspane, iterate = build(reg["glasspane"]), build(reg["iterate"])
    out["provider"]["disjoint"] = json.dumps(provider_ids_in_common(glasspane, iterate))
    a, b = build(reg["collision"]["a"]), build(reg["collision"]["b"])
    out["provider"]["collision"] = json.dumps(provider_ids_in_common(a, b))
    try:
        register_provider(glasspane, provider_from_json(reg["glasspane"][0]))
        out["provider"]["duplicate"] = "accepted"
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", type(exc).__name__)
        out["provider"]["duplicate"] = f"refused:{code}"
    out["provider"]["offers"] = json.dumps(
        [
            list(require_method(a, "glasspane-ae", "diagnose").requires),
            list(require_method(b, "glasspane-ae", "diagnose").requires),
        ]
    )
    inp = prov_fx["input"]
    out["provider"]["input"] = {
        "verdict": "accepted",
        "methods": [str(m["name"]) for m in inp["methods"]],
    }
    return out


def _transcribe_answers(rec_fx: dict[str, Any]) -> list[dict[str, str]]:
    """Drive the transcription fixture through this repo's Python module.

    The expected values come from the file, never from here — this function produces *answers*,
    and the comparison below is what decides whether the two implementations agree.
    """
    answers: list[dict[str, str]] = []
    for case in rec_fx["cases"]:
        record = json.loads(json.dumps(case["record"]))
        if "blobRepeat" in case:
            record["data"][case.get("blobKey", "blob")] = "x" * case["blobRepeat"]
        if "summaryRepeat" in case:
            record["data"]["summary"] = "s" * case["summaryRepeat"]
        try:
            draft = decision_log_draft_from_record(record, case["entryId"])
        except KernelDecisionTranscribeError as error:
            answers.append({"name": case["id"], "verdict": "refused", "answer": json.dumps({"code": error.code})})
            continue
        answers.append({"name": case["id"], "verdict": "accepted", "answer": json.dumps(draft)})
    return answers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kernel", help="path to a kernel directory with a built dist/")
    args = parser.parse_args(argv)

    node = shutil.which("node")
    if node is None:
        print("diff_kernel_implementations: node is not on PATH — the TypeScript side cannot be driven.")
        print("  Remedy: install node, or run this where the published package is available.")
        return 2
    kernel = _kernel_dir(args.kernel)
    if kernel is None:
        print("diff_kernel_implementations: no built iterate-kernel dist found — nothing to compare against.")
        print("  Remedy: `npm ci && npm run build` in the kernel checkout, or pass --kernel /path/to/kernel.")
        return 2

    conv_fx = json.loads((CORPUS / "convergence.ok-01.json").read_text())
    inv_fx = json.loads((CORPUS / "invariant-report.ok-01.json").read_text())
    prov_fx = json.loads((CORPUS / "provider-registration.ok-01.json").read_text())
    rec_fx = json.loads((CORPUS / "decision-record-transcription.ok-01.json").read_text())

    driver = Path("/tmp/kernel-diff-driver.cjs") if Path("/tmp").is_dir() else REPO / "kernel-diff-driver.cjs"
    driver.write_text(NODE_DRIVER)
    proc = subprocess.run(
        [node, str(driver), str(kernel), str(CORPUS / "convergence.ok-01.json"),
         str(CORPUS / "invariant-report.ok-01.json"), str(CORPUS / "provider-registration.ok-01.json"),
         str(CORPUS / "decision-record-transcription.ok-01.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print("diff_kernel_implementations: the TypeScript driver failed, so no comparison was made.")
        print(proc.stderr.strip()[-2000:])
        return 2
    theirs = json.loads(proc.stdout)
    ours = _python_answers(conv_fx, inv_fx, prov_fx)
    ours["transcribe"] = _transcribe_answers(rec_fx)

    mismatches: list[str] = []
    for section in ("convergence", "invariant", "reverify", "transcribe"):
        if len(theirs[section]) != len(ours[section]):
            mismatches.append(f"{section}: {len(theirs[section])} answers from TS, {len(ours[section])} from Python")
            continue
        for t, o in zip(theirs[section], ours[section]):
            if t["name"] != o["name"]:
                mismatches.append(f"{section}: case order differs ({t['name']} vs {o['name']})")
                continue
            if t["verdict"] != o["verdict"]:
                mismatches.append(f"{section}/{t['name']}: TS {t['verdict']}, Python {o['verdict']}")
            else:
                # Refused cases carry an `answer` too where the contract says the refusal code
                # is shared (transcription refuses with KERNEL_E_* on both sides). Sections whose
                # refused cases have no `answer` are untouched: the inner guard skips them.
                for field in ("answer", "digest", "failing"):
                    if field in t or field in o:
                        if _norm(t.get(field)) != _norm(o.get(field)):
                            mismatches.append(
                                f"{section}/{t['name']}: {field} TS {_norm(t.get(field))!r} != Python {_norm(o.get(field))!r}"
                            )
    for label in ("fail", "pass", "error"):
        t, o = theirs["refs"][label], ours["refs"][label]
        if t["digest"] != o["digest"] or _norm(t["failing"]) != _norm(o["failing"]):
            mismatches.append(f"refs/{label}: TS {_norm(t['failing'])} != Python {_norm(o['failing'])} (digest {t['digest'][:12]} vs {o['digest'][:12]})")
    for field in ("passRequest", "request"):
        if _norm(theirs[field]) != _norm(ours[field]):
            mismatches.append(f"{field}: TS {_norm(theirs[field])[:120]} != Python {_norm(ours[field])[:120]}")
    for key in ("disjoint", "collision", "offers"):
        if _norm(theirs["provider"].get(key)) != _norm(ours["provider"].get(key)):
            mismatches.append(f"provider/{key}: TS {_norm(theirs['provider'].get(key))!r} != Python {_norm(ours['provider'].get(key))!r}")
    # Refusal *codes* are per-language by design (KERNEL_E_* here, PROVIDER_E_* there);
    # what must agree is that the duplicate was refused and the registry was not mutated.
    t_dup = str(theirs["provider"].get("duplicate"))
    o_dup = str(ours["provider"].get("duplicate"))
    if (t_dup.startswith("refused")) != (o_dup.startswith("refused")) or t_dup.split(":")[0] != o_dup.split(":")[0]:
        mismatches.append(f"provider/duplicate: TS {t_dup} != Python {o_dup}")
    else:
        print(f"  note: duplicate registration refused on both sides — TS {t_dup}, Python {o_dup}")
    if len(theirs["provider"]["cases"]) != len(ours["provider"]["cases"]):
        mismatches.append("provider/cases: different number of answers")
    else:
        for t, o in zip(theirs["provider"]["cases"], ours["provider"]["cases"]):
            if t["verdict"] != o["verdict"]:
                mismatches.append(f"provider/{t['name']}: TS {t['verdict']}, Python {o['verdict']}")
    if _norm(theirs["provider"].get("input")) != _norm(ours["provider"].get("input")):
        mismatches.append(f"provider/input: TS {theirs['provider'].get('input')} != Python {ours['provider'].get('input')}")

    total = (
        len(theirs["convergence"])
        + len(theirs["invariant"])
        + len(theirs["reverify"])
        + len(theirs["transcribe"])
        + len(theirs["provider"]["cases"])
    )
    if mismatches:
        print(f"diff_kernel_implementations: {len(mismatches)} disagreement(s) between {kernel} and this repo:")
        for m in mismatches:
            print(f"  ✗ {m}")
        print("\n  A disagreement is not automatically a bug in Python: the fixture states the rules, so")
        print("  re-read its `comment` and decide which side moved. What is not allowed is shipping both.")
        return 1
    print(
        f"diff_kernel_implementations: {total} case verdicts + digests + registry semantics agree between\n"
        f"  {kernel} (iterate-kernel, TypeScript dist)\n  and this repo's Python modules."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
