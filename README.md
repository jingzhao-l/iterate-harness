<h1 align="center">
  <img src="assets/logo.png" alt="iterate-harness" width="64" style="vertical-align: middle;">
  <br>
  <code>iterate-harness</code>
</h1>

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

**iterate-harness** is a dedicated agent harness for the iterate review/fix
loop: repeated multi-dimension code review until findings **converge**,
deterministic aggregation, atomic fixes validated every round, and an
append-only decision log that makes every iteration auditable.

It is a focused fork of [OpenHarness](https://github.com/HKUDS/OpenHarness)
(v0.1.9, MIT): the kernel agent loop, React TUI, tool/skill/plugin systems
and permission layer are inherited; the iterate semantic layer (ported from
the iterate skill's TypeScript implementation) plus the engine-level
convergence policy are layered on top.

<p align="center">
  <a href="#-quick-start"><img src="https://img.shields.io/badge/Quick_Start-5_min-blue?style=for-the-badge" alt="Quick Start"></a>
  <a href="#-iterate-features"><img src="https://img.shields.io/badge/Iterate-6_tools-ff69b4?style=for-the-badge" alt="Iterate Tools"></a>
  <a href="#-iterate-features"><img src="https://img.shields.io/badge/Modes-dry--run_|_normal-61DAFB?style=for-the-badge" alt="Modes"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-≥3.10-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/React+Ink-TUI-61DAFB?logo=react&logoColor=white" alt="React">
  <img src="https://img.shields.io/badge/version-1.6.0-brightgreen" alt="Version">
</p>

---

## 🚀 Quick Start

```bash
# install (npm wrapper; needs Node + Python >= 3.10)
npm install -g iterate-harness
# ...or no-Node one-liner (macOS / Linux / WSL, Python only)
# curl -fsSL https://raw.githubusercontent.com/jingzhao-l/iterate-harness/main/scripts/install.sh | bash

# launch the TUI
ih

# inside the REPL
/iterate review        # dry-run: read-only multi-round review until convergence
```

CLI-first instead:

```bash
ih iterate onboard       # MODEL-DRIVEN project scan -> ITERATE.md knowledge base + config + fingerprints
ih iterate onboard --no-ai # detection-only fallback (no model call, channel=cli)
ih iterate status        # config + onboarding state + drift check
ih iterate refresh       # re-fingerprint manifests, report drift, refresh metadata
ih iterate reonboard     # backup, full model re-scan, preserve your user-owned region
ih iterate personalize   # 9-category wizard: constraints -> config + ITERATE.md user region
ih iterate init          # detect the project, generate iterate.config.yaml (config only)
ih iterate review        # headless dry-run (stream-json output available)
ih iterate review --changed # quick review: only files changed vs --ref (default HEAD)
ih iterate run           # headless autonomous fix loop
ih iterate resume        # resume the last session
ih iterate log           # tail the decision log
ih iterate log --trend   # cross-run finding trend (new/fixed/regressed/stubborn)
ih iterate log --replay  # replay the whole run chronologically (relative timestamps)
ih iterate report        # render the final report (CI mode, see below)
ih iterate report --pr    # post/update the report as a PR comment (gh CLI, idempotent)
ih iterate report --html # single-file HTML report (convergence curve, diffs, shareable)
ih iterate batch a/ b/   # review multiple repos sequentially, rank worst-first
ih iterate schedule add "0 9 * * 1-5" # daily changed-only quick review (cron, UTC)
ih iterate hook install  # managed pre-commit hook: 1-round changed-only gate
```

Onboarding note: `ih iterate onboard` first gates on a configured model
credential (`ih auth login`), then lets the model explore the project with its
read tools (manifests, 2-3 level directory tree, specs/tests/CI, README —
never `.env`/keys) and write `ITERATE.md` with byte-exact AI-maintained /
user-owned region markers. The harness itself validates the markers, captures
SHA-256 manifest fingerprints and writes `iterate.config.yaml` — untrusted
model output never touches trusted config structure. Both files are
byte-compatible with the skill's onboarding (same markers, same
`onboarding.fingerprints` schema), so projects onboarded by either ecosystem
interoperate. Every later loop kickoff injects the `ITERATE.md` knowledge
base into the system prompt, and a drifted manifest (dependency bump, stack
change) triggers a non-blocking warning before reviews.

Set your API key first: `export ANTHROPIC_API_KEY=your_key` (OpenAI-compatible
providers are also supported — see `ih --help`).

## ✨ Iterate Features

| Capability | What it does |
| --- | --- |
| **Deterministic review engine** | `iterate_review` plan / aggregate / meta-review: cross-round dedupe, `known_intentional` filtering, severity sort, convergence math, 6-check report audit — all pure computation, zero LLM judgment |
| **Two modes** | `dry-run` (read-only review, never touches files) and `normal` (review → atomic fix → validate → loop, validation failure rolls the round back via git isolation) |
| **Engine-enforced convergence** | `IterateLoopPolicy` lives in the kernel query loop: round caps, convergence auto-stop and next-round steering cannot be prompt-injected away |
| **Convergence dashboard** | Live React TUI panel: per-round findings trend, per-dimension counts, running USD cost, converged badge |
| **Findings triage** | `iterate_triage`: walk findings with `y` fix / `n` skip / `a` always-ignore; `a` persists to `known_intentional` so future rounds filter it automatically |
| **Cost transparency** | Token usage → per-round and cumulative USD from a built-in price table (overridable per model) |
| **Security boundaries as code** | `protected_paths` and `forbidden_fix_patterns` from settings are auto-assembled into the permission layer (deny path rules + write-payload regex); validation commands run through an EXACT-match allowlist |
| **Per-fix diff approval** | `require_fix_approval` routes every file write during a normal-mode loop through an interactive prompt with an inline diff preview — even in full-auto mode; hard denials are never downgraded |
| **Esc intervention** | Press Esc mid-loop: the loop pauses at the next round boundary and opens a directional-key menu (skip top finding / narrow dimensions / stop / resume); a second Esc force-interrupts the turn |
| **Finding trend library** | Every finished run fingerprints findings (`file\|line\|dimension`) into `.iterate/trend-library.json`; `ih iterate log --trend` / `/iterate trend` report new / fixed / regressed / stubborn (3+ runs) findings across runs |
| **Breakpoint resume** | The TUI startup panel summarizes the last finished run (verdict, rounds, severity buckets, last intervention) and `/iterate resume` continues from the decision log with re-verification of still-reproducing findings |
| **CI / PR mode** | `ih iterate report --github --fail-on high` turns the final report into GitHub Actions annotations with a severity-based exit-code gate for PRs; `--pr` posts (and on later runs UPDATES) a Markdown report comment via the gh CLI — every failure mode degrades gracefully, never breaking the exit-code policy |
| **Changed-only quick review** | `--changed [--ref <ref>]` (CLI + `/iterate review --changed`) pins the whole loop to the git delta: the kickoff, review plan and every reviewer prompt carry the explicit changed-file listing |
| **Batch ranking** | `ih iterate batch repoA repoB …` reviews multiple repos sequentially and ranks them worst-first by a severity-weighted score; one failing repo never kills the batch |
| **Scheduled review** | `ih iterate schedule add "0 9 * * 1-5"` registers a cron job that runs the changed-only quick review daily (UTC) with `--clean-ok`; new-vs-stubborn findings surface via the trend library |
| **HTML single-file report** | `ih iterate report --html` renders the run as ONE offline `.html` file: SVG convergence curve, severity/dimension bars, findings table with failure scenarios, and colorized per-fix diffs — share it as a CI artifact |
| **Decision replay** | `ih iterate log --replay` re-plays the run chronologically with relative timestamps (`[+90s] r1 review_result newFindings=3`) — watch how the loop unfolded like a recording |
| **Per-dimension resources** | `dimension_resources` in `iterate.config.yaml` sets per-dimension `model` / `concurrency` (1–8) / `token_budget` — a strong model for security, a fast one for style-tests; the plan carries them into every reviewer spawn |
| **Token budget enforcement** | `token_budget` caps the whole run at the engine level (hard-stop + closing report); `iterate_review(operation="aggregate", dimension_usage=…)` audits per-dimension usage, relays reviewer-reported totals into the engine cost meter (with per-dimension USD estimates at the model's blended price), and steers the next round away from exhausted dimensions |
| **Threshold gates** | `thresholds.max_critical` / `max_high` / `max_medium` / `max_low` (global or per dimension) cap finding counts in the final report — a violation flips the verdict to `needs_revision` and fails the `ih iterate report` exit code (`threshold gate: FAIL`) |
| **Schedule timezones** | `ih iterate schedule add "0 9 * * 1-5" --timezone Asia/Shanghai` evaluates the cron in local time (stored UTC-normalized) so "daily at 9" means 9 where you live |
| **Detection-driven init** | `ih iterate init` probes marker files (package.json / pyproject / go.mod / Cargo.toml / …), infers the test command from real evidence, suggests dimensions (frontend deps unlock `frontend-backend` / `ui-ux`), previews the yaml and writes it only after confirmation — `/iterate init` does the same in the TUI |
| **Model-driven onboarding** | `ih iterate onboard` chains auth gate → detection evidence → model scan → `ITERATE.md` knowledge base (AI/user region markers) + manifest fingerprints; `refresh` re-fingerprints, `reonboard` re-scans while preserving your notes; every kickoff injects the knowledge base and warns on drift — skill-compatible artifacts. TUI onboarding gets its fingerprints auto-captured on the next review/run — no manual `refresh` needed |
| **Personalization wizard** | `ih iterate personalize` walks the skill's 9 categories (protected paths, risk areas, known-intentional, dimension focus, fix priority, forbidden fixes, notes, conventions, extra validation commands): structured rules land in `iterate.config.yaml` (protected paths ALSO enforced by the kernel permission layer), free text lands in the `ITERATE.md` user region, and every kickoff carries the constraints; extra commands pass a strict whitelist before merging into `validation.commands` |
| **Pre-commit hook** | `ih iterate hook install` writes a MARKED managed `.git/hooks/pre-commit` that runs a 1-round changed-only review and gates the commit on `--fail-on` severity; refuses to touch foreign hooks, skippable via `ITERATE_SKIP_HOOK=1` / `--no-verify` |
| **Decision log** | Append-only `.iterate/decision-log.jsonl`: every round, fix, validation and triage decision is recorded |
| **Project knowledge** | `ITERATE.md` project knowledge + per-project structured personalization (9 categories) |

## 🔧 The six iterate tools

- `iterate_config` — effective config (defaults + `iterate.config.yaml` overrides)
- `iterate_validate` — run a preconfigured validation command (EXACT match only)
- `iterate_review` — deterministic engine: plan / aggregate / meta-review
- `iterate_decision_log` — append-only decision log
- `iterate_context` — SKILL.md / ITERATE.md / personalization context
- `iterate_triage` — interactive y/n/a findings triage with `known_intentional` persistence

Slash command `/iterate` (status / review / run / log / config / validate) and
the bundled `iterate` skill provide the same loops through different entries.

## 🧭 Architecture

```
src/iterate_harness/
├── iterate/            # semantic layer (Python port of the TS skill)
│   ├── review.py       # dedupe / known_intentional filter / severity sort / convergence
│   ├── meta_review.py  # 6-check report consistency audit
│   ├── config_loader.py# Master + Overrides merge
│   ├── validate.py     # EXACT-match validation runner
│   ├── decision_log.py # append-only JSONL
│   ├── loop_policy.py  # engine-level convergence enforcement + cost meter
│   ├── personalization.py # 9-category per-project store
│   ├── worktree_flow.py# git isolation: enter/commit/exit + rollback
│   └── prompts.py      # canonical dry-run/normal loop templates
├── engine/             # kernel agent loop (upstream + iterate control block)
├── permissions/        # checker + iterate auto-assembly (protected_paths …)
├── tools/iterate_tools.py  # the six iterate_* tools
└── ui/                 # React TUI backend host + review_progress protocol
```

## 📦 Install

- **npm (easiest)**: `npm install -g iterate-harness` — a thin wrapper that
  pip-installs the release tarball into a managed venv (`~/.iterate-harness-npm`)
  on first run and keeps the version in lockstep with the npm package
- **macOS / Linux / WSL**: `bash scripts/install.sh` (clone + venv + editable
  install, links `ih` and `iterate-harness` into `~/.local/bin`)
- **Windows (PowerShell)**: `scripts/install.ps1`
- **From a checkout**: `bash scripts/install_dev.sh`
- Requires Python ≥ 3.10; Node.js ≥ 18 enables the React TUI (skipped
  otherwise — the plain fallback UI still works)

## 🧪 Tests

```bash
python -m pytest tests/test_iterate -q   # semantic layer + kernel integration
python -m pytest -q                      # full suite
```

## 📄 License & Attribution

MIT — same as upstream. iterate-harness is a fork of
[OpenHarness](https://github.com/HKUDS/OpenHarness) maintained at
[jingzhao-l/iterate-harness](https://github.com/jingzhao-l/iterate-harness);
upstream receives full credit for the agent kernel, TUI and extension system.
The iterate semantic layer originates from the
[iterate-skill](https://github.com/jingzhao-l/iterate-skill) project.
