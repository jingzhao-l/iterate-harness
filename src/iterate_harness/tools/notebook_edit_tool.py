"""Minimal Jupyter notebook editing tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from iterate_harness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from iterate_harness.utils.fs import atomic_write_text, is_regular_file


class NotebookEditToolInput(BaseModel):
    """Arguments for notebook editing."""

    path: str = Field(description="Path to the .ipynb file")
    cell_index: int = Field(description="Zero-based cell index", ge=0)
    new_source: str = Field(description="Replacement or appended source for the target cell")
    cell_type: Literal["code", "markdown"] = Field(default="code")
    mode: Literal["replace", "append"] = Field(default="replace")
    create_if_missing: bool = Field(default=True)


class NotebookEditTool(BaseTool[NotebookEditToolInput]):
    """Edit notebook cells without requiring nbformat."""

    name = "notebook_edit"
    description = "Create or edit a Jupyter notebook cell."
    input_model = NotebookEditToolInput

    async def execute(
        self,
        arguments: NotebookEditToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = _resolve_path(context.cwd, arguments.path)

        from iterate_harness.sandbox.session import is_docker_sandbox_active

        if is_docker_sandbox_active():
            from iterate_harness.sandbox.path_validator import validate_sandbox_path

            allowed, reason = validate_sandbox_path(path, context.cwd)
            if not allowed:
                return ToolResult(output=f"Sandbox: {reason}", is_error=True)

        try:
            notebook = await asyncio.to_thread(
                _load_notebook, path, create_if_missing=arguments.create_if_missing
            )
        except ValueError as exc:
            return ToolResult(output=f"Cannot edit notebook {path}: {exc}", is_error=True)
        if notebook is None:
            return ToolResult(output=f"Notebook not found: {path}", is_error=True)

        cells = notebook.setdefault("cells", [])
        while len(cells) <= arguments.cell_index:
            cells.append(_empty_cell(arguments.cell_type))

        cell = cells[arguments.cell_index]
        cell["cell_type"] = arguments.cell_type
        cell.setdefault("metadata", {})
        if arguments.cell_type == "code":
            cell.setdefault("outputs", [])
            cell.setdefault("execution_count", None)

        existing = _normalize_source(cell.get("source", ""))
        updated = arguments.new_source if arguments.mode == "replace" else f"{existing}{arguments.new_source}"
        cell["source"] = updated

        path.parent.mkdir(parents=True, exist_ok=True)
        # Off the event loop: a notebook on a network/FUSE mount can block for
        # seconds, and a blocking write inside ``async def execute`` freezes
        # every concurrent agent, stream, and hook timer in the session.
        await asyncio.to_thread(
            atomic_write_text, path, json.dumps(notebook, indent=2) + "\n", encoding="utf-8"
        )
        return ToolResult(output=f"Updated notebook cell {arguments.cell_index} in {path}")


def _resolve_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _load_notebook(path: Path, *, create_if_missing: bool) -> dict[str, Any] | None:
    if path.exists():
        # ``is_regular_file`` first: a FIFO/device/directory at this path
        # would make ``read_text`` block forever (or raise IsADirectoryError)
        # inside a coroutine, where no timeout can cancel it.
        if not is_regular_file(path):
            raise ValueError(
                f"not a regular file ({path}); a notebook must be an .ipynb file on disk"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # A malformed notebook file must surface as a clean error response,
            # never as an unhandled exception that aborts the query.
            raise ValueError(f"not a valid .ipynb JSON file: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"not a valid .ipynb file: the JSON root must be an object, got "
                f"{type(payload).__name__}"
            )
        cells = payload.get("cells")
        if not isinstance(cells, list):
            if cells is None:
                payload["cells"] = []
            else:
                raise ValueError(
                    f"not a valid .ipynb file: 'cells' must be a list, got "
                    f"{type(cells).__name__}"
                )
        return cast("dict[str, Any]", payload)
    if not create_if_missing:
        return None
    return {
        "cells": [],
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _empty_cell(cell_type: str) -> dict[str, Any]:
    if cell_type == "markdown":
        return {"cell_type": "markdown", "metadata": {}, "source": ""}
    return {
        "cell_type": "code",
        "metadata": {},
        "source": "",
        "outputs": [],
        "execution_count": None,
    }


def _normalize_source(source: str | list[str]) -> str:
    if isinstance(source, list):
        return "".join(source)
    return str(source)
