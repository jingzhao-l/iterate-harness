"""Configuration routes (design §17.3 P6).

Read-only view of the effective config (with credentials redacted) and the
harness provider settings; mutating write-back with validation, backup, and
rollback.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Body

from ...config.settings import load_settings
from ...iterate.config_loader import (
    CONFIG_FILENAME,
    load_config,
    load_effective_config,
    validate_config,
)
from ..security import AuditLog, REDACTION_PREFIX, redact_mapping
from ..schemas import ConfigView, OperationResult

router = APIRouter(tags=["config"])

log = logging.getLogger(__name__)

#: Backup suffix for the previous config file before a write.
_BACKUP_SUFFIX = ".bak.webui"


def _restore_redacted(
    incoming: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Resolve redaction markers in ``incoming`` against ``existing``.

    ``GET /config`` redacts credential values (``<redacted:...>``, see
    :func:`~iterate_harness.web.security.redact_secret`) before echoing them
    to the editor. When the editor saves the (redacted) draft back, every
    marker is substituted with the current on-disk value so a config save
    never clobbers real credentials with the placeholder. Non-marker values
    (including a brand-new secret the user types) are kept untouched.
    """
    out: dict[str, Any] = {}
    for key, value in incoming.items():
        if isinstance(value, dict):
            prior = existing.get(key)
            out[key] = _restore_redacted(value, prior) if isinstance(prior, dict) else value
        elif isinstance(value, list):
            prior = existing.get(key)
            if isinstance(prior, list):
                out[key] = [
                    _restore_redacted(item, prev)
                    if isinstance(item, dict) and isinstance(prev, dict)
                    else item
                    for item, prev in zip(value, prior)
                ]
            else:
                out[key] = value
        elif isinstance(value, str) and value.startswith(REDACTION_PREFIX):
            prior = existing.get(key)
            out[key] = prior if isinstance(prior, str) else value
        else:
            out[key] = value
    return out


def _resolve_project(project_root: str) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    return root


@router.get("/config", response_model=ConfigView)
def get_config(project_root: str = "") -> ConfigView:
    """Effective config (raw + effective) with credentials redacted, plus provider list."""
    root = _resolve_project(project_root)
    effective = load_effective_config(root)
    raw = load_config(root) or {}
    # Redact the raw config so any credential-like keys are never echoed.
    raw_redacted = redact_mapping(raw)

    # Provider settings from the harness config.
    settings = load_settings()
    profiles = redact_mapping({k: v.model_dump() for k, v in settings.merged_profiles().items()})
    active = settings.resolve_profile()[0] if settings.merged_profiles() else ""

    return ConfigView(
        exists=bool(raw),
        source=effective.source,
        path=str(root / CONFIG_FILENAME),
        raw=raw_redacted,
        effective=redact_mapping(
            {
                "goal": effective.config.goal,
                "maxRounds": effective.config.max_rounds,
                "language": effective.config.language,
                "dimensions": list(effective.config.dimensions),
                "review": {"scope": effective.config.review.scope},
                "atomic": {
                    "maxLines": effective.config.atomic.max_lines,
                    "maxAdjacentMethods": effective.config.atomic.max_adjacent_methods,
                },
                "git": {
                    "targetBranch": effective.config.git.target_branch,
                    "useWorktree": effective.config.git.use_worktree,
                    "pushPerRound": effective.config.git.push_per_round,
                    "autoMerge": effective.config.git.auto_merge,
                },
                "tokenBudget": effective.config.token_budget,
                "budgetUsd": effective.config.budget_usd,
                "maxTurnsPerMinute": effective.config.max_turns_per_minute,
                "worktreeIsolation": effective.config.worktree_isolation,
            }
        ),
        providers=profiles,
        active_profile=active,
    )


@router.put("/config", response_model=OperationResult)
def update_config(
    config: dict[str, Any] = Body(..., description="New config content"),
    project_root: str = "",
    confirm: bool = False,
) -> OperationResult:
    """Validate and write ``iterate.config.yaml`` (mutating, audited).

    Before writing, the current file is backed up to ``<path>.bak.webui``.
    If the write fails, the backup is restored. Requires ``confirm=true``.
    """
    root = _resolve_project(project_root)
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="config update requires confirm=true (secondary confirmation)",
        )

    # Validate the submitted config.
    errors = validate_config(config)
    if errors:
        raise HTTPException(
            status_code=422,
            detail=f"Config validation failed: {', '.join(errors)}",
        )

    # The editor edits the redacted view; resolve every redaction marker back
    # to the current on-disk value before writing so secrets survive a save.
    existing = load_config(root) or {}
    resolved = _restore_redacted(config, existing)

    import yaml  # type: ignore[import-untyped]  # pyyaml ships no stubs

    config_path = root / CONFIG_FILENAME

    # Backup the existing config.
    if config_path.exists():
        backup_path = config_path.with_suffix(config_path.suffix + _BACKUP_SUFFIX)
        try:
            shutil.copy2(config_path, backup_path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Backup failed: {exc}") from exc

    # Write the new config.
    try:
        yaml_content = yaml.dump(resolved, default_flow_style=False, allow_unicode=True, sort_keys=False)
        config_path.write_text(yaml_content, encoding="utf-8")
    except (OSError, yaml.YAMLError) as exc:
        # Restore from backup. If the rollback itself fails, surface that too
        # instead of silently leaving the file in a half-written state.
        backup_path = config_path.with_suffix(config_path.suffix + _BACKUP_SUFFIX)
        rollback_error: OSError | None = None
        if config_path.exists() and backup_path.exists():
            try:
                shutil.copy2(backup_path, config_path)
            except OSError as rollback_exc:
                rollback_error = rollback_exc
                log.error("config rollback failed for %s: %s", config_path, rollback_exc)
        detail = f"Config write failed: {exc}"
        if rollback_error is not None:
            detail += f"; rollback also failed: {rollback_error}"
        raise HTTPException(status_code=500, detail=detail) from exc

    AuditLog(root).record(
        "config.update",
        str(config_path),
        summary={"keys": list(config.keys())},
    )
    return OperationResult(
        status="ok",
        message=f"Config written to {config_path.name}",
        target=str(config_path),
        detail={"keys": list(config.keys())},
    )


@router.get("/config/providers", response_model=dict[str, Any])
def get_providers(project_root: str = "") -> dict[str, Any]:
    """Provider profiles with credentials redacted for the provider management view."""
    settings = load_settings()
    profiles = settings.merged_profiles()
    active_profile_name, active_profile = settings.resolve_profile()
    return {
        "active": active_profile_name,
        "profiles": {
            name: redact_mapping(profile.model_dump())
            for name, profile in profiles.items()
        },
    }