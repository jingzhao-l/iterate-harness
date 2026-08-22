# Changelog

All notable changes to iterate-harness should be recorded in this file.

## [1.13.0] - 2026-08-22

Minor release to keep the iterate-harness distribution in step with the
released plugin line (iterate-plugin 2.11.0). Aligns the npm wrapper and the
Python package to the latest reviewed/hardened source state after the full
project review; no breaking API changes.

### Changed

- Version bumped to 1.13.0 across `src/iterate_harness/__init__.py` and
  `npm/package.json` (single source of truth retained in `__version__`).

## [1.12.12] - 2026-08-21

WebUI operation-console controls, sandbox hardening for file tools, and a
batch of UX fixes from the full project review.

### Added

- **Dashboard run controls** (`frontend/web/src/pages/Dashboard.tsx`): the
  persistent run-status banner on the dashboard now exposes direct
  pause / resume / stop buttons backed by `POST /chat/control`, so the
  operation console no longer depends on the chat panel. Stop requires a
  secondary confirmation and is disabled while the loop is waiting for human
  input.

### Security

- **Sandbox path validation for `todo_write` and `notebook_edit`**
  (`tools/todo_write_tool.py`, `tools/notebook_edit_tool.py`): both tools now
  resolve candidate paths and validate them against the Docker sandbox
  boundary the same way `file_write` already did, blocking directory-traversal
  escape attempts. Covered by new rejection tests in `tests/test_core_tools.py`.
- **No more hardcoded API keys in tests**: four real-API test files read
  `ANTHROPIC_API_KEY` from the environment (empty default) and skip their
  real-API cases when the key or the target workspace is absent.

### Fixed

- **Runs timeline diff expansion** (`frontend/web/src/pages/Runs.tsx`): the
  expandable diff block now normalizes both string and array persisted diff
  shapes, so it renders instead of being silently hidden.
- **StartDialog resume mode** (`frontend/web/src/components/StartDialog.tsx`):
  the "changed files / git ref" options are hidden in resume mode and a stale
  `changed` flag is cleared, preventing a bad kickoff.
- **Headless run guidance** (`cli.py`): after a `--headless` run finishes, the
  CLI prints next-step hints for viewing the HTML report or decision log.

## [1.12.11] - 2026-08-21

Security hardening, MCP 2.0.0 migration, and reproducible dependency pinning.

### Security

- **Credential encryption at rest** (`auth/storage.py`): when no system keyring
  is available (containers, CI, WSL), credentials in `credentials.json` are now
  encrypted with Fernet (authenticated symmetric encryption). The key is derived
  deterministically from the machine secret + user home + app salt, so the file
  is only decryptable on the same machine under the same user. Legacy plaintext
  values from older versions are migrated in place automatically.
- **WebUI access-token protection** (`web/token.py`, new): `ih web serve` issues
  a random token (persisted under the config dir with mode 600), prints it on
  startup, and injects it into the browser URL. All API calls must carry the
  token (`Authorization` header or `?token=` query), preventing local
  cross-process access to the loopback console.

### Changed

- **MCP 2.0.0 migration** (`mcp/client.py` + fixtures/tests): adapted to
  breaking changes — `FastMCP` → `MCPServer`, `inputSchema` → `input_schema`,
  `structuredContent` → `structured_content`, and the streamable HTTP client's
  new 2-tuple return. The HTTP transport now uses the dedicated `httpx2` client
  that MCP 2.0.0 requires.
- **Dependencies pinned to exact versions** (`pyproject.toml`): all runtime and
  dev dependencies are now exact-pinned (resolved from `uv.lock`) for
  reproducible installs. Added `httpx2==2.10.0`; upgraded `fastapi` to 0.141.1
  to resolve the `starlette` version conflict with MCP 2.0.0.
- **No more silent `except: pass`**: cleanup/best-effort error paths across
  checkpoint, report server, docker backend, cron scheduler, session storage,
  swarm, and task manager now log the reason at debug level instead of
  swallowing exceptions.

### Added

- **Test suites** for `state/`, `keybindings/`, `themes/`, WebUI token auth,
  and the auth manager (closing the coverage gaps identified in the full
  project review).

### Fixed

- **Unused imports** (`commands/registry.py`, two test files) removed so the
  pinned `ruff` rule set is fully green.

## [1.12.10] - 2026-08-20

Install source switched to the official **PyPI** index, so npm installs work
without GitHub in network-restricted (or TLS-broken) environments — exactly
the "no GitHub dependency" experience you get from DNS-fixed proxies, but
built in.

### Changed

- **Install channel** (`npm/lib/bootstrap.js`): the wrapper now installs the
  PyPI spec `iterate-harness==<version>` first, which pip resolves against the
  user's configured index/mirror (e.g. `pypi.tuna.tsinghua.edu.cn`). The
  GitHub pre-built wheel is demoted to a fallback (kept for users without a
  PyPI mirror), and the source archive is the final last-resort. Replaced the
  `installUrl`/`installFallbackUrl`/`fallback` API with an ordered
  `installCandidates` chain; HTTP candidates still get the node/curl
  local-download resilience, non-URL (PyPI) candidates are pip-installed
  directly.
