"""Canonical iterate workflow prompts for the harness runtime.

Adapted from the dsh plugin's ``skill-prompt.ts``: the plugin drove a JS
``workflow`` tool; the harness runtime instead has the kernel agent loop
plus the six ``iterate_*`` tools and the ``agent`` tool (parallel subagent
spawns). These templates teach the model the SAME canonical loop shape,
with convergence enforced deterministically by the engine-level
:class:`~iterate_harness.iterate.loop_policy.IterateLoopPolicy`.
"""

from __future__ import annotations

import json

#: Name of the default kickoff template preset (keeps current behavior).
DEFAULT_TEMPLATE = "standard"

#: Registered kickoff template presets. Each preset carries the suffix text
#: appended to dry-run / normal kickoffs plus a human-readable description.
TEMPLATE_PRESETS: dict[str, dict[str, str]] = {
    "standard": {
        "dry_run_suffix": "",
        "normal_suffix": "",
        "description": "Balanced depth — default behavior",
    },
    "strict": {
        "dry_run_suffix": " Be thorough: for every finding, verify the failure scenario is concrete and reproducible. Flag any finding where the suggested fix introduces a new risk. Prefer false positives over missed issues.",
        "normal_suffix": " Be conservative: prefer minimal, targeted fixes. After every fix, consider whether it could introduce a regression. Do not fix architecture-level issues — only atomic findings.",
        "description": "Conservative — emphasizes safety and thoroughness",
    },
    "quick": {
        "dry_run_suffix": " Focus on the most impactful findings only. Skip low-severity issues unless they compound. Respond quickly.",
        "normal_suffix": " Fix the most impactful findings first. Skip low-severity auto-fixes. Goal is a fast pass, not exhaustive coverage.",
        "description": "Fast — impacts only, minimal depth",
    },
}


def template_suffix(template: str, mode: str) -> str:
    """Return the preset's kickoff suffix for ``mode`` (``"dry-run"``/``"normal"``).

    The mode value is normalized ("dry-run" -> "dry_run") to match the
    ``{mode}_suffix`` keys in :data:`TEMPLATE_PRESETS`. Unknown templates
    degrade gracefully to the standard preset (empty suffix), so a typo or
    a stale caller never changes behavior.
    """
    key_mode = mode.replace("-", "_")
    presets = TEMPLATE_PRESETS.get(template) or TEMPLATE_PRESETS[DEFAULT_TEMPLATE]
    return str(presets.get(f"{key_mode}_suffix") or "")


def list_templates() -> list[dict[str, str]]:
    """Return metadata for every available template preset (menus/CLI use)."""
    return [
        {
            "name": name,
            "description": str(data.get("description") or ""),
        }
        for name, data in TEMPLATE_PRESETS.items()
    ]


