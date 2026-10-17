"""Project-root resolution shared by the WebUI routes.

``ih web serve --project /x`` pins the allowed roots to ``/x`` but does not
change the server's working directory. A request that omits ``project_root``
must therefore fall back to the launch project (``app.state.project_root``) —
NOT to the server's CWD. Without that fallback every non-chat route returned
403 when the console was launched with ``--project`` from outside that
directory, and the frontend misreported the 403 as "your token expired".

The explicit query parameter stays first (multi-project consoles select the
root from the sidebar), the launch project is the default, and the server CWD
is only the last resort for an unpinned server.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from .security import allowed_roots, root_is_allowed


def resolve_project_root(request: Request, project_root: str | None = None) -> Path:
    """Resolve the project root: explicit arg > launch project > CWD."""
    resolved = (project_root or "").strip()
    if not resolved:
        state_root = getattr(request.app.state, "project_root", None)
        if state_root:
            resolved = str(state_root)
    root = Path(resolved) if resolved else Path.cwd()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project root not found: {root}")
    # ``project_root`` is caller-controlled, so containment inside the root is
    # not enough on its own: without this the parameter *selects* the root, and
    # a valid token granted read/write over any directory on the machine.
    if not root_is_allowed(root):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Project root is outside the roots this WebUI serves: {root}. "
                f"Allowed: {', '.join(sorted(allowed_roots())) or '(none)'}"
            ),
        )
    return root.resolve()
