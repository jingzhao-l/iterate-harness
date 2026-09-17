"""Voice mode helpers and diagnostics."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from iterate_harness.api.provider import ProviderInfo
from iterate_harness.config.settings import load_settings, save_settings
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


def toggle_voice_mode(enabled: bool | None = None) -> bool:
    """Set (or flip) the persisted voice-mode setting and return the new state.

    ``enabled`` is the desired state; ``None`` flips the current persisted
    value (toggle semantics, matching ``/voice toggle``/``ctrl+v``). The change
    is written back to the global settings file so it survives the session —
    the same persistence contract as ``/vim`` / ``/fast`` / ``/output-style``.
    """
    settings = load_settings()
    new_state = bool(enabled) if enabled is not None else not bool(settings.voice_mode)
    if bool(settings.voice_mode) != new_state:
        settings.voice_mode = new_state
        save_settings(settings)
    return new_state


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