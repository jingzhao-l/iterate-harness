"""Voice mode helpers and diagnostics."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from iterate_harness.api.provider import ProviderInfo
from iterate_harness.voice.stream_stt import (
    STREAM_STT_UNAVAILABLE_REASON,
    stream_stt_available,
)


@dataclass(frozen=True)
class VoiceDiagnostics:
    """Basic voice mode capability summary."""

    available: bool
    reason: str
    recorder: str | None = None


def toggle_voice_mode(enabled: bool) -> bool:
    """Toggle voice mode state."""
    return not enabled


def inspect_voice_capabilities(provider: ProviderInfo) -> VoiceDiagnostics:
    """Return a coarse voice capability summary for the current environment."""
    recorder = shutil.which("sox") or shutil.which("ffmpeg") or shutil.which("arecord")
    if not provider.voice_supported:
        return VoiceDiagnostics(
            available=False,
            reason=provider.voice_reason,
            recorder=recorder,
        )
    if not stream_stt_available():
        return VoiceDiagnostics(
            available=False,
            reason=STREAM_STT_UNAVAILABLE_REASON,
            recorder=recorder,
        )
    if recorder is None:
        return VoiceDiagnostics(
            available=False,
            reason="no supported recorder found (expected sox, ffmpeg, or arecord)",
        )
    return VoiceDiagnostics(
        available=True,
        reason="voice shell is available",
        recorder=recorder,
    )

