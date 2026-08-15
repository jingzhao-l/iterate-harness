"""Pre-commit git hook: fast changed-only review before every commit.

Design §11.2.1 "跑类" last gap — "git hook：commit 前 30 秒 changed-only 快审".
``oh iterate hook install`` writes a managed ``.git/hooks/pre-commit`` that
runs ONE dry-run review round over the pending delta and gates the commit on
the report's severity (``--fail-on``), reusing the exact CI exit-code policy.

Safety semantics:
- the hook is MARKED (``# iterate-harness pre-commit hook``) — installing
  over an existing unmarked hook is refused so user/third-party hooks are
  never silently destroyed;
- ``ITERATE_SKIP_HOOK=1`` (or ``git commit --no-verify``) skips the review;
- the ``oh`` binary is resolved to an absolute path at install time because
  hook environments often lack the user's PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

HOOK_MARKER = "# iterate-harness pre-commit hook"
HOOK_FILENAME = "pre-commit"

#: Default severity gate for the commit (same vocabulary as `iterate report`).
DEFAULT_FAIL_ON = "high"

#: Review round cap for the hook — a single round keeps commits fast.
HOOK_ROUNDS = 1

_SKIP_GUARD = f'''{HOOK_MARKER}
# Managed by `oh iterate hook`. Re-run that command to update; remove with
# `oh iterate hook uninstall`. Skip once with ITERATE_SKIP_HOOK=1 or
# `git commit --no-verify`.
if [ "$ITERATE_SKIP_HOOK" = "1" ]; then
  echo "iterate: hook skipped (ITERATE_SKIP_HOOK=1)"
  exit 0
fi
'''


class HookError(Exception):
    """Raised for refused/dangerous hook operations (never silent)."""


def _git_root(cwd: str | Path) -> Path:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        raise HookError(f"not a git repo (or git failed): {cwd}") from exc
    if not output:
        raise HookError(f"not a git repo: {cwd}")
    return Path(output)


def hook_path(cwd: str | Path) -> Path:
    return _git_root(cwd) / ".git" / "hooks" / HOOK_FILENAME


def _resolve_oh_binary() -> str:
    found = shutil.which("oh")
    return found or "oh"


def render_hook_script(*, oh_binary: str, fail_on: str) -> str:
    """Render the pre-commit script (deterministic; used by install + tests)."""
    return (
        _SKIP_GUARD
        + f'''OH="{oh_binary}"
echo "iterate: pre-commit changed-only review (rounds={HOOK_ROUNDS}, fail-on={fail_on})…"
"$OH" iterate review --changed --clean-ok --ref HEAD --rounds {HOOK_ROUNDS} || exit 1
exec "$OH" iterate report --fail-on {fail_on}
'''
    )


def install_hook(cwd: str | Path, *, fail_on: str = DEFAULT_FAIL_ON) -> Path:
    """Install (or replace a managed) pre-commit hook; returns its path."""
    if fail_on not in ("none", "low", "medium", "high", "critical"):
        raise HookError(f"fail_on must be none|low|medium|high|critical (got {fail_on!r})")
    target = hook_path(cwd)
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise HookError(f"cannot read existing hook {target}: {exc}") from exc
        if HOOK_MARKER not in existing:
            raise HookError(
                f"{target} already exists and is not managed by iterate — "
                "refusing to overwrite (merge it manually or remove it first)"
            )
    script = render_hook_script(oh_binary=_resolve_oh_binary(), fail_on=fail_on)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script, encoding="utf-8")
    target.chmod(0o755)
    return target


def uninstall_hook(cwd: str | Path) -> bool:
    """Remove a managed hook; False when absent. Refuses foreign hooks."""
    target = hook_path(cwd)
    if not target.exists():
        return False
    existing = target.read_text(encoding="utf-8", errors="replace")
    if HOOK_MARKER not in existing:
        raise HookError(f"{target} is not managed by iterate — remove it manually")
    target.unlink()
    return True


def hook_status(cwd: str | Path) -> dict[str, Any]:
    """Describe the hook state for `oh iterate hook status`."""
    try:
        target = hook_path(cwd)
    except HookError as exc:
        return {"installed": False, "error": str(exc)}
    if not target.exists():
        return {"installed": False, "path": str(target)}
    existing = target.read_text(encoding="utf-8", errors="replace")
    return {
        "installed": HOOK_MARKER in existing,
        "path": str(target),
        "managed": HOOK_MARKER in existing,
        "skippable": "ITERATE_SKIP_HOOK=1 or git commit --no-verify",
    }


__all__ = [
    "DEFAULT_FAIL_ON",
    "HOOK_FILENAME",
    "HOOK_MARKER",
    "HOOK_ROUNDS",
    "HookError",
    "hook_path",
    "hook_status",
    "install_hook",
    "render_hook_script",
    "uninstall_hook",
]