- **Release distribution** (`.github/workflows/release.yml`): the released
  wheel is now also published to PyPI via twine (best-effort, gated on the
  `PYPI_PASSWORD` secret).
- **Tests** (`npm/test/bootstrap.test.js`): added `pypiInstallSpec`/
  `installCandidates` coverage and rewrote the `installHarness` suite for the
  new candidate-chain API (PyPI → wheel → archive), plus an end-to-end walk
  test asserting the exact pip/download call order.

## [1.12.9] - 2026-08-20

Interactive installer experience, mirroring iterate-skill-installer's banner + wizard.

### Added

- **Terminal UI helpers** (`npm/lib/ui.js`, new): `ITERATE` ASCII banner (cyan), colored `step/success/warning/error/info/hint` prefixes, a `frameSection` summary box, and an `askYesNo` interactive prompt — all written to **stderr**, so piped stdout stays clean.
- **Banner on every run** (`npm/lib/bootstrap.js`): `runHarness` prints the banner each time `ih` runs (skipped automatically when stderr is not a TTY, e.g. `ih --version | jq`).
- **Interactive install wizard** (`npm/lib/bootstrap.js`): on the first run, after `EnsureRuntime` detects the harness isn't installed, it asks `Install iterate-harness vX.Y.Z ...? [Y/n]` before downloading, then prints an "Installing" box with the version/runtime, and a "Done" box with getting-started tips. Declining the prompt exits cleanly (code 0) with a `CancelledError` message instead of erroring.
- **UI tests** (`npm/test/ui.test.js`, new): covers the banner shape, `stripAnsi`, `frameSection` alignment, and `CancelledError`.

## [1.12.8] - 2026-08-20

`ih` is now installed during `npm install` itself, not lazily on the first run.

### Added

- **Install-during-`npm install`** (`npm/scripts/postinstall.js`, new): an npm `postinstall` hook now creates the managed venv and pip-installs the harness right after `npm install`/`npm install -g` finishes — `ih` is ready immediately instead of waiting for the first run. It reuses the exact same `ensureRuntime` logic as the CLI entry point, never fails the npm install (it degrades to a runtime install on the first `ih` run when Python/network is unavailable or `ITERATE_HARNESS_SKIP_INSTALL=1`).
- **postinstall tests** (`npm/test/postinstall.test.js`, new): covers the `SKIP_INSTALL` fast path, a successful bootstrap delegation, and the bootstrap module smoke load.

## [1.12.7] - 2026-08-20

npm first-run install hardened for broken-Python-TLS environments: when pip can't fetch the release wheel (e.g. macOS python.org build with a broken certificate chain) and the wrapper downloads the wheel itself via Node/curl, the locally cached wheel now keeps a valid PEP 427 filename (`iterate_harness-<version>-py3-none-any.whl`) so pip accepts it instead of rejecting a bare `iterate-harness-<version>.whl`.

### Fixed

- **npm wrapper wheel cache** (`npm/lib/bootstrap.js`): `downloadCachePath` now returns the real wheel asset name for the `.whl` extension instead of a generic `iterate-harness-<version>.whl`. Previously, in the fallback path (Node/curl download), the cached wheel could not be pip-installed because pip rejects any `*.whl` lacking the `{python}-{abi}-{platform}` tags with "not a valid wheel filename".
- **Regression test** (`npm/test/bootstrap.test.js`): adds coverage asserting the wheel cache path is a valid PEP 427 wheel filename and round-trips through `artifactExtensionFor(wheelAssetUrl(...))`.

## [1.12.6] - 2026-08-20

