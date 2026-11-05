"""Capability inventory route (design §17.3 P12).

The engine already loaded tools, slash commands, skills, plugins and MCP server
configs, but a browser operator could not see any of them: a missing MCP
executable only became visible as a failed call inside a run. This route returns
the same read-only inventory as ``ih capabilities`` — one shared loader, one
set of static MCP checks, no network and no writes.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..roots import resolve_project_root
from ..schemas import CapabilitiesView

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesView)
def get_capabilities(request: Request, project_root: str = "") -> CapabilitiesView:
    """List loaded capabilities and static configuration problems.

    ``project_root`` follows the same allow-list as every other route. Loader
    failures are reported inside ``problems`` (with a remedy) rather than
    turning the whole page into a 500, because explaining a broken plugin is
    the reason this route exists.
    """
    from ...capabilities import build_capability_inventory

    root = resolve_project_root(request, project_root)
    return CapabilitiesView(**build_capability_inventory(root))


__all__ = ["router"]