ITERATE_SKILL_PROMPT = """## Iterate Workflow (autonomous code iteration)

You have the iterate harness installed, which registers these tools:
- `iterate_config` — read iterate.config.yaml (dimensions, validation commands, personalization)
- `iterate_validate` — run a whitelisted validation command (EXACT match only)
- `iterate_decision_log` — append to / read the append-only decision log
- `iterate_context` — read SKILL.md / ITERATE.md project context
- `iterate_review` — deterministic review engine: `plan` builds the review plan; `aggregate` dedupes/merges findings and computes convergence; `meta-review` audits a report for internal consistency.
- `iterate_triage` — after a review, walk findings with the user y (fix) / n (skip) / a (always-ignore); `a` persists to known_intentional so future rounds filter it.

### When to use
When the user asks to review or iterate on the project (e.g. "review this project", "iterate on error handling", "check the codebase for issues", "dry-run review", "反复审查"):
- If the user says "review only" / "dry run" / "不要改文件" / "反复审查" → use mode `dry-run`.
- Otherwise → use mode `normal`.

### Dry-run mode (pure review — the ONLY mode that never touches files)
Repeated review rounds until findings converge, then produce an auditable
report, then audit the report itself (meta-review). NO file writes, NO git,
NO branches, NO worktree.

Canonical loop — reproduce this structure exactly (adjust dims via the plan):
1. Call `iterate_review(operation="plan", mode="dry-run")` to get the plan
   (dimensions, per-dimension reviewer prompts, findings schema, maxReviewRounds).
2. Review round r: spawn ONE `agent` call per dimension IN THE SAME turn
   (parallel tool calls) using the plan's reviewer prompt for that dimension.
   Feed the already-known findings so reviewers hunt NEW issues only.
3. Call `iterate_review(operation="aggregate", mode="dry-run", rounds=[...])`
   with ALL rounds so far. It deterministically dedupes, filters
   known_intentional, sorts by severity, and computes convergence.
4. If the aggregate reports 0 NEW findings in the last round → converged, stop.
   Otherwise next round. Stop at maxReviewRounds regardless.
5. Call `iterate_review(operation="meta-review", report=...)` to audit the
   final report (counts, severity buckets, dimension sums, sort order,
   convergence math). `verdict` is `approved` only if every check passes.
6. Append exactly ONE `report` entry to the decision log. Nothing else is written.
   When the meta-review output carries a `thresholdGate` block (project
   thresholds configured), copy it verbatim into the report entry under
   `thresholdGate` so CI (`ih iterate report`) can gate on it.
7. When the user wants a say in the findings, offer triage via
   `iterate_triage(findings=[...])` — the user answers y/n/a per finding and
   `a` entries are persisted to known_intentional automatically.

Key rules for dry-run:
- NEVER edit files / create branches or worktree. Reviewers read only.
- Each round feeds already-known findings to reviewers so they hunt NEW
  issues only — that is what drives convergence.
- The engine-level loop policy may stop the loop for you when convergence
  is detected; the aggregate tool is the single source of truth.

### Normal mode (autonomous closed loop)
Loop: plan → parallel review ×N → fix atomic issues → validate → loop →
auto-stop when a round yields nothing left to fix.

Canonical loop — reproduce this structure exactly:
1. Call `iterate_config(validate=true)`; then
   `iterate_review(operation="plan", mode="normal")`.
2. Round r: parallel per-dimension review of the CURRENT code state
   (previous rounds' atomic findings are fixed). Do NOT re-report known
   architectural findings.
3. `iterate_review(operation="aggregate", mode="normal", rounds=[this_round])`
   → act ONLY on the deduped/filtered/sorted `findings` it returns.
4. Fix `is_atomic` findings with the smallest possible change (single file,
   single function, <= atomic.max_lines). Architectural findings are
   REPORTED, never auto-fixed.
5. Run every command in validation.commands via `iterate_validate` — the
   tool only executes EXACT preconfigured commands. Validation failures
   trigger rollback of this round's fixes (git isolation handles this).
6. Append a `review_result` decision-log entry every round; a final
   `report` entry when the loop ends. Stop when a round produces nothing
   to fix or maxReviewRounds is reached.

Key rules for normal mode:
- Fixers are the ONLY agents allowed to write files; reviewers read only.
- Aggregate deterministically before fixing, so fixes act on deduped
  findings.
- Validate after every round of fixes; validation results are logged,
  never silently dropped.

### Finding schema (for reviewer agents)
{ "dimension": string, "file": string (relative path), "line": number (optional),
  "severity": "critical" | "high" | "medium" | "low", "summary": string (one line),
  "failure_scenario": string (how/when it fails), "suggested_fix": string (the concrete fix),
  "is_atomic": boolean (true if fix <= atomic.max_lines within a single file/function) }
Atomic = is_atomic true. Architectural = everything else.

Always end with a clear summary: total findings, count by severity, fixes
applied (normal) or convergence stats (dry-run), and remaining
architectural findings.
"""


def changed_scope_clause(changed_files: list[str] | None) -> str:
    """Extra instruction block for changed-only quick reviews.

    Embeds the delta file list and directs the model to forward it to
    ``iterate_review(operation="plan", changed_files=[...])`` so every
    reviewer prompt carries the same restricted scope.
    """
    if not changed_files:
        return ""
    listing = json.dumps(changed_files, ensure_ascii=False)
    return (
        " This is a CHANGED-ONLY quick review: restrict the entire loop to "
        "the files below (relative to the repo root). Call "
        f'iterate_review(operation="plan", changed_files={listing}) so the '
        "review plan pins this exact scope; fix findings (normal mode) also "
        "stay within these files. Changed files:\n"
        + "\n".join(f"- {path}" for path in changed_files)
    )


