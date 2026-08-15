# Changelog

All notable changes to iterate-harness should be recorded in this file.

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.

## [Unreleased]

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
