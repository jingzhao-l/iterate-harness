"""Tests for voice mode capability inspection.

The UI gate in ``inspect_voice_capabilities`` must disable voice mode and give
an actionable reason when streaming STT is unavailable, so callers downstream
never enable voice and then feed a placeholder transcript into the flow.
"""

from __future__ import annotations

from iterate_harness.api.provider import ProviderInfo
from iterate_harness.voice.stream_stt import STREAM_STT_UNAVAILABLE_REASON
from iterate_harness.voice.voice_mode import VoiceDiagnostics, inspect_voice_capabilities


class TestInspectVoiceCapabilities:
    def test_disables_voice_when_stream_stt_unavailable(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/sox")
        provider = ProviderInfo(
            name="anthropic",
            auth_kind="api_key",
            voice_supported=True,
            voice_reason="ok",
        )
        diag = inspect_voice_capabilities(provider)
        assert isinstance(diag, VoiceDiagnostics)
        assert diag.available is False
        assert diag.reason == STREAM_STT_UNAVAILABLE_REASON

    def test_keeps_provider_reason_when_provider_blocks_voice(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/sox")
        provider = ProviderInfo(
            name="copilot",
            auth_kind="oauth_device",
            voice_supported=False,
            voice_reason="voice mode is not supported for GitHub Copilot",
        )
        diag = inspect_voice_capabilities(provider)
        assert diag.available is False
        assert diag.reason == "voice mode is not supported for GitHub Copilot"