def personalization_constraints(cwd: str | None) -> str:
    """Render the project's structured personalization rules as prompt text.

    Mirrors the skill's SKILL.md "Load personalization" step: protected
    paths, risk areas, forbidden fixes, fix priority and dimension focus
    are surfaced to every review/fix loop kickoff. Hard boundaries
    (protected paths as deny rules) are ALSO enforced by the permission
    layer; this clause covers prompt-side compliance (forbidden fixes,
    risk-area approval, reviewer focus).
    """
    if not cwd:
        return ""
    from .config_loader import load_effective_config

    personalization = load_effective_config(cwd).config.personalization
    if not isinstance(personalization, dict) or not personalization:
        return ""

    lines: list[str] = []
    protected_raw = personalization.get("protected_paths")
    protected = [str(p) for p in (protected_raw if isinstance(protected_raw, list) else [])]
    if protected:
        lines.append(
            "Protected paths (NEVER modify, enforced by permissions too): "
            + ", ".join(f"`{p}`" for p in protected)
        )
    risks_raw = personalization.get("risk_areas")
    risks = [
        (str(item.get("path", "")), str(item.get("reason", "")))
        for item in (risks_raw if isinstance(risks_raw, list) else [])
        if isinstance(item, dict)
    ]
    if risks:
        rendered = ", ".join(f"`{path}` ({reason or 'no reason given'})" for path, reason in risks)
        lines.append(f"Risk areas (changes require explicit user approval): {rendered}")
    forbidden_raw = personalization.get("forbidden_fixes")
    forbidden = [str(f) for f in (forbidden_raw if isinstance(forbidden_raw, list) else [])]
    if forbidden:
        lines.append(
            "Forbidden fix approaches (never use): " + ", ".join(f"`{f}`" for f in forbidden)
        )
    priority_raw = personalization.get("fix_priority_order")
    priority = [str(d) for d in (priority_raw if isinstance(priority_raw, list) else [])]
    if priority:
        lines.append("Fix priority order (high to low): " + " > ".join(priority))
    focus_raw = personalization.get("dimension_focus")
    focus = [
        (str(item.get("dimension", "")), str(item.get("focus", "")))
        for item in (focus_raw if isinstance(focus_raw, list) else [])
        if isinstance(item, dict)
    ]
    for dimension, focus_text in focus:
        if dimension and focus_text:
            lines.append(f"Extra focus for dimension `{dimension}`: {focus_text}")
    if not lines:
        return ""
    return (
        "\n\nPersonalization constraints from iterate.config.yaml (apply throughout):\n"
        + "\n".join(f"- {line}" for line in lines)
    )


