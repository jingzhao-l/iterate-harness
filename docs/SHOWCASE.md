# iterate-harness Showcase

This page collects concrete ways to use
[iterate-harness](https://github.com/jingzhao-l/iterate-harness) without
overselling the project. Each example is small, reproducible, and easy to
extend.

Prerequisites for every example: a configured model credential
(`export ANTHROPIC_API_KEY=your_key`, or `ih auth login`), and a Git project
around which the review/fix loop runs.

## 1. Repeated review until findings converge (dry-run)

Iterate until no new findings appear, without ever touching the code:

```bash
ih iterate review
```

Inside the TUI REPL (`ih`), the same loop is `/iterate review`, and the live
convergence dashboard shows per-round findings trend and cost while it runs.

## 2. Headless autonomous fix loop

Let the harness review, fix, validate, and loop on its own (each round rolls
back via Git isolation on validation failure):

```bash
ih iterate run
```

If an earlier run was interrupted, resume from the decision log:

```bash
ih iterate resume
```

## 3. Per-project knowledge and personalization

Build the project knowledge base, then teach iterate the project-specific rules
it can't discover on its own (protected paths, risk areas, known-intentional,
extra validation commands, …):

```bash
ih iterate onboard          # model-driven ITERATE.md + config + fingerprints
ih iterate init             # quick config-only init
ih iterate personalize      # 9-category wizard
ih iterate status           # onboarding state + drift check
ih iterate refresh          # re-fingerprint manifests, refresh metadata
```

## 4. CI / PR integration

Render and gate a run as one shareable artifact, and post it to a PR:

```bash
# GitHub Actions annotations + severity-gated exit code
ih iterate report --github --fail-on high

# Post (and later update) a Markdown report as the PR comment
ih iterate report --pr

# Single-file HTML report (convergence curve, diffs) — great as a CI artifact
ih iterate report --html
```

## 5. Batch and scheduled reviews

Review several repos in one pass, ranked worst-first; then register a daily
changed-only quick review:

```bash
ih iterate batch repoA/ repoB/ repoC/

ih iterate schedule add "0 9 * * 1-5" --timezone Asia/Shanghai
```

## 6. Change-scoped quick review and commit gate

Scope a whole loop to the git delta, or gate commits locally:

```bash
# only files changed vs HEAD
ih iterate review --changed

# managed pre-commit hook: 1-round changed-only gate
ih iterate hook install
```

## 7. Auditing the loop

The run is recorded and replayable:

```bash
ih iterate log             # tail the decision log
ih iterate log --trend     # new / fixed / regressed / stubborn findings
ih iterate log --replay    # replay the run chronologically
ih iterate doctor          # skill↔harness dimension-system consistency check
```

## Where to go next

- [`README.md`](../README.md) for install, usage, and architecture.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) for contributor workflow.
- [`CHANGELOG.md`](../CHANGELOG.md) for visible repo changes.

## How to contribute a showcase entry

Good showcase additions are:

- Based on a real workflow you ran.
- Short enough to reproduce locally.
- Honest about prerequisites and limitations.
- Focused on what iterate-harness makes easier, not on generic LLM claims.