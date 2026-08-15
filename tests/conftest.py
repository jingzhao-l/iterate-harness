"""Shared test fixtures."""

from __future__ import annotations

import pytest_asyncio

from iterate_harness.tasks.manager import shutdown_task_manager


@pytest_asyncio.fixture(autouse=True)
async def _reset_background_task_manager():
    yield
    await shutdown_task_manager()
