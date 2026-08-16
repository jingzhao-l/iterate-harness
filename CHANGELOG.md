# Changelog

All notable changes to iterate-harness should be recorded in this file.

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.

## [Unreleased]

## [1.9.1] - 2026-08-16

Distribution-only patch for the npm wrapper. Found during the 1.9.0 release e2e: on hosts where Python's TLS trust store is broken (typical for macOS python.org installs that never ran `Install Certificates.command`), `pip install <github tarball>` fails certificate verification while every other tool on the machine (curl, Node, browsers) downloads the same URL fine — the wrapper dead-ended with guidance text.

### Fixed

- **Tarball download fallback** (`npm/iterate-harness`): when the pinned GitHub release tarball cannot be pip-installed, the wrapper now retries by downloading the tarball with Node's own https stack (redirect-following, 120s timeout, partial-file cleanup) into `~/.iterate-harness-npm/cache/` and pip-installing the local file. Non-http targets (local path / `ITERATE_HARNESS_INSTALL_URL` overrides) keep the old single-attempt behavior; when both attempts fail, the error carries the original pip diagnosis plus the fallback failure reason.
- **Entry points**: `ih` / `iterate-harness` bin shims now handle the async bootstrap (failures still exit 1 with the same actionable message, never a raw stack trace or unhandled rejection).

## [1.9.0] - 2026-08-16

Closes the v1.24 leftover: the 9-category personalize wizard is now fully usable inside the TUI — no terminal hop required. `/iterate personalize` opens a directional-key menu flow over the same channels the Esc intervention menu uses (select modal + question modal); headless sessions keep the summary + CLI pointer.

### Added

- **Directional-key personalize wizard** (`iterate_harness.iterate.personalize_tui`): `/iterate personalize` now runs an interactive 9-category wizard in the TUI. Main menu lists every category with live entry counts plus Save&finish / Cancel; each category editor supports add (text via the question modal) and per-entry removal; `known_intentional` collects file → dimension (select over the canonical 9) → line (invalid → 0) → reason; `dimension_focus` pairs the dimension select with a focus prompt; `fix_priority_order` reorders via move-to-front (any permutation reachable) plus reset-to-default; `extra_validation_commands` keeps the strict whitelist validation with the rejection reason surfaced in the re-prompt. A final confirm gate offers save / keep-editing / discard. The save chain is byte-identical to the CLI wizard (`save_personalization_to_config` + `update_iterate_md_user_section`); wizard mutations happen on a deep copy, so cancel discards cleanly.
- **`QueryEngine.ask_user_select_channel` / `ask_user_prompt_channel`**: read-only properties exposing the interactive channels to slash commands (both `None` in headless runs).

### Changed

- `/iterate personalize` without interactive channels (headless) still shows the personalization summary and points at `ih iterate personalize`; nothing changes for CLI users.

## [1.8.0] - 2026-08-15

