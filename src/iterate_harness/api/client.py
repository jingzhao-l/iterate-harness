"""Anthropic API client wrapper with retry logic."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

from anthropic import APIError, APIStatusError, AsyncAnthropic

from iterate_harness.api.errors import (
    AuthenticationFailure,
    IterateHarnessApiError,
    RateLimitFailure,
    RequestFailure,
)
from iterate_harness.api.usage import UsageSnapshot
from iterate_harness.engine.messages import ConversationMessage, assistant_message_from_api

log = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 30.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 529}


@dataclass(frozen=True)
class ApiMessageRequest:
    """Input parameters for a model invocation."""

    model: str
    messages: list[ConversationMessage]
    system_prompt: str | None = None
    max_tokens: int = 4096
    tools: list[dict[str, Any]] = field(default_factory=list)
    # LLM reasoning effort ('low' | 'medium' | 'high'); None = provider default.
    # Only forwarded to providers that accept the OpenAI ``reasoning_effort``
    # parameter (DeepSeek reasoning models etc.). Ignored otherwise.
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ApiTextDeltaEvent:
    """Incremental text produced by the model."""

    text: str


@dataclass(frozen=True)
class ApiMessageCompleteEvent:
    """Terminal event containing the full assistant message."""

    message: ConversationMessage
    usage: UsageSnapshot
    stop_reason: str | None = None


@dataclass(frozen=True)
class ApiRetryEvent:
    """A recoverable upstream failure that will be retried automatically."""

    message: str
    attempt: int
    max_attempts: int
    delay_seconds: float


ApiStreamEvent = ApiTextDeltaEvent | ApiMessageCompleteEvent | ApiRetryEvent


class SupportsStreamingMessages(Protocol):
    """Protocol used by the query engine in tests and production.

    Declared as a plain generator-returning method (not ``async def``): the
    implementations are async generators, whose call result is an
    ``AsyncIterator`` rather than a coroutine.
    """

    def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Yield streamed events for the request."""


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is retryable."""
    if isinstance(exc, APIStatusError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, APIError):
        return True  # Network errors are retryable
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return False


def _retry_after_seconds(exc: APIStatusError) -> float | None:
    """Extract a usable ``Retry-After`` value (seconds form) from an API error."""
    sources: list[object] = [getattr(exc, "headers", None)]
    response = getattr(exc, "response", None)
    if response is not None:
        sources.append(getattr(response, "headers", None))
    for headers in sources:
        if headers is None:
            continue
        getter = getattr(headers, "get", None)
        if not callable(getter):
            continue
        try:
            raw = getter("retry-after")
        except (AttributeError, TypeError):
            continue
        if raw is None:
            continue
        try:
            return float(raw)
        except (ValueError, TypeError) as invalid_exc:
            log.debug("Ignoring invalid retry-after header %r: %s", raw, invalid_exc)
    return None


def _get_retry_delay(attempt: int, exc: Exception | None = None) -> float:
    """Calculate delay with exponential backoff and jitter."""
    import random

    # Check for Retry-After header. ``APIStatusError.headers`` is the httpx
    # response headers object, not a dict, and it is case-insensitive —
    # reading a dict out of it silently never found anything, so a provider
    # asking for a 30s backoff got the 1s default and burned its retry budget.
    if isinstance(exc, APIStatusError):
        val = _retry_after_seconds(exc)
        if val is not None:
            return min(val, MAX_DELAY)

    delay: float = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
    jitter: float = random.uniform(0.0, delay * 0.25)
    return delay + jitter


class AnthropicApiClient:
    """Thin wrapper around the Anthropic async SDK with retry logic."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._client = self._create_client()

    def _create_client(self) -> AsyncAnthropic:
        kwargs: dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return AsyncAnthropic(**kwargs)

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Yield text deltas and the final assistant message with retry on transient errors."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            emitted = False
            try:
                async for event in self._stream_once(request):
                    emitted = True
                    yield event
                return  # Success
            except IterateHarnessApiError:
                raise  # Auth errors are not retried
            except Exception as exc:
                last_error = exc
                if emitted:
                    # The attempt already streamed events to the caller.
                    # Replaying the request would duplicate the visible output
                    # and, worse, orphan the tool_use blocks already in the
                    # transcript (their tool_result never arrives) — the next
                    # request is then rejected with a 400. Surface the failure
                    # instead of corrupting the conversation.
                    log.error(
                        "API stream failed after %d event(s) were emitted; not retrying: %s",
                        1,
                        exc,
                    )
                    if isinstance(exc, APIError):
                        raise _translate_api_error(exc) from exc
                    raise RequestFailure(str(exc)) from exc
                if attempt >= MAX_RETRIES or not _is_retryable(exc):
                    if isinstance(exc, APIError):
                        raise _translate_api_error(exc) from exc
                    raise RequestFailure(str(exc)) from exc

                delay = _get_retry_delay(attempt, exc)
                status = getattr(exc, "status_code", "?")
                log.warning(
                    "API request failed (attempt %d/%d, status=%s), retrying in %.1fs: %s",
                    attempt + 1, MAX_RETRIES + 1, status, delay, exc,
                )
                yield ApiRetryEvent(
                    message=str(exc),
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES + 1,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)

        if last_error is not None:
            if isinstance(last_error, APIError):
                raise _translate_api_error(last_error) from last_error
            raise RequestFailure(str(last_error)) from last_error

    async def _stream_once(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Single attempt at streaming a message."""
        params: dict[str, Any] = {
            "model": request.model,
            "messages": [message.to_api_param() for message in request.messages],
            "max_tokens": request.max_tokens,
        }
        if request.system_prompt:
            params["system"] = request.system_prompt
        if request.tools:
            params["tools"] = request.tools

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if getattr(event, "type", None) != "content_block_delta":
                        continue
                    delta = getattr(event, "delta", None)
                    if getattr(delta, "type", None) != "text_delta":
                        continue
                    text = getattr(delta, "text", "")
                    if text:
                        yield ApiTextDeltaEvent(text=text)

                final_message = await stream.get_final_message()
        except APIError as exc:
            if isinstance(exc, APIStatusError) and exc.status_code in RETRYABLE_STATUS_CODES:
                raise  # Let retry logic handle it
            raise _translate_api_error(exc) from exc

        usage = getattr(final_message, "usage", None)
        yield ApiMessageCompleteEvent(
            message=assistant_message_from_api(final_message),
            usage=UsageSnapshot(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            ),
            stop_reason=getattr(final_message, "stop_reason", None),
        )


def _translate_api_error(exc: APIError) -> IterateHarnessApiError:
    name = exc.__class__.__name__
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return AuthenticationFailure(str(exc))
    if name == "RateLimitError":
        return RateLimitFailure(str(exc))
    return RequestFailure(str(exc))