def load_onboarding_template() -> str:
    """Read the bundled ITERATE.md skeleton (never fails; inline fallback)."""
    from pathlib import Path

    template_path = Path(__file__).parent / "data" / "ITERATE.template.md"
    try:
        return template_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def onboarding_kickoff(
    *,
    project_root: str,
    goal: str,
    dimensions: list[str],
    evidence_lines: list[str],
    channel: str,
    completed_at: str,
    preserve_user_section: str | None = None,
) -> str:
    """First-turn prompt that boots the model-driven project onboarding scan.

    The model explores the repository with its normal read tools, fills the
    AI-maintained region of the ITERATE.md skeleton from real evidence, and
    writes the complete file. Harness code (not the model) then validates the
    region markers, captures manifest fingerprints, and writes
    ``iterate.config.yaml`` — untrusted model output never touches trusted
    config structure.
    """
    from .onboarding import (
        AI_END_MARKER,
        AI_START_MARKER,
        FINGERPRINT_VERSION,
        SENSITIVE_SKIP_PATTERNS,
    )

    evidence = "\n".join(f"- {line}" for line in evidence_lines) or "- (none)"
    skeleton = load_onboarding_template()
    sensitive = ", ".join(SENSITIVE_SKIP_PATTERNS)
    preserve_clause = ""
    if preserve_user_section is not None:
        preserve_clause = (
            "\n\nRE-ONBOARD MODE: an existing user-owned region must be "
            "preserved VERBATIM. Replace the skeleton's user-owned region "
            f"(from `{AI_START_MARKER.split(':')[0]}:USER-OWNED:START -->` to "
            "its END marker) with EXACTLY the text between the fences below, "
            "markers included, unchanged:\n\n```\n"
            + preserve_user_section
            + "\n```"
        )
    return (
        "Run the iterate project onboarding scan now and create ITERATE.md — "
        "the project knowledge base used by every later iterate review.\n\n"
        "## Step 1 — Scan (read-only)\n"
        "- Read the project manifests (package.json, pyproject.toml, go.mod, "
        "Cargo.toml, …) to identify languages, frameworks and package managers.\n"
        "- List the directory tree 2-3 levels deep (skip node_modules, .git, "
        "build artifacts) to map the module structure.\n"
        "- Check for specs/, tests/, and CI configuration.\n"
        "- Read README.md / CONTRIBUTING.md if present for the project description.\n"
        f"- NEVER read sensitive files: {sensitive}.\n"
        "- Detection evidence gathered by the harness (verify, don't blindly trust):\n"
        f"{evidence}\n\n"
        "## Step 2 — Write ITERATE.md\n"
        f"Write the complete file to `{project_root}/ITERATE.md` using the "
        "skeleton below. Fill EVERY `{{PLACEHOLDER}}` from your scan results "
        f"(metadata table: completed_at={completed_at}, channel={channel}, "
        f"fingerprint_version={FINGERPRINT_VERSION}, project_root={project_root}). "
        "The two region marker comment lines "
        f"`{AI_START_MARKER}` … `{AI_END_MARKER}` (and the USER-OWNED pair) "
        "must appear EXACTLY as in the skeleton — later refresh flows locate "
        "regions by these byte-exact markers. Keep each section concise and "
        "factual; unknown areas say so instead of inventing details. "
        f"Review goal for context: {goal}. Dimensions enabled: "
        f"{', '.join(dimensions)}."
        + preserve_clause
        + "\n\n## Skeleton\n\n```md\n"
        + skeleton
        + "\n```\n\nAfter writing the file, reply with a one-paragraph summary "
        "of what you found. Do not modify any other file."
    )


def dry_run_kickoff(
    goal: str,
    max_rounds: int,
    changed_files: list[str] | None = None,
    cwd: str | None = None,
    template: str = DEFAULT_TEMPLATE,
) -> str:
    """First-turn prompt that boots the canonical dry-run loop."""
    return (
        f"Run an iterate dry-run review of this project now. Goal: {goal}. "
        f"Max review rounds: {max_rounds}.{changed_scope_clause(changed_files)}"
        + personalization_constraints(cwd)
        + " Follow the dry-run canonical loop "
        "exactly: plan via iterate_review, parallel per-dimension review, "
        "deterministic aggregate each round, stop on convergence (0 new "
        "findings) or the round cap, then meta-review the final report and "
        "append one report entry to the decision log. Do NOT modify any file."
        + template_suffix(template, "dry-run")
    )


def normal_kickoff(
    goal: str,
    max_rounds: int,
    changed_files: list[str] | None = None,
    cwd: str | None = None,
    template: str = DEFAULT_TEMPLATE,
) -> str:
    """First-turn prompt that boots the canonical normal-mode loop."""
    return (
        f"Run the iterate autonomous loop on this project now. Goal: {goal}. "
        f"Max rounds: {max_rounds}.{changed_scope_clause(changed_files)}"
        + personalization_constraints(cwd)
        + " Follow the normal-mode canonical loop "
        "exactly: config + plan, parallel per-dimension review of the current "
        "state, deterministic aggregate, fix ONLY atomic findings with "
        "minimal changes, validate every round via iterate_validate, roll "
        "back failed rounds, log every round, stop when nothing remains to "
        "fix or the round cap is reached."
        + template_suffix(template, "normal")
    )


