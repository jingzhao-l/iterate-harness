"""Failure-recovery checkpoint for the iterate loop.

The engine persists a compact checkpoint every time a new convergence
aggregate (round boundary) lands — see ``ReviewProgressEvent``. The file
``.iterate/checkpoint.json`` records the last *successful* convergence
point (round, per-dimension counts, tokens, cost) so that after a model /
network failure the run can be resumed from there via ``/iterate resume``
instead of restarting from scratch.

Design rules:

- The checkpoint only ever advances: it is overwritten with newer state,
  never merged or rolled back.
- Writes are atomic (temp file + ``os.replace``) so a crash mid-write can
  never corrupt the previous checkpoint.
- Reads are defensive: a missing / malformed file yields ``None``.
- ``last_state.summarize_last_run`` prefers a final ``report`` entry over
  the checkpoint; the checkpoint is the fallback for interrupted runs.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Subdirectory (mirrors ``decision_log.LOG_DIR``).
CHECKPOINT_DIR = ".iterate"
#: Checkpoint file name.
CHECKPOINT_FILE = "checkpoint.json"


def checkpoint_path(project_root: str | Path) -> Path:
    """Resolve the checkpoint file path, creating the directory if needed."""
    directory = Path(project_root) / CHECKPOINT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / CHECKPOINT_FILE


def save_checkpoint(
    project_root: str | Path,
    *,
    round: int,
    new_findings: int,
    total_findings: int,
    per_dimension: dict[str, int],
    converged: bool,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    mode: str,
) -> Path | None:
    """Persist the latest convergence state atomically.

    Returns the checkpoint file path, or ``None`` when the write
    fails (the loop must not abort on a failed checkpoint)."""
    try:
        target = checkpoint_path(project_root)
    except OSError as exc:
        log.warning("iterate checkpoint dir creation failed: %s", exc)
        return None
    payload = {
        "round": round,
        "new_findings": new_findings,
        "total_findings": total_findings,
        "per_dimension": dict(per_dimension),
        "converged": converged,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "mode": mode,
    }
    temp = target.with_suffix(".json.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, target)
    except OSError as exc:
        log.warning("iterate checkpoint write failed: %s", exc)
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        return None
    return target


def load_checkpoint(project_root: str | Path) -> dict[str, Any] | None:
    """Load the last checkpoint, or ``None`` when missing / malformed."""
    path = Path(project_root) / CHECKPOINT_DIR / CHECKPOINT_FILE
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def clear_checkpoint(project_root: str | Path) -> None:
    """Remove the checkpoint (used when a fresh run starts)."""
    path = Path(project_root) / CHECKPOINT_DIR / CHECKPOINT_FILE
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        log.warning("iterate checkpoint clear failed: %s", exc)


__all__ = [
    "CHECKPOINT_DIR",
    "CHECKPOINT_FILE",
    "checkpoint_path",
    "save_checkpoint",
    "load_checkpoint",
    "clear_checkpoint",
]