npm first-run install fixed: the wrapper no longer tries to build the harness from the GitHub source archive (which lacks `frontend/web/dist`, so pip's source build always failed). It now downloads the **pre-built wheel** uploaded to the GitHub release and pip-installs that — mirroring how `iterate-skill-installer` ships pre-wrapped assets — with the source archive demoted to a last-resort fallback.

### Changed

- **npm wrapper install source** (`npm/lib/bootstrap.js`): `installUrl` defaults to the pre-built release wheel `iterate_harness-<version>-py3-none-any.whl` instead of the source archive. New `wheelAssetUrl` / `wheelAssetName` helpers build the pinned asset URL; new `installHarness` fallback path `pip from wheel URL → Node/curl download + local pip → (missing wheel) source archive`. New `installFallbackUrl` returns the archive URL only when the user did not override `ITERATE_HARNESS_INSTALL_URL`. Cache files keep the correct `.whl` / `.tar.gz` extension (`artifactExtensionFor`, `downloadCachePath` ext param).
- **Release distribution** (`.github/workflows/release.yml`, new): on a published harness release, installs frontend deps, builds `frontend/web/dist`, runs `uv build --wheel`, and uploads the wheel to the release. Ordering note: `npm publish` must wait for this job so the wrapper can fetch the wheel.

### Docs

- `npm/README.md` / `npm/README.zh-CN.md`: first-run step 3 and the `ITERATE_HARNESS_INSTALL_URL` table row now describe the wheel-first install with the source archive as fallback.

## [1.12.5] - 2026-08-20

Provider refresh + self-update + WebUI UX review: replaced ModelScope with OpenAI as a first-class provider, added an automatic update mechanism with an `ih update` command, and fixed a set of WebUI UX issues found in a full frontend pass.

### Added

- **OpenAI provider** (`api/registry.py`, `config/settings.py`): ModelScope removed from the provider registry and built-in profiles; OpenAI added as a standard provider with `OPENAI_API_KEY` env source, official base URL `https://api.openai.com/v1`, `gpt-5.4` default model, and the `gpt-5.4` / `gpt-5` / `gpt-4.1` / `o4-mini` model family for the `/model` picker; `ih auth login openai` and `ih setup` now offer OpenAI as a first-class choice.
- **Self-update** (`update.py`, `cli.py`): new `ih update` command with `--check` (report without applying), `-y/--yes` (skip confirmation) and `--force` (reinstall even when current) options. Detects the install layout (npm-managed venv / source checkout / plain pip), discovers the latest release from the GitHub feed (with a raw-file fallback), applies the matching update, and verifies the live version afterwards. Version discovery falls back to the system `curl` when Python's CA store is broken or stale (`SSL: CERTIFICATE_VERIFY_FAILED`), matching the installer's strategy for machines behind corporate proxies / self-signed roots.
- **Version-change notification**: `ih --version` now prints a one-line advisory hint when a newer release exists (cached for 24h, 3s timeout, never fatal; opt out with `ITERATE_HARNESS_UPDATE_CHECK=0`).

### Fixed

- **WebUI Runs page** (`frontend/web/src/pages/Runs.tsx`): changing the timeline type filter no longer keeps the stale pagination offset — the page resets to the newest page so a filtered result can never land on an empty later page.
- **WebUI report preview sandbox** (`frontend/web/src/pages/Reports.tsx`): the preview iframe now runs with `sandbox="allow-scripts"` only; the previous `allow-same-origin allow-scripts` combination would have silently voided the sandbox and given report-derived HTML same-origin access to the WebUI API.
- **WebUI dashboard layout** (`frontend/web/src/pages/Dashboard.tsx`, `styles.css`): the recent-reports / recent-audit pair moved off an inline fixed two-column grid onto a `.panels-grid` class that stacks to a single column on narrow viewports.

## [1.12.4] - 2026-08-20

User-facing copy review sweep: verified CLI/TUI help, READMEs, report output, bundled skills, frontend and install scripts are iterate-only, and removed the last autopilot vestige left by the 1.12.2 subsystem removal.

### Removed

- **Vestigial "Active Repo Context" injection** (`config/paths.py`, `prompts/context.py`, `tests/test_prompts/test_claudemd.py`): dropped `get_project_autopilot_dir` / `get_project_active_repo_context_path` and the runtime system-prompt entry for `.iterate-harness/autopilot/active_repo_context.md` — nothing in the codebase ever writes that file, so the read-side injection was dead code left behind when the autopilot subsystem was removed.

### Verified clean (no change needed)

- `ih --help` / `ih iterate --help` and the TUI slash-command registry: no stale commands or legacy wording.
- README / README.zh-CN, report text/CSV output, bundled skills, React TUI + web frontends, install scripts: no `ohmo` / `openclaw` / `openharness` / Slack / Feishu / Lark / webhook remnants.

## [1.12.3] - 2026-08-20

Final legacy sweep: removed the remaining external-communication surface and confirmed dead code so `ih` is purely the iterate review/fix harness with no channel/notification leftover.

### Removed

- **Webhook notifications** (`iterate/webhook.py`, `tests/test_iterate/test_webhook.py`, `cli.py`): dropped the Slack / Lark / Feishu / generic webhook notifier and the `ih iterate report --webhook <url>` option; exports (`WebhookResult`, `detect_webhook_type`, `notify_report`, `send_webhook`) removed from `iterate/__init__.py`.
- **Compatibility helpers** (`utils/helpers.py`, `tests/test_utils/test_helpers.py`): removed the unused `get_data_path` / `safe_filename` / `split_message` shims kept only for legacy channel adapters.
- **Empty OAuth service dir** (`services/oauth/`): removed the leftover empty package from the deprecated subscription/OAuth flow.
- **Dead channel config models** (`config/schema.py`): removed the unused `Config` / `ChannelConfigs` / `SlackConfig` / `FeishuConfig` / etc. compatibility models — no production or test code imports `iterate_harness.config.schema`.

## [1.12.2] - 2026-08-19

Iterate-only cleanup: stripped the remaining OpenHarness branding and the general-purpose agent-harness surface area from the CLI/TUI, auth, commands, workflows, and docs so `ih` presents purely as the iterate review/fix harness.

### Removed

- **Non-iterate CLI commands** (`cli.py`): dropped `cron`, `autopilot`, `hooks`, `mcp`, `plugin`, `reload-plugins`, `ship` from `ih --help`; dry-run classification table updated.
- **TUI slash commands** (`commands/registry.py`): removed `/hooks`, `/mcp`, `/plugin`, `/reload-plugins`, `/autopilot`, `/ship`; cleaned "ohmo" wording from the remaining `/stop` help.
- **Autopilot subsystem**: the whole repo-autopilot stack is gone — `src/iterate_harness/autopilot/`, the project autopilot path helpers, the `autopilot-run-next.yml` / `autopilot-scan.yml` / `autopilot-pages.yml` workflows, `autopilot-dashboard/`, `docs/autopilot/`, and related tests.
- **Redundant `local` provider profile** (`config/settings.py`, `cli.py`, `auth/manager.py`): removed the duplicate OpenAI-compatible "local" profile; Ollama now covers local endpoints, and `ih auth login` / `ih setup` no longer present a dead-end local provider.
- **Legacy release notes** (`RELEASE_NOTES_v0.1.8.md`, `RELEASE_NOTES_v0.1.9.md`): stale OpenHarness-era files removed.
- **Bug-report template `oh` reference** (`.github/ISSUE_TEMPLATE/bug_report.yml`): repro placeholder now uses `ih`.

### Changed

- **Branding** (`scripts/install.sh`, `frontend/terminal/src/components/WelcomeBanner.tsx`, `config/schema.py`): "Oh my Harness" / `ohmo` / `openclaw` references removed; `bot_names` is now `["iterate_harness"]`; the install banner is iterate-branded.
- **Auth guidance** (`cli.py`): `ih auth login` for local providers points to `ih setup` instead of dead-ending; setup hints include Ollama.
- **Skill docs** (`skills/bundled/content/skill-creator.md`): private skills path changed from `~/.ohmo/skills` to `~/.iterate-harness/skills`.

## [1.12.1] - 2026-08-19

Force reviewers to open every file in their assigned review scope and surface a coverage gap hint.

### Added

- **Scope inventory + coverage** (`iterate/review_scope.py`): `collect_scope_files` gathers the sorted relative-path inventory for a `full` scope, `chunk_files` batches it by `reviewer.scope_chunk_size` (default 25) while keeping directory runs together, and `compute_coverage` scores self-reported reads against the assigned inventory.
- **Scope batching in plan** (`iterate/review.py`): `build_review_plan` splits a `full` scope into per-chunk reviewer tasks when `scope_files` is supplied; `changed-only` stays a single batch owning the full delta.
- **Coverage hint in meta-review** (`iterate/meta_review.py`): new prompt-informative `COVERAGE_GAP` (medium severity) when reviewers' self-reported `readFiles` don't cover their assigned scope; never flips the verdict.
- **Config switches** (`iterate/types.py`, `iterate/config_loader.py`): `reviewer.coverage_validation` (default `true`) and `reviewer.scope_chunk_size` (default `25`).

### Changed

- **Reviewer prompt** (`iterate/review.py`): injected mandatory `COVERAGE RULE` — reviewers must `read_file` EVERY file in the assigned inventory before judging and return a `readFiles` array listing every file actually opened.
- **Evidence binary detection** (`iterate/evidence.py`): files containing a NUL byte are treated as not line-addressable (binary payload), same as an out-of-range line.
- **Violation attribution** (`iterate/meta_review.py`): `EVIDENCE_VIOLATION` now reports the round that first surfaced the poisoned finding.
- **Non-contiguous round convergence** (`iterate/review.py`): `aggregate_rounds` sizes `findings_by_round` by the max round number (not `len(rounds)`), and `compute_convergence` reads the last present round by its reported round number.
- **Runtime wiring** (`tools/iterate_tools.py`): the plan tool collects scope files and batches them; the meta-review tool computes coverage and threads it through the final report; `IterateConfigTool` exposes `coverageValidation`/`scopeChunkSize`.

## [1.12.0] - 2026-08-19

Forced evidence-based review: sub-agents must actually read files before judging, and fabricated locations fail the run.

### Added

- **Hard code-evidence gate** (`iterate/evidence.py`): new module validating every review finding's `file`/`line` against real files on disk (file existence, line bounds, whole-file/`0` lines, optional read-set cross-check, traversal-path rejection).
- **Evidence folding into meta-review** (`iterate/meta_review.py`): `build_final_review_report` now folds evidence results in — any fabricated path or out-of-range line becomes a critical `EVIDENCE_VIOLATION` and flips the verdict to `revise`.
- **Config switch** (`iterate/types.py`, `iterate/config_loader.py`): new `reviewer.evidence_validation` (default `true`); disabled via `false`.

### Changed

- **Reviewer prompt** (`iterate/review.py`): injected mandatory `EVIDENCE RULE` — reviewers must `read_file` every file they report on before judging, never report locations they did not read, and treat fabricated line numbers as poisoned evidence. `line` is now required for anchored, line-targeted findings (0 = whole-file/module-level).
- **Runtime wiring** (`tools/iterate_tools.py`): the meta-review path now runs the evidence audit against the real repo and threads the result through the final report.

### Security

- Evidence gate prevents hallucinated file paths / invented line numbers from entering the report as credible issues.

### Tests

- Added evidence module tests (existence, line bounds, whole-file, read-set, traversal) and meta-review evidence folding tests; full suite passes.

## [1.11.3] - 2026-08-18

Full implementation audit (code + UX) closing all remaining gaps with tests; worktree metadata no longer pollutes git state.

### Fixed

- **Worktree metadata polluted the worktree's git state** (`swarm/worktree.py`): the ownership JSON written inside each worktree made directories appear modified to git, so a subsequent `git add -A` in the fix flow could stage the metadata or fail an "empty diff" check. Metadata is now persisted as a sidecar `<ns>/<flat_slug>.meta.json` *outside* the worktree, keeping the worktree's git state clean while preserving ownership/agent/slug lookups (with regression tests).
- **Streaming STT placeholder** (`voice/stream_stt.py`): replaced a silent placeholder that parsed real audio into non-speech frames with an explicit `NotImplementedError` so unsupported engines fail loudly instead of degrading silently.
- **Settings file permissions** (`config/settings.py`): the persisted settings file is now written `0o600` (owner-only) instead of default permissive mode, protecting any locally cached secrets.
- **Swarm lifecycle hardening** (`swarm/registry.py`, `swarm/team_lifecycle.py`): ownership/teardown flows now surface and log errors instead of swallowing them (empty exception blocks removed), keeping resource cleanup reliable.
- **Worktree spool hardening** (`swarm/spawn_utils.py`, `swarm/in_process.py`): input validation and error propagation tightened for agent spawn/spool execution.
- **Report checksum verification** (`iterate/checkpoint.py`, `iterate/review.py`): checkpoint/review artifact handling hardens against corrupt or missing sidecar data.
- **API secret exposure** (`web/routes/config.py`, `api/*`): API-key style fields are masked (not truncated) in config responses so they cannot be partially leaked to the UI.
- **Bridge secret encryption** (`bridge/work_secret.py`, `bridge/manager.py`): secret-at-rest encryption is enforced and decryption failures are logged explicitly.
- **WebUI page states** (frontend `web/src/pages/*`): Runs/Dashboard/Checkpoints/Reports/Workspaces now render distinct loading/empty/error states instead of silently blank — including a visible failure state when run `find`-results fail to load (previously an unhandled promise could leave the list blank).
- **Type coverage** (frontend `web/src/types.ts`): completed type definitions so the frontend builds cleanly under strict checks.

### Added

- **Voice test suite** (`tests/test_voice/`): unit tests for `stream_stt` and `voice_mode`.

## [1.11.2] - 2026-08-18

WebUI reliability fixes from a full implementation audit.

### Fixed

- **Chat history lost after SSE disconnect/reconnect** (`store.ts`): the hub only serves live subscribers, so chat messages, progress events and decision-log entries published while the stream was down were permanently lost on reconnect. `onopen` now resyncs chat history + run status from REST and bumps the decision-log revision on (re)connect — the UI never shows a stale transcript.
- **Assistant output dropped on early run termination** (`web/run_manager.py`): buffer text was only flushed on an `AssistantTurnComplete`, so a turn interrupted by an error, an early stop or a system-exit silently discarded the model's partial output. A `_flush_assistant_buffer` in the run loop's `finally` now publishes any buffered text (no-op when the last turn was already flushed), plus a regression test.
- **Report mtime conversion wrapped in an empty exception** (`web/routes/reports.py`): conversion failures were swallowed with `except Exception: pass`, hiding the error and leaving `modified` silently null. Replaced with an explicit `_to_modified_iso` helper that logs a warning and degrades to `None` only for genuinely un-representable mtimes, with tests.

## [1.11.1] - 2026-08-17

WebUI review fix: truthful tool-failure markers in the human-in-the-loop chat timeline.

### Fixed

- **Tool failure shown as success** (`web/run_manager.py`): `ToolExecutionCompleted` carries an `is_error` flag but the chat stream ignored it, so a failing tool call still rendered a green `✔` checkmark in the live tool timeline — misleading during model laziness / fault diagnosis. The manager now publishes `✖ {tool}：{preview}` for failing calls, keeping the tool timeline truthful.
- **Frontend tool-card failure state** (`components/ChatPanel.tsx`, `styles.css`): the tool-line parser now distinguishes `✔/✖` into done / failed states; failed cards render with a red accent and a "失败" label (the nested status ternary was also refactored into a lookup table).
- **Version sync**: `npm/package.json` was left at `1.10.1` after the 1.11.0 release — bumped to `1.11.1` to restore the release-required lock-step (`npm == harness == tag`).

## [1.11.0] - 2026-08-17

WebUI iteration wave: workspaces management, findings triage, tool-call visualization, and UX robustness for the management console (design §17.3 P2/P4, §18). Focus stays on the *run* — the new surfaces complement the dashboard/timeline/checkpoints and the human-in-the-loop chat panel.

### Added

- **Findings triage journal** (`web/findings_triage.py`, `web/routes/runs.py` `GET/POST/DELETE /runs/findings/triage`): persistent approve/reject decisions in `.iterate/findings-triage.jsonl` (append-only, latest decision wins), dedup keyed on `(file, line, dimension)`, all mutations require `confirm=true` and are audit-logged. The Runs page (`pages/Runs.tsx`) adds approve / reject buttons per finding, a triaged-state filter, and a "clear all decisions" action with a secondary confirmation dialog.
- **Workspaces management** (`web/routes/workspaces.py`, `pages/Workspaces.tsx`): `/workspaces` lists the primary checkout plus every isolate worktree whose `original_path` matches the project (git metadata, config isolation flag, entry count); `/workspaces/remove` removes a stale worktree — slug validated against path traversal, `confirm=true` required, audited. New `/workspaces` route wired into `App.tsx`.
- **Tool-call visualization** (`components/ChatPanel.tsx`): tool-execution messages render as status cards (executing / done / idle) with tool name and detail, so the chat panel makes the model's tool activity legible at a glance.
- **UX robustness layer** (`components/ErrorBoundary.tsx`, `components/Skeleton.tsx`): page-level error boundary with retry / reload, skeleton loaders for tables and cards.
- **Keyboard shortcuts + store hardening** (`App.tsx`, `store.ts`): `g <key>` page jumps, `/` or `Cmd/Ctrl+K` toggles the chat panel; SSE disconnect triggers polling fallback, connection-state toasts, and browser notifications when the run waits on a human decision.

### Fixed

- **Frontend type-check regressions** (`src/api.ts`, `pages/Runs.tsx`): removed a duplicate `request` export and a `triage` identifier collision between the triage state map and the callback — `npm run typecheck` is clean again.
- **Workspaces remove button unreachable** (`web/routes/workspaces.py`, `pages/Workspaces.tsx`): the list filtered to project-owned worktrees while the frontend required `!active` to show the delete action, making deletion dead UI. Worktrees are now flagged `detail.stale` (round older than the project's latest round) and only stale ones expose the remove action, so the active round's sandbox is never deleted mid-run.
- **Triage key mismatch for findings without a line number** (`web/findings_triage.py`): the backend dedup key rendered a missing line as the literal `"None"` while the frontend rendered it as `""`, so persisted approve/reject decisions never matched their row after a reload and re-triaging duplicated records. `_key` now canonicalizes `None` → `""` to match the frontend exactly.
- **Non-atomic triage journal rewrite** (`web/findings_triage.py`): `_rewrite` now writes a temp file and atomically replaces the journal so an interrupted clear-all never truncates the journal.
- **Version sync**: `frontend/web/package.json` bumped to `1.11.0` to match the Python harness (was stuck at `1.10.0`).

## [1.10.1] - 2026-08-17

Code-quality / release-hygiene pass from a full implementation audit:

### Fixed
- **Single source of truth for the version**: `src/iterate_harness/__init__.py` now
  exposes `__version__` and `pyproject.toml` reads it at build time
  (`[tool.hatch.version].path`). The CLI (`cli.py`) and the WebUI FastAPI app
  (`web/api.py`) report `__version__` instead of duplicating the literal, so a
  version bump can never drift across files.
- **npm wrapper version drift**: `npm/package.json` was stuck at `1.9.4` while the
  Python harness moved to `1.9.5`/`1.10.x`. Bumped to `1.10.1` to restore the
  release-required lock-step (`npm == harness == tag`).
- **Observability of swallowed errors**: `coordinator/agent_definitions.py` now logs
  malformed agent-file parse failures at WARNING (was DEBUG) so config mistakes
  surface; `iterate/onboard_cmd.py` logs the auth pre-check fallback at DEBUG
  instead of an opaque `pass`.
- **Comment fix** in `utils/shell.py` (clarified the non-Docker code path label).

## [1.10.0] - 2026-08-17

Ships the full WebUI management console (design §17) with the iterate-specific conversational control panel (design §18). The WebUI is now the primary operating surface: users start, monitor, pause and resume iterate loops from the browser, and confirm decisions / nudge a stalled loop through a side chat panel. Per design §18, the harness center stays the *run*, not the conversation — the chat panel is a human-in-the-loop instrument for decision confirmation, status updates, and urging the model when it stalls mid-round.

### Added

- **WebUI management console** (`web/api.py`, `web/routes/{status,runs,checkpoints,config,reports}.py`, `frontend/web`): FastAPI backend + React frontend with Dashboard / Runs timeline / Checkpoints / Budget & rate / Config / Reports pages, dark mode, SSE real-time push, timeline pagination, and a "启动迭代" start entry point.
- **In-process event hub** (`web/hub.py`): pub/sub fan-out (`ChatHub`) bridging the iterate run loop to every SSE connection; full queues drop the oldest event so the run loop never blocks on a slow client.
- **Live run manager** (`web/run_manager.py`): owns the single in-server iterate loop, substitutes the engine's three human-interaction channels (`permission_prompt` / `ask_user_prompt` / `ask_user_select`) with Web versions that pause the run and await the answer, and persists a human-interaction-only transcript to `.iterate/web-chat.jsonl`.
- **Human-in-the-loop nudge injection** (`iterate/loop_policy.py` `IterateLoopPolicy.inject_nudge`): user 督促 messages are queued and prepended to the next-round instruction at the round boundary, so a stalled model is urged to continue even mid-loop.
- **Conversational control panel** (`web/routes/chat.py`, `frontend/web/src/components/{ChatPanel,RunStatusCard,StartDialog}.tsx`): chat routes (`/chat/start`, `/chat/status`, `/chat/message`, `/chat/control`, `/chat/history`) plus a fixed overlay panel that auto-opens when a decision is pending and supports permission approval, select/prompt answers, pause/resume/stop, and free-text nudges.
- **Security posture** (`web/security.py`): loopback-only binding by default, strict CORS, path whitelisting against traversal, API-key redaction, and append-only audit log for the WebUI surface.

## [1.9.4] - 2026-08-17

Implements the remaining 28-item review backlog left open by the 1.9.3 feature wave (design §15.4): triage results now persist into `iterate.config.yaml`, prompt template presets, multilingual + CSV reports, offline model providers, run-to-run diffing, branch-targeted review, and webhook push notifications.

### Added

- **Triage → config bridge** (`iterate/personalization.py` `sync_known_intentional_to_config`, wired in `tools/iterate_tools.py` `_persist_ignores`): entries marked "always ignore" during `iterate_triage` now merge into `personalization.known_intentional` of the project `iterate.config.yaml` (deduped, preserving hand-written entries, atomic write, `managed-by-iterate-triage` marker) so the whole project — not just the harness's private JSON — sees the filters from the next round.
- **Prompt template presets** (`iterate/prompts.py`): `TEMPLATE_PRESETS` registry with `standard` (default), `strict` (conservative, safety-first) and `quick` (impact-only) presets; `template_suffix()` normalizes mode (`dry-run` → `dry_run`); CLI `iterate review|run --template <preset>`.
- **Multilingual reports** (`iterate/ci_report.py`): `L10N_TEXTS` (en/zh) with header / gate / no-findings strings; `render_text(..., language=)`; CLI `--lang en|zh` (default `en`).
- **CSV export** (`iterate/ci_report.py` `render_csv`): findings written as UTF-8-BOM CSV (severity/dimension/file/line/summary/failure_scenario/suggested_fix) for direct Excel opening; CLI `iterate report --csv <path>` (`-` → `.iterate/report.csv`).
- **Offline model providers** (`config/settings.py`): `local` (openai-compatible localhost, e.g. llama.cpp / LM Studio / vLLM) and `ollama` provider profiles with `auth_source: local`; unblock BYOK-style workflows against local endpoints.
- **Run-to-run diff** (`iterate/trend_store.py`): `diff_runs()` + `RunDiff` classify findings as new / fixed / regressed / unchanged by fingerprint (regressions identified via `previously_fixed_findings`); `render_diff()` human-readable summary.
- **Branch-targeted review** (`cli.py` `iterate review|run --branch`): review/loop runs against a target branch via worktree isolation; `commands/iterate.py` `/iterate dimensions` shows configured dimensions and their resource allocations (model / concurrency / token_budget).
- **Webhook notifications** (`iterate/webhook.py`): universal notifier auto-detecting Slack Incoming Webhook, Lark/Feishu custom bot and generic JSON endpoints; rich Slack Blocks / Feishu interactive card payloads with severity emoji; CLI `iterate report --webhook <url>`.
- **Common-failure self-healing guide** (`README.md` "Troubleshooting / 常见失败自愈指南"): TLS / auth / rate-limit / checkpoint / provider-not-found scenarios with concrete fixes.

### Fixed

- **`test_bash_tool` partial-output assertion** remains a known environment limitation (macOS PTY without the `script` wrapper cannot stream partial output through `create_shell_subprocess`); unrelated to this release and left for a follow-up cross-platform pass.

## [1.9.3] - 2026-08-16

Six high-value capability gaps identified in the post-1.9.2 UX review (design §15.3) are now implemented end to end: BYOK custom model providers, convergence dashboard in the TUI, failure recovery via checkpoints, session workspace isolation, budget enforcement / rate limiting, and an HTML report service with round replay.

### Added

- **Custom model provider (BYOK)** (`config/settings.py`): `ProviderProfile` (BaseModel) supports custom API format / auth source / `base_url` / default model. Ten built-in provider profiles (claude-api / claude-subscription / openai-compatible / codex / copilot / moonshot / gemini / minimax / nvidia / qwen / modelscope) plus `default_provider_profiles()` / `merged_profiles()` / `resolve_profile()`. `ih provider add` registers a custom endpoint; `auth/manager.py` loads credentials per profile; `/model` shows profile state.
- **Convergence dashboard in the TUI**: `ReviewProgressEvent` (round / new_findings / per_dimension / token_cost) dispatches through `ui/app.py` into a React `ReviewProgressPanel` — findings sparkline, per-dimension counts, cumulative cost.
- **Failure recovery / checkpoint resume** (`iterate/checkpoint.py`, `iterate/last_state.py`): atomic checkpoints (`save_checkpoint` / `load_checkpoint` / `clear_checkpoint` with file locking) persist the last successful round's state; the engine reloads it on resume so a model failure resumes from the last converged point instead of round 1.
- **Session workspace isolation** (`iterate/worktree_flow.py`, `iterate/worktree_runtime.py`, `swarm/worktree.py`): `WorktreeSession` (serializable) + git command wrapper create / merge / roll back a dedicated git worktree per round; the engine (`engine/query.py`) runs fix rounds inside the sandboxed worktree, merges on success, drops on failure. Includes per-repo namespace hashing and stale-worktree cleanup.
- **Budget enforcement / rate limiting** (`iterate/loop_policy.py`): `IterateLoopPolicy` gains `total_token_budget` (hard token cap → STOP with closing report guidance) and `budget_usd` (CostMeter-cumulative USD cap → STOP) via `_budget_stop_reason()`; `max_turns_per_minute` throttling (`RATE_LIMIT_WINDOW_SECONDS=60` rolling window + `_throttle_delay`) is consulted by the engine via `before_request`.
- **HTML report service** (`iterate/report_server.py`, `iterate/html_report.py`): static HTTP server (`serve_report`, oneshot/persist modes, auto-browser-open, MIME table) and an interactive round-replay page (`build_replay_page` — per-round panels, prev/next navigation, jump dots, keyboard ←/→, type-specific entry cards, HTML-escaped against XSS). Both CLI (`iterate report --serve/--serve-port/--serve-persist`) and TUI (`/iterate report --serve`) entry points.

### Fixed

- **`mcp` dependency pin** (`pyproject.toml`): narrowed `mcp>=1.0.0` to `mcp>=1.0.0,<2.0.0` — mcp 2.0 removed `mcp.server.fastmcp`, breaking the MCP HTTP integration tests. The upper bound keeps the FastMCP server API that the tests rely on.
- **Package `py.typed` marker** (`src/iterate_harness/py.typed`): the package previously shipped without a typed marker, so mypy treated it as an untyped third-party package and skipped analysis. The marker makes mypy type-check the harness source properly (new modules verified clean).

## [1.9.2] - 2026-08-16

Found during the post-1.9.1 full code review of the harness core (`iterate/` loop bookkeeping, `services/cron_scheduler`, `iterate/onboard_cmd`): four real data-integrity / robustness / portability defects, each with a regression test.

### Fixed

- **Decision log defensive parsing** (`iterate/decision_log.py`): `read_entries` raised `ValueError`/`TypeError` on malformed entries (non-numeric `round`, non-mapping `data`), crashing consumers like `report --fail-on` and trend analysis on a single bad line. Field parsing is now guarded; malformed lines are skipped with a warning instead of aborting the whole read. Tests: non-numeric round skipped, non-mapping data skipped, corrupt-file resilience.
- **Trend library key mismatch** (`iterate/trend_store.py`): `TrendRecord.to_dict()` serialized camelCase (`firstSeen`/`lastSeen`/`fixedAt`) while reads used snake_case, so cross-run trend classification (`new`/`fixed`/`regressed`/`stubborn`) silently misread persisted data after a restart. Serialization standardized on snake_case to match deserialization.
- **Onboarding config overwrite** (`iterate/onboard_cmd.py`): `run_onboard` (and thus `reonboard`) rebuilt `iterate.config.yaml` from scratch, dropping user-owned sections (personalization, review, budget, cron, …) whenever onboarding ran against an existing config. New fields now merge over the existing config (`_merge_into_existing`) instead of replacing it.
- **Cron scheduler daemon on Windows** (`services/cron_scheduler.py`): `start_daemon` used `os.fork()`/`os.setsid()`, which are Unix-only and crash on Windows. Daemon spawn now uses `subprocess.Popen` with a fully detached child, keeping the scheduler usable on all supported platforms.

### Reviewed (no change)

- **Pre-commit hook gate** (`iterate/git_hook.py`): the `|| exit 1` after `iterate review --changed --clean-ok` was flagged during review. Analysis: `review --changed` only exits non-zero on a genuine review failure (model/auth crash) or invalid ref; in that case failing the commit closed is the intended fail-closed behavior, and it does NOT bypass the `report --fail-on` severity gate (which runs whenever review exits 0). Removing it would make the hook fail-open on a crashed review — left unchanged.

## [1.9.1] - 2026-08-16

Distribution-only patch for the npm wrapper. Found during the 1.9.0 release e2e: on hosts where Python's TLS trust store is broken (typical for macOS python.org installs that never ran `Install Certificates.command`), `pip install <github tarball>` fails certificate verification while every other tool on the machine (curl, Node, browsers) downloads the same URL fine — the wrapper dead-ended with guidance text.

### Fixed

- **Tarball download fallback** (`harness/iterate-harness/npm`): when the pinned GitHub release tarball cannot be pip-installed, the wrapper now retries by downloading the tarball into `~/.iterate-harness-npm/cache/` and pip-installing the local file. The download itself is two-tiered: Node's own https stack first (redirect-following, 120s timeout), then `curl` (system trust store — survives intercepting proxies and Node's bundled-CA gaps; TLS verification stays ON in both tiers, partial files are cleaned up between attempts). Non-http targets (local path / `ITERATE_HARNESS_INSTALL_URL` overrides) keep the old single-attempt behavior; when everything fails, the error carries the original pip diagnosis plus every download-tier failure reason.
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

- **npm distribution wrapper** (`iterate-harness` on npm, maintained at `harness/iterate-harness/npm/` in the iterate-skill monorepo): `npm install -g iterate-harness` exposes `ih` everywhere npm works. The wrapper is a zero-dependency Node shim — first run resolves Python ≥ 3.10 (env override `ITERATE_HARNESS_PYTHON`), creates a managed venv (`~/.iterate-harness-npm`), pip-installs the release tarball **pinned to the npm package version** (lockstep: npm 1.6.0 → harness v1.6.0, npm upgrades self-heal on next run), then delegates to the real `ih` with argv/stdio/signals/exit-code passthrough. Requires network access to pypi.org + github.com for the one-time install; `ITERATE_HARNESS_SKIP_INSTALL=1` runs an existing install directly. 10 unit tests (`node --test`) cover the pure helpers.

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

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.