def resume_kickoff(
    goal: str,
    max_rounds: int,
    last_summary: dict[str, object],
    template: str = DEFAULT_TEMPLATE,
) -> str:
    """First-turn prompt that resumes the last iterate run (breakpoint resume).

    ``last_summary`` is the payload from
    :func:`iterate_harness.iterate.last_state.summarize_last_run`.
    """
    verdict = str(last_summary.get("verdict") or "unknown")
    rounds_raw = last_summary.get("rounds")
    last_rounds = int(rounds_raw) if isinstance(rounds_raw, (int, str)) else 0
    total_raw = last_summary.get("totalFindings")
    total = int(total_raw) if isinstance(total_raw, (int, str)) else 0
    interrupted = last_summary.get("interrupted", False)
    preview_lines: list[str] = []
    preview_raw = last_summary.get("preview")
    for finding in (preview_raw if isinstance(preview_raw, list) else []):
        if not isinstance(finding, dict):
            continue
        preview_lines.append(
            "- [{severity}] {file} ({dimension}): {summary}".format(
                severity=str(finding.get("severity") or "?"),
                file=str(finding.get("file") or "?"),
                dimension=str(finding.get("dimension") or "?"),
                summary=str(finding.get("summary") or ""),
            )
        )
    preview = "\n".join(preview_lines) or "- (no finding previews recorded)"
    mode = str(last_summary.get("mode") or "normal")
    resume_suffix = template_suffix(template, mode)
    deferred = last_summary.get("deferred_architectural")
    deferred_block = ""
    if isinstance(deferred, list) and deferred:
        lines = [
            f"- [{str(d.get('file') or '?')}] ({str(d.get('dimension') or '?')}): "
            f"{str(d.get('summary') or '')}"
            for d in deferred[:20]
        ]
        deferred_block = (
            "\nPreviously deferred architectural findings (left unfixed for a "
            "follow-up — do not silently drop them):\n"
            + "\n".join(lines)
            + "\n"
        )
    per_dim_raw = last_summary.get("perDimension")
    per_dim = per_dim_raw if isinstance(per_dim_raw, dict) else {}
    if interrupted:
        return (
            f"Resume the interrupted iterate run on this project. Goal: {goal}. "
            f"The previous run was interrupted after round {last_rounds} (last "
            f"convergence checkpoint: {total} finding(s)). The checkpoint "
            f"per-dimension breakdown is: "
            f"{dict(per_dim)}. "
            "Re-read the decision log (.iterate/decision-log.jsonl) via "
            "iterate_log first, re-verify which of the previously reported "
            "findings still reproduce on the current state, then continue "
            "the canonical loop (dry-run rules if the last run was dry-run, "
            "normal fix rules otherwise) within a fresh cap of "
            f"{max_rounds} rounds. Previously reported findings:\n{preview}\n"
            f"{deferred_block}"
            "Do NOT re-report findings that no longer reproduce; log the "
            "resume as a decision entry before the first new round."
            + resume_suffix
        )
    return (
        f"Resume the last iterate run on this project. Goal: {goal}. "
        f"The previous run stopped after round {last_rounds} with verdict "
        f"\"{verdict}\" and {total} total finding(s). Re-read the decision "
        "log (.iterate/decision-log.jsonl) via iterate_log first, re-verify "
        "which of the previously reported findings still reproduce on the "
        "current state, then continue the canonical loop (dry-run rules if "
        "the last run was dry-run, normal fix rules otherwise) within a "
        f"fresh cap of {max_rounds} rounds. Previously reported findings:\n"
        f"{preview}\n"
        f"{deferred_block}"
        "Do NOT re-report findings that no longer reproduce; log the resume "
        "as a decision entry before the first new round."
        + resume_suffix
    )


def next_round_instruction(
    round_number: int,
    new_findings: int,
    *,
    exhausted_dimensions: list[str] | None = None,
) -> str:
    """Engine-injected message steering the next review round.

    ``exhausted_dimensions`` lists dimensions whose configured token budget
    is already spent — the next round must skip spawning reviewers for them.
    """
    base = (
        f"[iterate] Round {round_number} recorded {new_findings} new finding(s). "
        "Start the next review round now: re-review every dimension IN "
        "PARALLEL on the current state, feed the already-known findings so "
        "reviewers hunt NEW issues only, then aggregate via "
        'iterate_review(operation="aggregate").'
    )
    if exhausted_dimensions:
        listing = ", ".join(sorted(exhausted_dimensions))
        base += (
            " Token budgets are EXHAUSTED for: "
            f"{listing} — do NOT spawn reviewer agents for these dimensions "
            "this round; review only the remaining dimensions."
        )
    return base