Closes the last v1.22 v2 candidate: the skill↔harness dimension-system consistency check. The 9 review dimensions are defined in six places across the two repositories (canonical yaml, JSON-schema enum, skill wizard constants, harness `ALL_DIMENSIONS`, harness default config, and every project's config references) — any of them can drift during a rename. This release bundles the canonical definitions inside the harness and ships a `doctor` command that answers "are my dimensions consistent?" in one shot, plus a six-source lock test in the skill repository that fails CI on drift.

### Added

- **`ih iterate doctor` / `/iterate doctor`** (`iterate_harness.iterate.dimension_check`): dimension-system consistency check. Loads the bundled canonical `data/dimensions.yaml` (byte-identical to the skill's `config/dimensions.yaml`), verifies the harness-internal constants (`personalize_cmd.ALL_DIMENSIONS` order, `IterateConfig` default set) against it, then validates every dimension reference in the project's effective `iterate.config.yaml`: enabled `dimensions` (unknown key = error, e.g. a `securty` typo), `dimension_resources` / `thresholds.dimensions` keys (unknown = error; configured-but-not-enabled = inert warning), and `personalization` references (`fix_priority_order` / `dimension_focus` / `known_intentional` outside the enabled set = error, mirroring the skill's `scripts/validate.py` semantics). Canonical dimensions not enabled are reported as informational. CLI exits 1 on drift (CI-gateable); the TUI renders the same report.
- **Bundled canonical dimension data** (`iterate_harness/iterate/data/dimensions.yaml`): packaged in the wheel; `load_canonical_dimensions` parses it defensively (missing file / bad yaml / malformed entry / invalid priority are reported as errors, never raised).
- **Six-source dimension lock test** (skill repository, `tests/test_dimension_lock.py`): locks the canonical yaml against the JSON-schema enum, the skill wizard's `ALL_DIMENSIONS` + `DIMENSION_LABELS`, the harness `personalize_cmd` constants, the harness default-config dimensions, `init_wizard.BASE_DIMENSIONS` (subset), and byte-identity of the bundled copy. Extraction is AST/regex/json only — zero third-party dependencies.

### Changed

- **Ruff rule set pinned explicitly** (`[tool.ruff.lint] select = ["E4", "E7", "E9", "F"]`): ruff 0.16 expanded its default rule set, which would have failed the CI quality job on the next dependency resolve (uv.lock is not distributed). The pin keeps the lint scope identical to what the codebase was written against, independent of ruff version bumps.

### Fixed

- Removed an unused `prompts` import in the pause-handler path (`engine/query.py`) and an f-string without placeholders in onboarding output (`iterate/onboard_cmd.py`) — the only two violations under the pinned rule set.

## [1.7.0] - 2026-08-15

Closes the three v1.21 leftovers: giant-PR comment pagination, exact per-dimension USD billing, and per-dimension cost in the TUI convergence panel. Also removes the inherited OpenHarness logo from the READMEs (trademark hygiene for the fork).

### Added

- **Per-dimension USD in the convergence panel**: `ReviewProgressEvent` carries a `dimension_cost_usd` map end-to-end (loop policy → protocol → backend host → React panel). The panel now renders `security 3 ~$0.0210` style per-dimension estimates next to the counts whenever reviewer usage was reported; the header keeps the metered main-loop total.
- **Exact in/out split billing** (`dimension_usage_io`): the `iterate_review` aggregate operation accepts an optional per-dimension input/output split (e.g. `{"security": {"input": 1000, "output": 500}}`). Split dimensions bill at the model's exact input/output prices; bare-total dimensions keep the blended-average estimate. Split reports also feed dimension token-budget audits even without bare totals. All shapes are sanitized defensively — malformed entries drop, never raise.
- `CostMeter.record_dimension_usage(dimension, input_tokens, output_tokens)`: monotonic per-stream max accounting (same anti-double-count discipline as the bare totals).

### Changed

- **PR comment lookup paginates** (`pr_comment._find_marker_comment`): the marker scan now pages through the PR comment list (`per_page=100`, up to a 10-page defensive cap) instead of reading a single page — the idempotent update path keeps working on giant PRs with 100+ comments. Pages are chronological; the highest page's marker wins.
- README (en + zh-CN): removed the inherited OpenHarness logo image (`assets/logo.png` deleted — the fork no longer ships upstream branding); version badges updated to 1.7.0.

## [1.6.0] - 2026-08-15

Personalization parity with the skill: the full 9-category wizard, kernel-enforced protected paths, and fingerprint auto-capture after TUI onboarding.

### Added

- **`ih iterate personalize`** (`iterate_harness.iterate.personalize_cmd`): the skill's 9-category personalization wizard, re-run any time to edit in place (existing config + `ITERATE.md` are pre-loaded as defaults). Structured categories (protected paths, risk areas, known-intentional, dimension focus overrides, fix priority order, forbidden fixes) are written to the `personalization` section of `iterate.config.yaml`; free-text categories (iterate notes, code conventions) are rendered into the `ITERATE.md` user-owned region with exact-header section replacement — manually written user sections survive verbatim. Extra validation commands pass the skill's strict whitelist (forbidden shell metacharacters, allowed tool prefixes, `ITERATE_EXTRA_SAFE_PREFIXES` env extension) BEFORE merging into `validation.commands` + `command_whitelist`, so schema validation can never fail on a hostile entry. `/iterate personalize` in the TUI shows the current personalization summary and points at the CLI wizard.
- **Kernel-enforced protected paths**: `build_permission_checker` (`permissions/checker.py`) now also merges the current project's `personalization.protected_paths` into the deny path rules (normalized to absolute-path globs) — wizard-configured protected paths are a hard permission boundary, not just a prompt-side instruction. Unreadable configs contribute no rules and never crash permission bootstrap.
- **Personalization constraints in every kickoff**: review/run kickoffs append the personalization constraint block (protected paths, risk areas, forbidden fixes, fix priority, dimension focus, known-intentional count) so every loop starts with the project-specific rules in context.
- **Fingerprint auto-capture after TUI onboarding** (`ensure_onboarding_fingerprints`): when `ITERATE.md` exists but the config has no `onboarding.fingerprints` (the TUI `/iterate onboard` path — the slash-command flow cannot run the CLI's synchronous post-scan bookkeeping), the next review/run/drift-check completes the config's `onboarding` section with harness-serialized fingerprints. The model never touches trusted config; users no longer need a manual `ih iterate refresh` after TUI onboarding.
- Defensive config parsing: `load_personalization_from_config` accepts either a full config dict or the personalization section itself, and string lists keep only non-empty `str` entries (hostile YAML degrades to empty instead of coercing).

### Distribution

- **npm distribution wrapper** (`iterate-harness` on npm, maintained at `npm/iterate-harness/` in the iterate-skill monorepo): `npm install -g iterate-harness` exposes `ih` everywhere npm works. The wrapper is a zero-dependency Node shim — first run resolves Python ≥ 3.10 (env override `ITERATE_HARNESS_PYTHON`), creates a managed venv (`~/.iterate-harness-npm`), pip-installs the release tarball **pinned to the npm package version** (lockstep: npm 1.6.0 → harness v1.6.0, npm upgrades self-heal on next run), then delegates to the real `ih` with argv/stdio/signals/exit-code passthrough. Requires network access to pypi.org + github.com for the one-time install; `ITERATE_HARNESS_SKIP_INSTALL=1` runs an existing install directly. 10 unit tests (`node --test`) cover the pure helpers.

### Changed

- README (en + zh-CN): `personalize` in the CLI cheat-sheet, a "Personalization wizard" feature row, the onboarding row now mentions TUI fingerprint auto-capture; version badges updated to 1.6.0.

## [1.5.0] - 2026-08-15

Full onboarding parity with the skill: model-driven `ITERATE.md` generation, manifest fingerprints, drift detection, and knowledge-base injection into every loop.

### Added

- **`ih iterate onboard`** (`iterate_harness.iterate.onboard_cmd`): the complete first-run flow the skill has — auth gate (`ih auth login` guidance, never a bare stack trace) → detection evidence (`init_wizard.detect_project`) → interactive dimension/goal/rounds Q&A (`--yes` skips) → **model-driven project scan**. The model explores the repo with its read tools (manifests, 2-3 level tree, specs/tests/CI, README — the kickoff embeds the skill's sensitive-file deny list `.env`/keys/credentials) and writes `ITERATE.md` using the bundled skeleton (`iterate/data/ITERATE.template.md`) with byte-exact region markers. Harness code then validates marker presence/order, captures SHA-256 manifest fingerprints (15 tracked manifests, skill parity), and serializes `iterate.config.yaml` via `yaml.safe_dump` — untrusted model prose never reaches trusted config structure. `--no-ai` renders a detection-only knowledge base (channel `cli`) without any model call; the AI path records channel `ai`. Both artifacts are byte-compatible with the skill's onboarding output (same markers, same `onboarding.fingerprints` schema) so either ecosystem can read what the other produced.
- **`ih iterate refresh`**: re-captures fingerprints, prints a drift summary (changed/added/removed manifests), updates the config `onboarding` section + the `ITERATE.md` metadata row with rollback on write failure. Never calls the model, never touches the user-owned region.
- **`ih iterate reonboard`**: backs up both artifacts (`*.bak-<timestamp>`), re-runs the full model onboarding with the existing user-owned region embedded verbatim in the kickoff, and restores the backups automatically when the re-scan fails validation.
- **`ih iterate status`** (new CLI subcommand): effective config summary + onboarding state (channel, completed_at, fingerprint count) + drift check result. The TUI `/iterate status` shows the same block.
- **Knowledge-base injection**: `build_runtime_system_prompt` now inlines the project's `ITERATE.md` (found by walking up from cwd, capped at 4000 chars with a pointer to the `iterate_context` tool for the full file) — every review/run kickoff starts with the project knowledge in context, closing the v1.22 gap.
- **Drift warnings**: `ih iterate review` / `run` / `resume` print a non-blocking warning when tracked manifests drifted since onboarding; the TUI `/iterate review|run` start messages carry the same notice.
- TUI `/iterate onboard`: submits the onboarding kickoff inside the current session (no nested runtime); the completion message points at `ih iterate refresh` to record fingerprints.

### Changed

- README (en + zh-CN): onboarding commands in the CLI cheat-sheet, an onboarding explainer paragraph (trust boundary + skill interop), and a "Model-driven onboarding" feature row.

## [1.4.0] - 2026-08-15

Identity migration: the fork now runs under its own name end to end — `openharness` (package/CLI/paths) → `iterate-harness` / `ih`.

### Changed

- **Python package renamed** `openharness` → `iterate_harness` (`src/openharness/` → `src/iterate_harness/`, all 1.8k+ imports and module-path strings across 271 files migrated). Class identifiers and user-visible strings followed (`OpenHarnessSessionBackend` → `IterateHarnessSessionBackend`, banners/messages now brand iterate-harness).
- **CLI entry points replaced**: `oh` / `openh` / `openharness` launchers removed; the package now installs `ih` (short) and `iterate-harness` (full) — `ih iterate review`, `ih iterate report --pr`, … `python -m iterate_harness` works too. All in-repo docs, install scripts (`install.sh` / `install.ps1` / `install_dev.sh`), the managed pre-commit hook (`shutil.which("ih")`), and bundled skill content now reference `ih`.
- **Data directories migrated** `~/.openharness/` → `~/.iterate-harness/` (sessions, settings, themes, plugins, worktrees, teams, copilot auth) and project-level `.openharness/` → `.iterate-harness/`. venv exclude paths and hatch wheel/force-include mappings updated to the new package layout. Existing local data under the old name is not migrated automatically — move it by hand if you have state worth keeping.
- Frontend workspace package renamed `@openharness/terminal` → `@iterate-harness/terminal` (package.json + lockfile).
- README (en + zh-CN) CLI cheat-sheet and feature tables now use `ih`; CONTRIBUTING / SHOWCASE rebranded; historical CHANGELOG / RELEASE_NOTES entries keep the original `oh` wording on purpose (they describe what shipped at the time).
- `pyproject` description keeps the "fork of OpenHarness" attribution; upstream links in README/CONTRIBUTING are preserved.

### Upgrade Notes

- After `git pull`, re-run the installer (or `uv pip install -e .`) so the new `ih` / `iterate-harness` console scripts are generated; stale `oh` shims from previous installs can be deleted.
- Managed pre-commit hooks installed by ≤1.3.0 embed the old absolute `oh` path — run `ih iterate hook uninstall && ih iterate hook install` to re-render them.
- Scheduled quick-review jobs store a `ih iterate review --changed …` command string resolved via PATH at fire time — jobs created by ≤1.3.0 still reference `oh` and must be re-registered (`ih iterate schedule add …`) after upgrading.

## [1.3.0] - 2026-08-15

CI visibility: PR comment mode, medium/low threshold gates, per-dimension USD estimates.

### Added

- PR comment mode (`openharness.iterate.pr_comment` + `oh iterate report --pr`): renders the final report as a Markdown PR comment (marker-anchored, findings table capped at 50 rows, threshold-gate status with inline violations) and posts it via the GitHub CLI. Idempotent — the hidden `<!-- iterate-report -->` marker lets subsequent runs UPDATE the existing comment instead of duplicating one per CI run (`gh api` PATCH by comment id; POST only when none exists yet). Every failure mode degrades to a `skipped` status without raising: gh not installed, no PR context, auth missing, API errors, 60s timeouts — so the `--fail-on` exit-code semantics stay untouched. `--pr` composes with `--github` (annotations + comment) and suppresses the plain-text render. The process boundary is a single injectable runner, keeping the module fully unit-testable.
- Threshold gates extended to `max_medium` / `max_low` (closes the v1.1 note): `thresholds` and `thresholds.dimensions.<dim>` now accept all four severity caps. Parsing (`_parse_threshold_metric`), gate evaluation (`evaluate_threshold_gates` counts findings at EXACTLY each severity, global + per-dimension), and yaml round-trip serialization are driven by one `SEVERITY_METRICS` tuple in `types.py`, so adding a metric later is a one-line change. Invalid values still degrade to per-field errors, never exceptions.
- Per-dimension USD estimates (closes the v1.2 note): `CostMeter.dimension_cost_usd(model)` converts the reviewer-reported per-dimension token totals into estimated USD at the model's blended (input+output)/2 price; `format_summary(dimension_model=…)` appends `(~$X.XXXX)` to each dimension line. Estimates stay OUT of `total_cost_usd` / `total_tokens` (main-loop metered accounting is never polluted by reviewer-reported figures).

### Changed

- `oh iterate report` renders plain text only when neither `--github` nor `--pr` is given.
- README (en + zh-CN): CI/PR 模式 row now mentions `--pr`; version badges updated to 1.3.0.

## [1.2.0] - 2026-08-15

Daily-driver ergonomics: detection-driven init wizard, managed pre-commit hook, engine-level per-dimension usage relay.

### Added

- Detection-driven config wizard (`openharness.iterate.init_wizard` + `oh iterate init` / `/iterate init`): probes project marker files (package.json / pyproject.toml / setup.py / requirements.txt / go.mod / Cargo.toml / Gemfile / pom.xml / build.gradle / composer.json), infers the language stack, derives the test command ONLY from explicit evidence (a real `scripts.test` entry, a pytest table or tests/ layout, a go.mod…), and recommends dimensions (frontend deps unlock `frontend-backend` / `ui-ux`). The wizard prints the evidence lines, previews the yaml, and writes `iterate.config.yaml` only after confirmation (`--yes` skips prompts; `--force` overwrites). Dimension selection accepts 1-based indexes or names (`2,4` / `security ui-ux`); the emitted yaml goes through `yaml.safe_dump` so goal text can never inject yaml structure. Malformed marker files degrade gracefully to evidence notes. Replaces the older marker-lite `init` command (same CLI name, richer behavior; `--defaults` became `--yes`).
- Managed pre-commit hook (`openharness.iterate.git_hook` + `oh iterate hook install|uninstall|status`): writes a MARKED `.git/hooks/pre-commit` that runs ONE dry-run changed-only review round (`oh iterate review --changed --clean-ok --ref HEAD --rounds 1`) and gates the commit via the exact CI exit-code policy (`--fail-on`, default `high`). Refuses to install over or remove foreign hooks (marker check); `ITERATE_SKIP_HOOK=1` / `git commit --no-verify` skips; the `oh` binary is resolved to an absolute path at install time because hook environments often lack the user's PATH. The generated script is pure POSIX sh.
- Engine-level per-dimension usage relay (closes the v1.1 audit gap): `iterate_review(operation="aggregate", dimension_usage=…)` now publishes the reported per-dimension token totals into the loop-policy state; `IterateLoopPolicy.on_turn_end` relays them into the `CostMeter` (monotonic max — running totals never double-count), so `format_summary()` and future progress reporting see reviewer-subagent spend without polluting main-loop token totals. Budget-stop and normal round paths both record; malformed usage maps are dropped defensively.

### Changed

- README (en + zh-CN): feature table gains the detection-driven init / pre-commit hook rows; the token-budget row now mentions the usage relay; the CLI block shows `oh iterate hook install`; version badges updated to 1.2.0.

## [1.1.0] - 2026-08-15

Policy layer: engine-enforced token budgets, project threshold gates, timezone-aware schedules.

### Added

- Whole-run token budget (`token_budget` in `iterate.config.yaml`): a positive integer enforced by the engine-level loop policy — once main-loop usage exceeds it the loop hard-stops with a `token budget exhausted (used/budget tokens)` reason and steers the model to the closing report. Enforcement is snapshot-independent (fires even on a turn without a fresh aggregate); `validate_config` rejects non-positive/non-integer values without killing the loop.
- Per-dimension budget auditing (`openharness.iterate.review.audit_dimension_budgets`): `iterate_review(operation="aggregate", dimension_usage={...})` now accepts reported per-dimension token usage and audits it against `dimension_resources.<dim>.token_budget`. The aggregate output gains a `budgetAudit` block (budget/used/remaining/exceeded per dimension); exhausted dimensions flow into the loop-policy state, where ALL budgets exhausted stops the loop and PARTIAL exhaustion injects an explicit "do NOT spawn reviewer agents for these dimensions" directive into the next-round instruction. Auditing never raises — malformed usage is clamped/skipped.
- Project threshold gates (`thresholds` in `iterate.config.yaml`): `max_critical` / `max_high` cap the number of findings at or above that severity, globally and per dimension (`thresholds.dimensions.<dim>`). Gates are evaluated in `iterate_review(operation="meta-review")` (pure `evaluate_threshold_gates`); a violated gate emits a `thresholdGate` block in the final report, folds one `THRESHOLD_EXCEEDED` meta-review issue per violation, and flips the verdict to `needs_revision`. The canonical-loop prompt directs the model to copy the block into the single report entry; CI (`oh iterate report`) renders `threshold gate: PASS/FAIL` (violations inlined, capped at 5) and fails the exit code via `threshold_exit_code` combined with `severity_gate`. Invalid threshold yaml entries are reported by `validate_config` and skipped.
- Timezone-aware scheduled reviews: `oh iterate schedule add <cron> --timezone <IANA>` (e.g. `Asia/Shanghai`) evaluates the 5-field cron expression in local time and stores the next run UTC-normalized; `next_run_time` / `upsert_cron_job` / `mark_job_run` all honor the job's `timezone` (unknown stored zones fall back to UTC), unknown zones are rejected with `ValueError` at install time, and the scheduler's due check compares against the stored UTC time so a 09:00 Beijing job fires at 01:00 UTC.

### Changed

- README (en + zh-CN): feature table gains the token budgets / threshold gates / schedule timezones rows; version badges updated to 1.1.0.

## [1.0.0] - 2026-08-15

First stable release: shareable single-file HTML report, chronological decision replay, per-dimension resource overrides.

### Added

- Single-file HTML report (`openharness.iterate.html_report` + `oh iterate report --html [path]` / `/iterate report --html`): renders the final report entry plus the fix timeline (atomic_fix / revert / validation entries before the report, capped at 50) into ONE self-contained `.html` file — inline CSS only, zero external requests, no scripts. Includes an inline SVG convergence curve (findings per round with per-point labels), severity + dimension distribution bars, the full findings table (failure scenario + suggested fix per finding), colorized unified diffs from `atomic_fix` entries, and verdict/mode/converged badges. All log-derived text is HTML-escaped; severity colors come from a fixed table so log content cannot inject markup. Default output path: `.iterate/report.html`; the `--fail-on` severity gate still decides the exit code so CI can upload the artifact AND gate on it.
- Decision-log replay (`openharness.iterate.replay` + `oh iterate log --replay` / `/iterate log --replay`): re-plays the whole run chronologically with relative timestamps (`[+90s] r1 review_result newFindings=3`), a per-type one-line summary (goal / findings / fix / command / verdict probes), unknown-type payload previews, and truncation at 140 chars. Unparseable timestamps degrade to `[+?s]`; an empty log prints a friendly placeholder.
- Per-dimension resource overrides (`dimension_resources` in `iterate.config.yaml`): each dimension can set `model` (e.g. a strong model for security, a fast one for style-tests), `concurrency` (clamped to 1–8) and `token_budget` (non-negative). Values are parsed defensively (invalid entries are reported by `validate_config` and skipped, never fatal), flow into the review plan (`DimensionPlan.resources`, serialized in `plan_to_dict`) and are appended to the reviewer prompt as an explicit spawn directive ("Resource plan: model=…; max concurrent reviewer agents=…; token budget=…"). `/iterate config` lists the effective per-dimension resources.

### Changed

- README (en + zh-CN): feature table gains the HTML report / decision replay / per-dimension resources rows; the CLI block shows `oh iterate report --html` and `oh iterate log --replay`; version badges updated to 1.0.0.

## [0.6.0] - 2026-08-15

Unattended scenarios: changed-only quick review, multi-repo batch ranking, scheduled reviews.

### Added

- Changed-only quick review (`openharness.iterate.git_scope`): `oh iterate review --changed [--ref <ref>]` / `oh iterate run --changed` / `/iterate review --changed` collect the delta via `git diff --name-only <ref>` + `git status --porcelain` (renames contribute the new path, only on-disk files qualify, ref tokens are validated against an option-injection pattern, 200-file cap) and pin the entire loop to those files. The kickoff embeds the file list and directs the model to `iterate_review(operation="plan", changed_files=[...])`; the plan then flips to `changed-only` scope and every per-dimension reviewer prompt carries the explicit listing. `--clean-ok` exits 0 on a clean tree for scheduled runs.
- Multi-repo batch review with ranking (`openharness.iterate.batch` + `oh iterate batch <repo...>`): reviews each repo sequentially through the headless print pipeline (per-repo stdout captured, one failing repo never kills the batch), then ranks all repos worst-first by a severity-weighted score (critical 10 / high 5 / medium 2 / low 1). Clean repos and errors are surfaced as their own statuses; `--json` emits machine-readable records, `--full` opts out of changed-only.
- Scheduled quick review (`oh iterate schedule add <cron> [--ref] [--rounds] [--mode] [--timeout]`): registers the `iterate.review-changed` cron job (UTC, 5-field) that runs `oh iterate review --changed --clean-ok` in the repo; `schedule remove` / `schedule status` manage it, and cross-run new-vs-stubborn findings surface via the trend library (`oh iterate log --trend`). Cron jobs gained an optional per-job `timeout` field (default 300s, clamped to [1, 7200]s) honored by `execute_job`.

### Changed

- README (en + zh-CN): feature table gains changed-only quick review / batch ranking / scheduled review rows; the CLI block shows `--changed`, `oh iterate batch` and `oh iterate schedule add`.

## [0.5.0] - 2026-08-15

Data accumulation + UX polish: finding trend library, componentized Esc menu, breakpoint resume.

### Added

- Finding fingerprint trend library (`openharness.iterate.trend_store`): every finished run fingerprints its findings (`file|line|dimension` SHA-1, line optional for file-level findings) into `.iterate/trend-library.json` (atomic write, corrupt data resets to empty, 2000-record LRU prune). Cross-run classification: **new** (first sighting), **fixed** (open before, absent now), **regressed** (fixed before, back now), **stubborn** (open for 3+ runs). Surfaced via `oh iterate log --trend`, `/iterate trend` and `/iterate log trend`; recorded automatically when the report entry lands in the decision log.
- Breakpoint resume: `openharness.iterate.last_state` summarizes the last finished run from the decision log (mode, verdict, rounds, severity buckets, top-3 finding preview, last Esc intervention). The React TUI backend emits a `last_loop_state` event at startup and the new `IterateResumePanel` renders it above the status bar (auto-hidden once a live `review_progress` dashboard appears). `/iterate resume` re-kicks the loop with a resume prompt that embeds the previous verdict/findings and instructs re-verification of still-reproducing findings; `iterate_state` now persists in session snapshots so mid-loop state survives restarts.
- Directional-key Esc intervention menu: new `AskUserSelect` channel end-to-end (`QueryContext.ask_user_select` → `ui.runtime` → backend host `_ask_select`). The TUI renders `select_prompt` modals with the existing SelectModal component (↑↓ navigate, Enter select, number-key quick select, Esc submits the safe first option); the pause menu is now a 4-option componentized menu instead of free-text `s`/`n`/`x` input. Non-TUI frontends keep the free-text question fallback; every decision is still logged as an intervention entry.

### Changed

- README (en + zh-CN): feature table gains the trend library / breakpoint resume rows, the Esc intervention row now describes the directional-key menu, and the CLI block shows `oh iterate log --trend`.

## [0.4.0] - 2026-08-15

Esc mid-loop intervention + dependency vulnerability cleanup.

### Added

- Esc intervention (design §11.2.1): pressing Esc while an iterate loop is running no longer hard-cancels the turn — the loop pauses at the next round boundary and surfaces the intervention menu through the existing question modal: `s` skip the current top finding, `n <dimensions>` narrow the review scope, `x` stop now, empty/anything else resumes the normal loop. A second Esc force-interrupts the current turn. Every intervention decision is appended to the decision log (`type=decision`, `kind=intervention`); headless sessions (no interactive channel) default to a safe stop. `QueryEngine.submit_message` drops stale pause flags so a leftover request cannot fire on a later run.

### Changed

- Dependency security cleanup (all 10 dependabot alerts, 5 high): `autopilot-dashboard` vite `^6.3.2` → `^6.4.3` (pulls patched postcss/esbuild/@babel/core); `frontend/terminal` marked `^18.0.0` → `^18.0.9`, tsx `^4.19.2` → `^4.23.12` (esbuild 0.28.2, ws 8.21.3). Both packages now report 0 npm-audit vulnerabilities; vite build and `tsc --noEmit` verified.

## [0.3.0] - 2026-08-15

Interactive safety for autonomous fixes + CI consumption of the final report.

### Added

- Per-fix diff approval (`Settings.iterate.require_fix_approval`): while a normal-mode loop is active, mutating file tools (`write_file` / `edit_file` / `notebook_edit`, incl. `file_write`/`file_edit` aliases) are routed through the interactive permission prompt with an inline diff preview (clipped to 40 lines / 200 chars per line) — even in full-auto mode. Hard denials (protected paths, forbidden fix patterns) are never downgraded into a confirmation, and dry-run reviews never trigger the gate.
- `openharness.iterate.ci_report`: renders the final `report` entry from the decision log as GitHub Actions workflow commands (per-finding annotations with `file=`/`line=` properties and full workflow-command escaping), plain text, and a severity exit-code gate (`none|low|medium|high|critical`, default `high`). Missing or malformed reports degrade to an empty report instead of failing the pipeline.
- `oh iterate report` CLI subcommand (`--github`, `--fail-on`) and `/iterate report` slash subcommand for REPL parity.

## [0.2.0] - 2026-08-15

First iterate-harness release: a focused fork of OpenHarness v0.1.9 dedicated
to the iterate review/fix loop (semantic layer ported from the iterate skill).

### Added

- `openharness.iterate` semantic layer (Python port of the TS skill): deterministic review engine (plan / aggregate / meta-review), Master+Overrides config loader, EXACT-match validation runner, append-only decision log, per-project structured personalization, cost meter (tokens → USD), git worktree isolation flow, and canonical dry-run/normal loop prompts.
- Six `iterate_*` tools registered in the kernel tool registry: `iterate_config`, `iterate_validate`, `iterate_review`, `iterate_decision_log`, `iterate_context`, `iterate_triage`.
- Engine-level `IterateLoopPolicy` in the kernel query loop: deterministic convergence enforcement, round caps, next-round steering, `ReviewProgressEvent` emission, and cost accumulation — auto-attached when `settings.iterate.enabled` is on.
- `review_progress` protocol event wired through the React TUI backend host: live convergence dashboard panel (per-round findings trend, per-dimension counts, running USD cost, converged badge); print mode emits the same progress to stderr / stream-json.
- `iterate_triage` interactive findings triage: y (fix) / n (skip) / a (always-ignore); `a` answers persist to `known_intentional` personalization and are filtered from future review rounds; headless sessions apply a configured default decision.
- `protected_paths` and `forbidden_fix_patterns` from `Settings.iterate` are auto-assembled into the permission layer via `build_permission_checker` (deny path rules normalized to absolute-path globs + write-payload regex boundary evaluated before tool allowlists).
- `/iterate` slash command (status / review / run / log / config / validate) and `oh iterate init|review|run|resume|log` CLI subcommands; bundled `iterate` skill.
- `ITERATE.md` project knowledge discovery alongside CLAUDE.md; iterate state survives microcompaction via a dedicated compact attachment.

### Changed

- Fork identity: package renamed to `iterate-harness` with an `iterate-harness` console script; README/README.zh-CN rewritten for the fork; install.sh / install.ps1 / install_dev.sh install from `jingzhao-l/iterate-harness` (git clone + editable), register `oh` + `iterate-harness`, and no longer reference the removed `ohmo` / channels stack.

## [0.1.9] - 2026-05-07

### Added

- Added a bundled `skill-creator` skill for creating, improving, and verifying OpenHarness/ohmo skills.
- User-invocable skills can now be triggered directly as slash commands, with support for skill-specific arguments and model override metadata.

### Fixed

- `oh setup` can now update the API key for an already-configured API-key provider profile instead of only changing the model.
- `oh provider edit <profile> --api-key <key>` can now replace a saved profile API key, and `oh provider add ... --api-key <key>` can store one during profile creation.

## [0.1.8] - 2026-05-06

### Added

- Built-in `nvidia` provider profile so `oh setup` offers NVIDIA NIM as a first-class OpenAI-compatible provider choice, with `NVIDIA_API_KEY` auth source, `openai/gpt-oss-120b` as the default model, and the NVIDIA NIM endpoint.
- Built-in `qwen` provider profile so `oh setup` offers Qwen (DashScope) as a first-class provider choice, with `dashscope_api_key` auth source, `qwen-plus` as the default model, and the DashScope OpenAI-compatible endpoint.
- Plugin tool discovery: plugins can now provide `BaseTool` subclasses in a `<plugin>/tools/` directory and they are auto-discovered, instantiated, and registered in the tool registry at runtime. Add `tools_dir` to `plugin.json` (defaults to `"tools"`).
- `oh --dry-run` safe preview mode for inspecting resolved runtime settings, auth state, prompt assembly, commands, skills, tools, and configured MCP servers without executing the model or tools.
- Built-in `minimax` provider profile so `oh setup` offers MiniMax as a first-class provider choice, with `MINIMAX_API_KEY` auth source, `MiniMax-M2.7` as the default model, and `MiniMax-M2.7-highspeed` in the model picker.
- Docker as an alternative sandbox backend (`sandbox.backend = "docker"`) for stronger execution isolation with configurable resource limits, network isolation, and automatic image management.
- Built-in `gemini` provider profile so `oh setup` offers Google Gemini as a first-class provider choice, with `gemini_api_key` auth source and `gemini-2.5-flash` as the default model.
- `diagnose` skill: trace agent run failures and regressions using structured evidence from run artifacts.
- OpenAI-compatible API client (`--api-format openai`) supporting any provider that implements the OpenAI `/v1/chat/completions` format, including Alibaba DashScope, DeepSeek, GitHub Models, Groq, Together AI, Ollama, and more.
- `OPENHARNESS_API_FORMAT` environment variable for selecting the API format.
- `OPENAI_API_KEY` fallback when using OpenAI-format providers.
- GitHub Actions CI workflow for Python linting, tests, and frontend TypeScript checks.
- `CONTRIBUTING.md` with local setup, validation commands, and PR expectations.
- `docs/SHOWCASE.md` with concrete OpenHarness usage patterns and demo commands.
- GitHub issue templates and a pull request template.
- React TUI assistant messages now render structured Markdown blocks, including headings, lists, code fences, blockquotes, links, and tables.
- Built-in `codex` output style for compact, low-noise transcript rendering in React TUI.

### Fixed

- Subprocess teammate spawn (`agent` tool, `task_create`) now works on Windows under Git Bash. `subprocess_backend.spawn` builds a direct-exec `argv` list and passes it through new `argv=` and `env=` kwargs on `BackgroundTaskManager.create_agent_task` / `create_shell_task`; `_start_process` then runs the executable via `asyncio.create_subprocess_exec(*argv)` with no shell in between. Previously the spawn command was a single string interpreted by `bash -lc`, which on Windows could not reliably exec a Windows-pathed Python interpreter (e.g. `C:\Users\...\python.exe`) — Git Bash's escape parser consumed the backslashes from the embedded env-prefix and, even with proper quoting, bash launched via `asyncio.create_subprocess_exec` returned `command not found` for Windows-pathed binaries that worked perfectly when invoked interactively. Bypassing the shell sidesteps the entire class of cross-platform quoting and path-translation hazard. The legacy shell-evaluated `command=` path is preserved for callers (e.g. `BashTool`) that legitimately want shell semantics. See issue #230.
- Bundled skill loader now uses `yaml.safe_load` for SKILL.md frontmatter, matching the user-skill loader. The shared parser is extracted to `openharness.skills._frontmatter` so bundled and user skills handle YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs the same way.
- Compaction now detects llama.cpp/OpenAI-compatible context overflow errors, accounts for image blocks in auto-compact token estimates, and strips image payloads from summarizer-only compaction requests.
- Large tool results are now bounded in conversation history: oversized outputs are saved under `tool_artifacts`, old MCP results become microcompactable, and context collapse trims stale tool-result payloads.
- ohmo now keeps personal memory isolated from OpenHarness project memory: `/memory` in ohmo sessions targets the ohmo workspace memory store, and ohmo runtime prompt refreshes no longer inject project memory unless explicitly requested.
- Fixed `glob` and `grep` tools hanging indefinitely when the `rg` subprocess produced enough stderr output to fill the OS pipe buffer. `stderr` is now redirected to `DEVNULL` so it is discarded rather than blocking the child process.
- Fixed `bash_tool` hanging after a timed-out command when the subprocess stdout stream stayed open. `_read_remaining_output` now applies a 2-second `asyncio.wait_for` timeout so the tool always returns promptly.
- Fixed `session_runner` background task deadlock caused by an unread `stderr=PIPE` stream. The subprocess now uses `stderr=STDOUT` so all output merges into the single readable stdout pipe.
- React TUI prompt input now treats the raw DEL byte (`0x7f`) as backward delete while preserving true forward-delete escape sequences, fixing backspace failures seen in some macOS terminal environments.
- `todo_write` tool now updates an existing unchecked item in-place when `checked=True` instead of appending a duplicate `[x]` line.

- Built-in `Explore` and `claude-code-guide` agents no longer hard-code `model="haiku"`, which caused them to fail for users on non-Anthropic providers (OpenAI, Bedrock, custom base URLs, etc.). Both agents now use `model="inherit"` so they run with whatever model the parent session is using. `build_inherited_cli_flags` is also fixed to skip the `--model` flag entirely when the value is `"inherit"`, letting the subprocess correctly inherit the parent model via the `OPENHARNESS_MODEL` environment variable instead of receiving the literal string `"inherit"` as a model name.

- React TUI spinner now stays visible throughout the entire agent turn: `assistant_complete` no longer resets `busy` state prematurely, and `tool_started` explicitly sets `busy=true` so the status bar remains active even when tool calls follow an assistant message. `line_complete` is the sole signal that ends the turn and clears the spinner.
- Skill loader now uses `yaml.safe_load` to parse SKILL.md frontmatter, correctly handling YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs instead of naive line-by-line splitting.
- `BackendHostConfig` was missing the `cwd` field, causing `AttributeError: 'BackendHostConfig' object has no attribute 'cwd'` on startup when `oh` was run after the runtime refactor that added `cwd` support to `build_runtime`.
- Shell-escape `$ARGUMENTS` substitution in command hooks to prevent shell injection from payload values containing metacharacters like `$(...)` or backticks.
- Swarm `_READ_ONLY_TOOLS` now uses actual registered tool names (snake_case) instead of PascalCase, fixing read-only auto-approval in `handle_permission_request`.
- Memory scanner now parses YAML frontmatter (`name`, `description`, `type`) instead of returning raw `---` as description.
- Memory search matches against body content in addition to metadata, with metadata weighted higher for relevance.
- Memory search tokenizer handles Han characters for multilingual queries.
- Fixed duplicate response in React TUI caused by double Enter key submission in the input handler.
- Fixed concurrent permission modals overwriting each other in TUI default mode when the LLM returns multiple tool calls in one response; `_ask_permission` now serialises callers via an `asyncio.Lock` so each modal is shown and resolved before the next one is emitted.
- Fixed React TUI Markdown tables to size columns from rendered cell text so inline formatting like code spans and bold text no longer breaks alignment.
- Fixed grep tool crashing with `ValueError` / `LimitOverrunError` when ripgrep outputs a line longer than 64 KB (e.g. minified assets or lock files). The asyncio subprocess stream limit is now 8 MB and oversized lines are skipped rather than terminating the session.
- Fixed React TUI exit leaving the shell prompt concatenated with the last TUI line. The terminal cleanup handler now writes a trailing newline (`\n`) alongside the cursor-show escape sequence so the shell prompt always starts on a fresh line.
- Reduced React TUI redraw pressure when `output_style=codex` by avoiding token-level assistant buffer flushes during streaming.

### Changed

- ohmo Feishu group routing now supports managed group creation, gateway-scoped provider/model commands, and stricter group mention handling so group conversations only wake ohmo when explicitly addressed.
- Dry-run output now reports a `ready` / `warning` / `blocked` readiness verdict, concrete `next_actions`, likely matching skills/tools for normal prompts, and richer slash-command previews for read-only vs stateful command paths.
- React TUI now groups consecutive `tool` + `tool_result` transcript rows into a single compound row: success shows the result line count inline (e.g. `→ 24L`), errors show a red icon and up to 5 lines of error detail beneath the tool row. Standalone successful tool results are suppressed to reduce transcript noise; standalone errors are still surfaced.
- README now links to contribution docs, changelog, showcase material, and provider compatibility guidance.
- README quick start now includes a one-command demo and clearer provider compatibility notes.
- README provider compatibility section updated to include OpenAI-format providers.

## [0.1.7] - 2026-04-18

### Fixed

- Install script now links `oh`, `ohmo`, and `openharness` into `~/.local/bin` instead of prepending the virtualenv `bin` directory to `PATH`, which avoids overriding Conda-managed shells while preserving global command discovery.
- React TUI prompt now supports `Shift+Enter` for inserting a newline without submitting the current prompt.
- React TUI busy-state animation is less error-prone on Windows terminals: the extra pseudo-animation line was removed, Windows now uses conservative ASCII spinner frames, and the spinner interval was slightly slowed to reduce flashing.

## [0.1.0] - 2026-04-01

### Added

- Initial public release of OpenHarness.
- Core agent loop, tool registry, permission system, hooks, skills, plugins, MCP support, and terminal UI.
