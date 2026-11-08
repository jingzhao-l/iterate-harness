"""Project memory routes (design §17.3 P14).

The engine injects the project memory bank into every runtime prompt, but the
operator could only see it from the REPL's ``/memory`` slash command. This
module closes that gap with a read-only inventory (plus a "what would the model
be told for this task?" injection preview), and with the two write operations
an operator reasonably wants from a console: add/overwrite a memory file and
delete one. Both write paths require ``confirm=true`` and are audited, and both
refuse to overwrite without an explicit flag.

The memory directory lives under the harness data dir (``get_data_dir()``), not
under the project, so unlike ``project_root``-bound routes this one is
machine-global — but the memory dir is keyed by the project path, and the
project root still has to pass the console's root allow-list before any memory
is read or written for it.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query, Request

from ...memory import (
    add_memory_entry,
    build_memory_inventory,
    list_memory_files,
    prune_memory_index,
    read_memory_entry,
    remove_memory_entry,
)
from ..roots import resolve_project_root
from ..schemas import MemoryCreateRequest, MemoryView
from ..security import AuditLog

router = APIRouter(tags=["memory"])

#: Longest memory file name accepted by the write routes.
MAX_NAME = 128


@router.get("/memory", response_model=MemoryView)
def get_memory(
    request: Request,
    project_root: str = "",
    query: str = "",
    prompt: str = "",
    limit: int = 500,
) -> MemoryView:
    """Read the project memory bank.

    ``query`` ranks ``matches`` with the engine's relevance scorer; ``prompt``
    additionally returns the exact memory section the runtime prompt would
    inject for that task. Read-only; never creates the memory directory.
    """
    root = resolve_project_root(request, project_root)
    return MemoryView(
        **build_memory_inventory(
            root,
            query=query,
            prompt=prompt,
            max_entries=max(limit, 50),
        )
    )


@router.get("/memory/entry")
def get_memory_entry(
    request: Request,
    name: str = Query(..., min_length=1, max_length=MAX_NAME),
    project_root: str = "",
) -> dict[str, object]:
    """Read one memory file's full content (for the console's detail pane).

    Names that escape the memory directory are rejected with 422; a missing
    file answers 404. ``MEMORY.md`` is readable — it is the index.
    """
    root = resolve_project_root(request, project_root)
    entry = read_memory_entry(root, name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"memory not found: {name}")
    return entry


@router.post("/memory")
def create_memory(
    body: MemoryCreateRequest,
    request: Request,
    confirm: bool = False,
) -> dict[str, object]:
    """Create a memory file, audited.

    Refuses to overwrite an existing file unless ``body.overwrite`` is true
    (409 otherwise) — a stray save must not silently replace remembered
    context.
    """
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="memory create requires confirm=true (secondary confirmation)",
        )
    root = resolve_project_root(request, "")
    slug = body.title.strip().lower()
    # Title is slugged server-side; compare against actual filenames so the
    # conflict test matches what add_memory_entry would produce.
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_") or "memory"
    existing = [p for p in list_memory_files(root) if p.stem == slug]
    if existing and not body.overwrite:
        raise HTTPException(
            status_code=409,
            detail=(
                f"memory '{slug}.md' already exists; pass overwrite=true to "
                "replace it (this cannot be undone)"
            ),
        )
    try:
        path = add_memory_entry(root, body.title, body.content, overwrite=body.overwrite)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"cannot write memory: {exc}") from exc

    AuditLog(root).record(
        "memory.add",
        path.name,
        summary={
            "title": body.title,
            "bytes": len(body.content.encode("utf-8")),
            "overwrite": body.overwrite,
        },
    )
    return {
        "status": "ok",
        "message": f"已保存记忆 {path.name}",
        "target": path.name,
        "detail": {"path": str(path), "name": path.name},
    }


@router.delete("/memory")
def delete_memory(
    request: Request,
    name: str = Query(..., min_length=1, max_length=MAX_NAME),
    project_root: str = "",
    prune_index: bool = False,
    confirm: bool = False,
) -> dict[str, object]:
    """Delete a memory file and unlink it from MEMORY.md, audited."""
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="memory remove requires confirm=true (secondary confirmation)",
        )
    root = resolve_project_root(request, project_root)
    removed = remove_memory_entry(root, name)
    if removed:
        AuditLog(root).record("memory.remove", name, summary={"prune_index": prune_index})
        return {
            "status": "ok",
            "message": f"已删除记忆 {name}",
            "target": name,
        }
    if prune_index:
        pruned = prune_memory_index(root, name)
        if pruned:
            AuditLog(root).record(
                "memory.prune-index", name, summary={"reason": "dead index link"}
            )
            return {"status": "ok", "message": f"已移除失效索引链接 {name}", "target": name}
    raise HTTPException(status_code=404, detail=f"memory not found: {name}")


__all__ = ["router"]
