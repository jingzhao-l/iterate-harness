"""Regression tests: notebook_edit must bound cell source growth."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from iterate_harness.tools.base import ToolExecutionContext
from iterate_harness.tools.notebook_edit_tool import (
    NotebookEditTool,
    NotebookEditToolInput,
    _MAX_CELL_SOURCE_CHARS,
    _TRUNCATION_MARKER,
)


def _write_notebook(path: Path, source: str) -> None:
    path.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "source": source,
                        "outputs": [],
                        "execution_count": None,
                    }
                ],
                "metadata": {"language_info": {"name": "python"}},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_notebook_edit_appended_source_is_capped_with_marker(tmp_path: Path):
    """Repeated appends must not grow a cell without bound."""
    path = tmp_path / "demo.ipynb"
    initial = "a" * (_MAX_CELL_SOURCE_CHARS - 10)
    _write_notebook(path, initial)

    result = await NotebookEditTool().execute(
        NotebookEditToolInput(
            path="demo.ipynb",
            cell_index=0,
            new_source="b" * 100,
            mode="append",
        ),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False

    stored = json.loads(path.read_text(encoding="utf-8"))["cells"][0]["source"]
    assert len(stored) == _MAX_CELL_SOURCE_CHARS
    assert stored.endswith(_TRUNCATION_MARKER)


@pytest.mark.asyncio
async def test_notebook_edit_rejects_oversized_new_source(tmp_path: Path):
    with pytest.raises(ValidationError):
        NotebookEditToolInput(
            path="demo.ipynb",
            cell_index=0,
            new_source="a" * (_MAX_CELL_SOURCE_CHARS + 1),
        )


@pytest.mark.asyncio
async def test_notebook_edit_within_cap_is_untouched(tmp_path: Path):
    path = tmp_path / "demo.ipynb"
    _write_notebook(path, "print('hi')\n")

    result = await NotebookEditTool().execute(
        NotebookEditToolInput(
            path="demo.ipynb",
            cell_index=0,
            new_source="print('bye')\n",
            mode="replace",
        ),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False

    stored = json.loads(path.read_text(encoding="utf-8"))["cells"][0]["source"]
    assert stored == "print('bye')\n"
