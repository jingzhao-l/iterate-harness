"""``ih iterate onboard / refresh / reonboard`` orchestration.

Implements the skill's full onboarding loop on top of the harness runtime:

1. ``onboard``  — auth gate → detection evidence → interactive Q&A →
   MODEL-DRIVEN project scan that writes ``ITERATE.md`` → marker validation →
   fingerprint capture → ``iterate.config.yaml`` (with the ``onboarding``
   section, schema-compatible with the skill). ``--no-ai`` degrades to a
   detection-rendered knowledge base without calling the model.
2. ``refresh``  — re-capture fingerprints, report drift, refresh the config
   ``onboarding`` section + the ITERATE.md metadata row. Never calls the
   model, never touches the user-owned region.
3. ``reonboard``— backup both files, re-run the full model onboarding while
   preserving the existing user-owned region verbatim; rolls the backups in
   on failure.

Trust boundary: the model only ever writes ``ITERATE.md`` (untrusted prose).
``iterate.config.yaml`` — including fingerprints and the onboarding section —
is always serialized by harness code via ``yaml.safe_dump``.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no stubs in this env

from . import init_wizard, onboarding, prompts

logger = logging.getLogger(__name__)


def _print_flush(message: str) -> None:
    print(message, flush=True)


def check_auth_configured() -> str | None:
    """Return None when a model credential is configured, else guidance."""
    try:
        from iterate_harness.config.settings import load_settings

        settings = load_settings()
        resolved = settings.materialize_active_profile().resolve_auth()
        if resolved.value or resolved.auth_kind == "none":
            return None
    except Exception:
        logger.debug("Auth pre-check failed; falling back to no-ai guidance", exc_info=True)
    return (
        "No model credential configured.\n"
        "  Run `ih auth login` first (or `ih setup`), then re-run onboarding.\n"
        "  OrcaRouter free models: no key yet? Run `ih setup orcarouter` and\n"
        "  press Enter at the key prompt to open the signup page in your browser.\n"
        "  Alternatively use `ih iterate onboard --no-ai` for a detection-only\n"
        "  knowledge base that does not call the model."
    )


def _load_existing_config(path: Path) -> dict[str, object] | None:
    """Read an existing config as a dict, or None when absent/unreadable."""
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return raw if isinstance(raw, dict) else None


def _merge_into_existing(
    new_config: dict[str, object], config_path: Path
) -> dict[str, object]:
    """Merge a freshly built config over an existing one, preserving user sections.

    ``onboard``/``reonboard`` must refresh goal/dimensions/rounds/validation and
    the ``onboarding`` section WITHOUT dropping user-owned sections
    (personalization, review, budget, cron, …) that already live in the config.
    """
    existing = _load_existing_config(config_path)
    if existing is None:
        return new_config
    merged = dict(existing)
    merged.update(new_config)
    return merged


def render_detection_iterate_md(
    *,
    profile: init_wizard.ProjectProfile,
    goal: str,
    dimensions: list[str],
    channel: str,
    completed_at: str,
    project_root: str,
) -> str:
    """Render a detection-based ITERATE.md (the ``--no-ai`` path)."""
    template = prompts.load_onboarding_template()
    languages = ", ".join(profile.languages) if profile.languages else "unknown"
    top_dirs = _top_level_dirs(project_root)
    module_map = "\n".join(f"| `{name}/` | {_guess_purpose(name)} |" for name in top_dirs)
    if not module_map:
        module_map = "| (no top-level source directories detected) | |"
    values = {
        "COMPLETED_AT": completed_at,
        "CHANNEL": channel,
        "FINGERPRINT_VERSION": onboarding.FINGERPRINT_VERSION,
        "PROJECT_ROOT": project_root,
        "PROJECT_OVERVIEW": f"{goal}. Detected stack: {languages}.",
        "TECH_STACK": f"- Languages / runtimes: {languages}\n"
        f"- Test command (evidence-based): {profile.test_command or 'not detected'}",
        "MODULE_MAP": module_map,
        "RECOMMENDED_DIMENSIONS": "\n".join(f"- {dim}" for dim in dimensions),
        "ITERATE_NOTES": "- Detection-only knowledge base (`--no-ai`); re-run "
        "`ih iterate reonboard` for a model-driven scan.\n"
        "- Review evidence lines in `iterate.config.yaml` before trusting the test command.",
    }
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


_DIR_PURPOSES: tuple[tuple[str, str], ...] = (
    ("src", "源码 / source code"),
    ("lib", "库代码 / library code"),
    ("api", "API 层 / API layer"),
    ("routes", "路由 / routing"),
    ("controllers", "控制器 / controllers"),
    ("handlers", "请求处理 / request handlers"),
    ("services", "业务服务 / services"),
    ("tests", "测试 / tests"),
    ("test", "测试 / tests"),
    ("docs", "文档 / docs"),
    ("config", "配置 / configuration"),
    ("scripts", "脚本 / scripts"),
    ("migrations", "数据库迁移 / DB migrations"),
    ("components", "UI 组件 / UI components"),
    ("pages", "页面 / pages"),
    ("frontend", "前端 / frontend"),
    ("backend", "后端 / backend"),
    ("web", "Web 端 / web app"),
    ("tools", "工具 / tooling"),
)

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build",
    ".next", "target", "Pods", "DerivedData", ".idea", ".vscode",
}


def _top_level_dirs(project_root: str) -> list[str]:
    try:
        entries = sorted(Path(project_root).iterdir())
    except OSError:
        return []
    return [
        entry.name
        for entry in entries
        if entry.is_dir() and not entry.name.startswith(".") and entry.name not in _SKIP_DIRS
    ][:20]


def _guess_purpose(name: str) -> str:
    for key, purpose in _DIR_PURPOSES:
        if name.lower() == key:
            return purpose
    return "（待模型扫描确认 / to confirm via model scan）"


def _confirm_questions(
    *,
    yes: bool,
    profile: init_wizard.ProjectProfile,
    goal: str,
) -> tuple[list[str] | None, str, int]:
    """Interactive Q&A shared by onboard; returns (dimensions, goal, rounds)."""
    import typer

    offered = profile.suggested_dimensions
    _print_flush("Suggested dimensions:")
    for index, dim in enumerate(offered, 1):
        _print_flush(f"  {index}. {dim}")
    final_goal = goal.strip() or (
        f"Iterative review for this {profile.languages[0] if profile.languages else 'software'} project"
    )
    if yes:
        return offered, final_goal, 3
    raw = typer.prompt(
        "Dimensions to keep (comma-separated numbers/names, empty = all)", default=""
    )
    chosen = init_wizard.parse_dimension_selection(raw, offered)
    while chosen is None:
        _print_flush("Invalid selection — use numbers or exact dimension names.")
        raw = typer.prompt("Dimensions to keep (empty = all)", default="")
        chosen = init_wizard.parse_dimension_selection(raw, offered)
    if not goal.strip():
        final_goal = typer.prompt("Review goal", default=final_goal)
    rounds = typer.prompt("Max review rounds", default=3, type=int)
    return chosen, final_goal, rounds


def run_onboard(
    *,
    yes: bool = False,
    goal: str = "",
    no_ai: bool = False,
    allow_existing: bool = False,
    preserve_user_section: str | None = None,
) -> int:
    """Full onboarding; returns a process exit code (0 = success)."""
    cwd = Path.cwd()
    md_path = cwd / onboarding.ITERATE_MD_FILENAME
    if md_path.exists() and not allow_existing:
        _print_flush(
            f"{onboarding.ITERATE_MD_FILENAME} already exists ({md_path}).\n"
            "Use `ih iterate refresh` to re-fingerprint, or "
            "`ih iterate reonboard` to re-scan with the model (user region preserved)."
        )
        return 1

    channel = "cli" if no_ai else "ai"
    if not no_ai:
        auth_error = check_auth_configured()
        if auth_error is not None:
            _print_flush(auth_error)
            return 1

    profile = init_wizard.detect_project(cwd)
    _print_flush(
        f"Detected stack: {', '.join(profile.languages) if profile.languages else 'unknown'}"
    )
    for line in profile.evidence:
        _print_flush(f"  - {line}")

    chosen, final_goal, rounds = _confirm_questions(yes=yes, profile=profile, goal=goal)
    assert chosen is not None  # _confirm_questions re-prompts until the selection is valid

    config = init_wizard.build_config_dict(
        goal=final_goal,
        dimensions=chosen,
        max_rounds=rounds,
        test_command=profile.test_command,
    )
    completed_at = onboarding.utc_now_iso()
    _print_flush(
        "\nOnboarding plan:\n"
        f"  - knowledge base : {md_path} ({'detection-only' if no_ai else 'MODEL-DRIVEN scan'})\n"
        f"  - config         : {init_wizard.existing_config_path(cwd)}\n"
        f"  - channel        : {channel}"
    )

    import typer

    if not yes and not typer.confirm("Start onboarding", default=True):
        _print_flush("Aborted — nothing written.")
        return 1

    if no_ai:
        content = render_detection_iterate_md(
            profile=profile,
            goal=final_goal,
            dimensions=chosen,
            channel=channel,
            completed_at=completed_at,
            project_root=str(cwd),
        )
        if preserve_user_section is not None:
            content = onboarding.replace_user_owned_section(content, preserve_user_section)
        onboarding.write_iterate_md(cwd, content)
    else:
        kickoff = prompts.onboarding_kickoff(
            project_root=str(cwd),
            goal=final_goal,
            dimensions=chosen,
            evidence_lines=profile.evidence,
            channel=channel,
            completed_at=completed_at,
            preserve_user_section=preserve_user_section,
        )
        _print_flush("Invoking model for the project scan (streaming below)…\n")
        from iterate_harness.ui.app import run_print_mode

        asyncio.run(run_print_mode(prompt=kickoff, permission_mode="full_auto"))

    errors = onboarding.validate_iterate_md(md_path)
    if errors:
        _print_flush(f"\nITERATE.md validation failed: {'; '.join(errors)}")
        _print_flush("The model output was kept on disk for inspection; config NOT written.")
        return 1

    fingerprints = onboarding.capture_fingerprints(cwd)
    config["onboarding"] = onboarding.build_onboarding_section(
        channel=channel, fingerprints=fingerprints, completed_at=completed_at
    )
    safe_config = _merge_into_existing(config, init_wizard.existing_config_path(cwd))
    written_config = init_wizard.write_config(cwd, safe_config)
    _print_flush("\nOnboarding complete:")
    _print_flush(f"  - {md_path}")
    _print_flush(f"  - {written_config} ({len(fingerprints)} manifest fingerprints)")
    _print_flush("Next: `ih iterate review --changed` for a quick changed-only review.")
    return 0


def run_refresh() -> int:
    """Re-fingerprint + drift report + metadata refresh; no model call."""
    cwd = Path.cwd()
    if not onboarding.is_onboarded(cwd):
        _print_flush("Not onboarded — run `ih iterate onboard` first.")
        return 1
    config_path = init_wizard.existing_config_path(cwd)
    if not config_path.exists():
        _print_flush(f"{init_wizard.CONFIG_FILENAME} missing — cannot refresh fingerprints.")
        return 1

    stored = onboarding.load_stored_fingerprints(cwd)
    ignores = onboarding.load_drift_ignore(cwd)
    drift = onboarding.check_drift(cwd, stored, ignores)
    _print_flush(drift.summary())

    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _print_flush(f"Cannot read {config_path}: {exc}")
        return 1
    if not isinstance(raw_config, dict):
        _print_flush(f"{config_path} is not a valid mapping — aborting.")
        return 1

    md_text = onboarding.read_iterate_md(cwd)
    if md_text is None:
        _print_flush("ITERATE.md became unreadable — aborting.")
        return 1

    completed_at = onboarding.utc_now_iso()
    old_config_text = config_path.read_text(encoding="utf-8")
    onboarding_section = raw_config.get("onboarding")
    if not isinstance(onboarding_section, dict):
        onboarding_section = {}
    previous_channel = onboarding_section.get("channel")
    onboarding_section.update(
        onboarding.build_onboarding_section(
            channel=previous_channel if isinstance(previous_channel, str) else "cli",
            fingerprints=onboarding.capture_fingerprints(cwd, ignores),
            completed_at=completed_at,
        )
    )
    raw_config["onboarding"] = onboarding_section

    try:
        config_path.write_text(
            init_wizard.render_config_text(raw_config), encoding="utf-8"
        )
        onboarding.write_iterate_md(
            cwd, onboarding.update_completed_at_in_md(md_text, completed_at)
        )
    except OSError as exc:
        _print_flush(f"Refresh failed ({exc}) — rolling back.")
        try:
            config_path.write_text(old_config_text, encoding="utf-8")
            onboarding.write_iterate_md(cwd, md_text)
        except OSError as rollback_error:
            _print_flush(f"Rollback ALSO failed ({rollback_error}) — inspect files manually.")
            return 1
        return 1

    _print_flush(f"Refreshed fingerprints and metadata ({completed_at}).")
    if drift.has_drift:
        _print_flush(
            "Stack drift detected — run `ih iterate reonboard` for a full model re-scan."
        )
    return 0


def run_reonboard(*, yes: bool = False, goal: str = "", no_ai: bool = False) -> int:
    """Backup both artifacts, then re-run the full onboarding preserving the user region."""
    cwd = Path.cwd()
    if not onboarding.is_onboarded(cwd):
        _print_flush("Not onboarded — run `ih iterate onboard` first.")
        return 1

    timestamp = onboarding.utc_now_iso().replace(":", "").replace("-", "")
    md_path = cwd / onboarding.ITERATE_MD_FILENAME
    config_path = init_wizard.existing_config_path(cwd)
    md_backup = md_path.with_name(f"{md_path.name}.bak-{timestamp}")
    config_backup = config_path.with_name(f"{config_path.name}.bak-{timestamp}")
    try:
        shutil.copy2(md_path, md_backup)
        if config_path.exists():
            shutil.copy2(config_path, config_backup)
    except OSError as exc:
        _print_flush(f"Backup failed ({exc}) — aborting reonboard.")
        return 1

    existing_md = onboarding.read_iterate_md(cwd) or ""
    user_section = onboarding.extract_user_owned_section(existing_md)

    _print_flush(f"Backups: {md_backup.name}" + (f", {config_backup.name}" if config_path.exists() else ""))
    exit_code = run_onboard(
        yes=yes,
        goal=goal,
        no_ai=no_ai,
        allow_existing=True,
        preserve_user_section=user_section,
    )
    if exit_code != 0:
        _print_flush("Reonboard failed — restoring backups.")
        try:
            shutil.copy2(md_backup, md_path)
            if config_backup.exists():
                shutil.copy2(config_backup, config_path)
            _print_flush("Backups restored.")
        except OSError as rollback_error:
            _print_flush(
                f"Restore failed ({rollback_error}) — manual recovery needed from {md_backup}."
            )
        return exit_code

    _print_flush(f"Reonboard complete (backups kept: {md_backup.name}).")
    return 0


def render_status_onboarding_lines(cwd: str | Path) -> list[str]:
    """Onboarding block appended by `ih iterate status` / `/iterate status`."""
    root = Path(cwd)
    lines: list[str] = []
    if not onboarding.is_onboarded(root):
        lines.append("Onboarding: not onboarded (`ih iterate onboard`)")
        return lines
    config_path = root / init_wizard.CONFIG_FILENAME
    channel = "?"
    completed = "?"
    fingerprint_count = len(onboarding.load_stored_fingerprints(root))
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raw = None
    if isinstance(raw, dict) and isinstance(raw.get("onboarding"), dict):
        section = raw["onboarding"]
        value = section.get("channel")
        channel = value if isinstance(value, str) else "?"
        value = section.get("completed_at")
        completed = value if isinstance(value, str) else "?"
    lines.append(f"Onboarding: onboarded (channel={channel}, completed_at={completed})")
    lines.append(f"Fingerprints: {fingerprint_count} tracked manifest(s)")
    drift = onboarding.check_onboarding_drift(root)
    if drift is None:
        lines.append("Drift: check disabled or no onboarding data")
    elif drift.has_drift:
        lines.append(f"Drift: DRIFTED — {drift.summary()}")
    else:
        lines.append("Drift: no drift")
    return lines


def warn_if_drifted(cwd: str | Path) -> None:
    """Non-blocking drift warning printed before review/run loops."""
    drift = onboarding.check_onboarding_drift(cwd)
    if drift is not None and drift.has_drift:
        _print_flush(f"[iterate] warning: {drift.summary()}")
        _print_flush(
            "[iterate] run `ih iterate refresh` (re-fingerprint) or "
            "`ih iterate reonboard` (full re-scan) to update the knowledge base."
        )


def ensure_onboarding_fingerprints(cwd: str | Path, *, quiet: bool = False) -> bool:
    """Auto-capture fingerprints when ITERATE.md exists but the config has none.

    Covers the TUI onboarding path: the model writes ITERATE.md, but the
    slash-command flow cannot run the post-scan bookkeeping the CLI performs
    synchronously. The next review/run (or any drift check) lands here and
    completes the ``onboarding`` config section with harness-serialized data —
    the model never touches the trusted config. Returns True when the config
    was written; False when nothing needed doing or the write failed.
    """
    root = Path(cwd)
    if not onboarding.is_onboarded(root):
        return False
    config_path = init_wizard.existing_config_path(root)
    if not config_path.is_file():
        return False
    if onboarding.load_stored_fingerprints(root):
        return False

    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(raw_config, dict):
        return False

    onboarding_section = raw_config.get("onboarding")
    if not isinstance(onboarding_section, dict):
        onboarding_section = {}
    previous_channel = onboarding_section.get("channel")
    onboarding_section.update(
        onboarding.build_onboarding_section(
            channel=previous_channel if isinstance(previous_channel, str) else "ai",
            fingerprints=onboarding.capture_fingerprints(root, onboarding.load_drift_ignore(root)),
        )
    )
    raw_config["onboarding"] = onboarding_section
    try:
        config_path.write_text(
            init_wizard.render_config_text(raw_config), encoding="utf-8"
        )
    except OSError:
        return False
    if not quiet:
        _print_flush(
            "[iterate] recorded onboarding fingerprints into "
            f"{config_path.name} (auto-captured after TUI onboarding)."
        )
    return True


__all__ = [
    "check_auth_configured",
    "ensure_onboarding_fingerprints",
    "render_detection_iterate_md",
    "render_status_onboarding_lines",
    "run_onboard",
    "run_refresh",
    "run_reonboard",
    "warn_if_drifted",
]
