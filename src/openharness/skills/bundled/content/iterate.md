---
description: Autonomous code iteration - multi-dimension review, atomic fixes, validation loop with deterministic convergence (dry-run read-only review or autonomous fix loop)
---
# iterate

Autonomous code iteration harness: repeated multi-dimension review until
findings converge, then (normal mode) atomic fixes validated each round.

## Tools
- `iterate_config` — effective iterate.config.yaml (defaults + project overrides)
- `iterate_validate` — run a PRECONFIGURED validation command (EXACT match only)
- `iterate_decision_log` — append-only decision log (.iterate/decision-log.jsonl)
- `iterate_context` — SKILL.md / ITERATE.md / personalization context
- `iterate_review` — deterministic engine: plan / aggregate / meta-review
- `iterate_triage` — interactive y/n/a findings triage; `a` persists to known_intentional

## Modes
- **dry-run**: read-only review. Multi-round convergence, auditable report,
  meta-review verdict. NEVER modifies files. Use for "review only", CI
  pre-checks, baselines.
- **normal**: autonomous loop. Review → fix atomic findings (single file,
  single function, <= atomic.max_lines) → validate → repeat. Architectural
  findings are reported, never auto-fixed. Validation failure rolls back
  the round.

## Canonical dry-run loop
1. `iterate_review(operation="plan", mode="dry-run")` → dimensions ×
   reviewer prompts × findings schema.
2. Review round: spawn one `agent` per dimension IN THE SAME turn (parallel),
   feeding already-known findings so reviewers hunt NEW issues only.
3. `iterate_review(operation="aggregate", mode="dry-run", rounds=[...])` —
   deterministic dedupe / known_intentional filter / severity sort /
   convergence stats.
4. 0 new findings in the last round → converged, stop; else next round
   (capped by maxReviewRounds).
5. `iterate_review(operation="meta-review", report=...)` audits the report;
   verdict is `approved` only when every consistency check passes.
6. Append exactly ONE `report` entry to the decision log.
7. Offer `iterate_triage(findings=[...])` when the user wants to accept /
   reject / permanently ignore individual findings — `a` answers are
   persisted to known_intentional and filtered from future rounds.

## Findings schema
{ "dimension", "file" (relative), "line"?, "severity": critical|high|medium|low,
  "summary", "failure_scenario", "suggested_fix", "is_atomic" }

## Hard rules
- Reviewers NEVER write files. Fixers (normal mode only) make the smallest
  possible change and only for `is_atomic` findings.
- Validation commands must match `validation.commands` EXACTLY — prefixes
  are rejected by design.
- Every round and the final report land in the append-only decision log.
