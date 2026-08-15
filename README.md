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
  <img src="https://img.shields.io/badge/version-1.0.0-brightgreen" alt="Version">
</p>

---

## 🚀 Quick Start

```bash
# one-click install (macOS / Linux / WSL)
curl -fsSL https://raw.githubusercontent.com/jingzhao-l/iterate-harness/main/scripts/install.sh | bash

# launch the TUI
oh

# inside the REPL
/iterate review        # dry-run: read-only multi-round review until convergence
```

CLI-first instead:

```bash
oh iterate init          # detect the project, generate iterate.config.yaml
oh iterate review        # headless dry-run (stream-json output available)
oh iterate review --changed # quick review: only files changed vs --ref (default HEAD)
oh iterate run           # headless autonomous fix loop
oh iterate resume        # resume the last session
oh iterate log           # tail the decision log
oh iterate log --trend   # cross-run finding trend (new/fixed/regressed/stubborn)
oh iterate log --replay  # replay the whole run chronologically (relative timestamps)
oh iterate report        # render the final report (CI mode, see below)
oh iterate report --html # single-file HTML report (convergence curve, diffs, shareable)
oh iterate batch a/ b/   # review multiple repos sequentially, rank worst-first
oh iterate schedule add "0 9 * * 1-5" # daily changed-only quick review (cron, UTC)
```

Set your API key first: `export ANTHROPIC_API_KEY=your_key` (OpenAI-compatible
providers are also supported — see `oh --help`).

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
| **Finding trend library** | Every finished run fingerprints findings (`file\|line\|dimension`) into `.iterate/trend-library.json`; `oh iterate log --trend` / `/iterate trend` report new / fixed / regressed / stubborn (3+ runs) findings across runs |
| **Breakpoint resume** | The TUI startup panel summarizes the last finished run (verdict, rounds, severity buckets, last intervention) and `/iterate resume` continues from the decision log with re-verification of still-reproducing findings |
| **CI / PR mode** | `oh iterate report --github --fail-on high` turns the final report into GitHub Actions annotations with a severity-based exit-code gate for PRs |
| **Changed-only quick review** | `--changed [--ref <ref>]` (CLI + `/iterate review --changed`) pins the whole loop to the git delta: the kickoff, review plan and every reviewer prompt carry the explicit changed-file listing |
| **Batch ranking** | `oh iterate batch repoA repoB …` reviews multiple repos sequentially and ranks them worst-first by a severity-weighted score; one failing repo never kills the batch |
| **Scheduled review** | `oh iterate schedule add "0 9 * * 1-5"` registers a cron job that runs the changed-only quick review daily (UTC) with `--clean-ok`; new-vs-stubborn findings surface via the trend library |
| **HTML single-file report** | `oh iterate report --html` renders the run as ONE offline `.html` file: SVG convergence curve, severity/dimension bars, findings table with failure scenarios, and colorized per-fix diffs — share it as a CI artifact |
| **Decision replay** | `oh iterate log --replay` re-plays the run chronologically with relative timestamps (`[+90s] r1 review_result newFindings=3`) — watch how the loop unfolded like a recording |
| **Per-dimension resources** | `dimension_resources` in `iterate.config.yaml` sets per-dimension `model` / `concurrency` (1–8) / `token_budget` — a strong model for security, a fast one for style-tests; the plan carries them into every reviewer spawn |
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
src/openharness/
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

- **macOS / Linux / WSL**: `bash scripts/install.sh` (clone + venv + editable
  install, links `oh` and `iterate-harness` into `~/.local/bin`)
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
