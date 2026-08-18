"""Streaming STT interface.

This build does NOT ship a bundled local ASR engine (vosk/whisper are MIT
licensed but are optional extras). ``transcribe_stream`` raises a clear
error instead of returning a placeholder string, so callers can never
mistake a fake transcript for a real one. To enable streaming transcription,
install vosk or whisper and wire it up here.
"""

from __future__ import annotations

#: Human-readable reason surfaced when streaming STT is unavailable.
STREAM_STT_UNAVAILABLE_REASON = (
    "Streaming STT is not enabled in this build; install vosk or whisper and "
    "configure it before using streaming transcription."
)

#: Whether a real streaming STT backend is wired into this build.
STREAM_STT_AVAILABLE = False


def stream_stt_available() -> bool:
    """Return True when a real streaming STT backend is configured."""
    return STREAM_STT_AVAILABLE


async def transcribe_stream(data: bytes) -> str:
    """Transcribe one audio chunk locally, or raise when STT is unavailable.

    Args:
        data: Raw PCM/WAV audio bytes to transcribe.

    Raises:
        RuntimeError: always, until a local ASR backend is configured in this
            build. Guards callers against treating the return value as a real
            transcript.
    """
    del data  # No backend wired in; the payload is irrelevant until enabled.
    raise RuntimeError(STREAM_STT_UNAVAILABLE_REASON)


__all__ = [
    "STREAM_STT_AVAILABLE",
    "STREAM_STT_UNAVAILABLE_REASON",
    "stream_stt_available",
    "transcribe_stream",
]