def convergence_stop_notice(reason: str, total_findings: int) -> str:
    """Engine-injected stop notice when the loop policy halts the loop."""
    return (
        f"[iterate] Review loop stopped: {reason}. Total findings: "
        f"{total_findings}. Produce the final report now: aggregate, "
        'meta-review via iterate_review(operation="meta-review"), append '
        "the single report entry to the decision log, and summarize."
    )


def pause_menu_question(round_number: int, new_findings: int, reason: str | None = None) -> str:
    """The Esc intervention menu (design §11.2.1) shown at a round boundary.

    Answers (parsed by the engine): ``s`` skip the current top finding,
    ``n <dims>`` narrow the review dimensions, ``x`` stop now, empty/other
    resumes the normal loop.
    """
    head = f"Iterate loop paused (round {round_number}, {new_findings} new finding(s))"
    if reason == "stall":
        head = (
            f"Iterate loop paused: no new findings for {round_number} round(s) — "
            "review may be stalled"
        )
    elif reason == "budget":
        head = f"Iterate loop paused: budget headroom low (round {round_number}, {new_findings} new finding(s))"
    return (
        f"{head}.\n"
        "Choose an action:\n"
        "  s = skip the current top finding, then continue\n"
        "  n <dimensions> = narrow the review to the given dimensions, then continue\n"
        "  x = stop the loop now\n"
        "  (empty / anything else = resume the normal loop)"
    )


def pause_menu_title(round_number: int, new_findings: int, reason: str | None = None) -> str:
    """Title for the componentized (directional-key) pause menu."""
    if reason == "stall":
        return (
            f"Iterate loop paused: no new findings for a while — "
            f"round {round_number}, {new_findings} new finding(s)"
        )
    if reason == "budget":
        return (
            f"Iterate loop paused: budget headroom low — "
            f"round {round_number}, {new_findings} new finding(s)"
        )
    return f"Iterate loop paused — round {round_number}, {new_findings} new finding(s)"


def budget_headroom_pause_notice(reason: str) -> str:
    """Engine-injected notice when the loop pauses for budget headroom.

    The user is asked to either resume (spending past the headroom; the hard
    stop still applies once the budget is exhausted) or stop the loop now
    (design §18.5).
    """
    return (
        f"[iterate] Loop paused before the next round: {reason}.\n"
        "The run budget is running low. Choose an action:\n"
        "  resume = continue the loop (the configured budget hard-stop still applies)\n"
        "  stop = stop the loop now and produce the final report\n"
        "  (or use the conversation panel to top up the budget / inject a nudge)"
    )


def pause_menu_options() -> list[dict[str, str]]:
    """Options for the componentized pause menu (values parsed by the engine).

    ``resume`` first so Esc-cancel (which submits the cancel value) keeps
    the loop running unchanged — the safe default.
    """
    return [
        {
            "value": "resume",
            "label": "Resume loop",
            "description": "continue the normal loop unchanged",
        },
        {
            "value": "skip",
            "label": "Skip top finding",
            "description": "drop the current top finding and continue",
        },
        {
            "value": "narrow",
            "label": "Narrow dimensions…",
            "description": "restrict the review to specific dimensions",
        },
        {
            "value": "stop",
            "label": "Stop loop",
            "description": "halt now and produce the final report",
        },
    ]


def narrow_dimensions_question() -> str:
    """Follow-up question after the pause-menu ``narrow`` selection."""
    return (
        "Narrow the review to which dimensions? Reply with a comma-separated "
        "list (e.g. security, correctness). Empty cancels narrowing and "
        "resumes the normal loop."
    )


def skip_current_finding_instruction() -> str:
    """Engine-injected message for the pause-menu ``s`` answer."""
    return (
        "[iterate] User intervention: SKIP the current top finding (mark it "
        "skipped in the decision log with a decision entry), then continue "
        "the loop with the next finding — do not spend another round on it."
    )


def narrow_dimensions_instruction(dimensions: str) -> str:
    """Engine-injected message for the pause-menu ``n <dims>`` answer."""
    return (
        f"[iterate] User intervention: NARROW the review to these dimensions "
        f"only: {dimensions}. Log the narrowing as a decision entry, then "
        "continue the loop restricted to them."
    )
