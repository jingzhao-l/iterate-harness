"""Tests for the OpenAI-compatible client's ``reasoning_effort`` passthrough.

Covers the full chain: ``ApiMessageRequest.reasoning_effort`` -> request body.
Uses a fake streaming response so no network is ever touched.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from iterate_harness.api.client import ApiMessageRequest
from iterate_harness.api.openai_client import OpenAICompatibleClient
from iterate_harness.engine.messages import ConversationMessage


class _FakeAsyncCompletions:
    """Minimal fake for ``AsyncOpenAI.chat.completions``."""

    def __init__(self) -> None:
        self.last_params: dict[str, Any] = {}

    async def create(self, **params: Any) -> AsyncIterator[object]:
        # Return an async stream (the real client ``await``s create, then
        # iterates the returned stream). One empty choice chunk plus a
        # usage-only chunk terminate the parser normally.
        self.last_params = params

        async def _stream() -> AsyncIterator[object]:
            class _Choice:
                class _Delta:
                    content: str | None = None
                    reasoning_content: str | None = None
                    tool_calls: list[Any] | None = None

                delta = _Delta()
                finish_reason: str | None = None
                index = 0

            class _Chunk:
                choices = [_Choice()]
                usage = None

            yield _Chunk()

            class _UsageChunk:
                choices = []

                class _Usage:
                    prompt_tokens = 10
                    completion_tokens = 5

                usage = _Usage()

            yield _UsageChunk()

        return _stream()


def _make_client(reasoning_effort: str | None) -> tuple[OpenAICompatibleClient, _FakeAsyncCompletions]:
    client = OpenAICompatibleClient(api_key="test-key", base_url="https://example.test/v1")
    fake = _FakeAsyncCompletions()
    client._client = type("FakeOpenAI", (), {"chat": type("Chat", (), {"completions": fake})()})()
    return client, fake


@pytest.mark.asyncio
async def test_reasoning_effort_forwarded_when_configured() -> None:
    client, fake = _make_client(reasoning_effort=None)
    request = ApiMessageRequest(
        model="deepseek-reasoner",
        messages=[ConversationMessage.from_user_text("review this")],
        max_tokens=1024,
        reasoning_effort="low",
    )
    events = [event async for event in client.stream_message(request)]
    assert events  # streaming completed
    assert fake.last_params["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_reasoning_effort_omitted_when_not_configured() -> None:
    client, fake = _make_client(reasoning_effort=None)
    request = ApiMessageRequest(
        model="deepseek-reasoner",
        messages=[ConversationMessage.from_user_text("review this")],
        max_tokens=1024,
        reasoning_effort=None,
    )
    _ = [event async for event in client.stream_message(request)]
    assert "reasoning_effort" not in fake.last_params
