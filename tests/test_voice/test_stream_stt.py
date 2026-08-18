"""Tests for the streaming STT interface.

Streaming STT is intentionally unavailable in this build: ``transcribe_stream``
must raise a clear error rather than return a placeholder string a caller could
mistake for a real transcript.
"""

from __future__ import annotations

import pytest

from iterate_harness.voice.stream_stt import (
    STREAM_STT_AVAILABLE,
    STREAM_STT_UNAVAILABLE_REASON,
    stream_stt_available,
    transcribe_stream,
)


class TestTranscribeStream:
    def test_raises_not_returns_placeholder(self):
        with pytest.raises(RuntimeError) as exc_info:
            # Kick any sync harness; the coroutine body runs here.
            from asyncio import run

            run(transcribe_stream(b"\x00" * 16))
        message = str(exc_info.value)
        assert "not enabled" in message
        assert "vosk" in message or "whisper" in message

    def test_raises_matches_public_reason_constant(self):
        from asyncio import run

        with pytest.raises(RuntimeError) as exc_info:
            run(transcribe_stream(b"\x00"))
        assert str(exc_info.value) == STREAM_STT_UNAVAILABLE_REASON

    def test_reason_is_actionable(self):
        assert "install vosk or whisper" in STREAM_STT_UNAVAILABLE_REASON


class TestStreamSttAvailability:
    def test_reports_unavailable_in_this_build(self):
        assert stream_stt_available() is False

    def test_flag_is_false(self):
        assert STREAM_STT_AVAILABLE is False