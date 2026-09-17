"""Tests for voice mode capability inspection.

The UI gate in ``inspect_voice_capabilities`` must disable voice mode and give
an actionable reason when streaming STT is unavailable, so callers downstream
never enable voice and then feed a placeholder transcript into the flow.
"""

from __future__ import annotations

from iterate_harness.api.provider import ProviderInfo
from iterate_harness.voice.stream_stt import STREAM_STT_UNAVAILABLE_REASON
from iterate_harness.voice.voice_mode import (
    VoiceDiagnostics,
    inspect_voice_capabilities,
    toggle_voice_mode,
)


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
            name="ollama",
            auth_kind="api_key",
            voice_supported=False,
            voice_reason="voice mode is not supported for local Ollama",
        )
        diag = inspect_voice_capabilities(provider)
        assert diag.available is False
        assert diag.reason == "voice mode is not supported for local Ollama"


class TestToggleVoiceMode:
    """toggle_voice_mode must persist the change and return the new state
    (previously a dead no-op that just negated its argument without touching
    any state)."""

    def test_sets_explicit_state_and_persists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path / "config"))
        from iterate_harness.config.settings import load_settings

        assert toggle_voice_mode(True) is True
        assert load_settings().voice_mode is True
        assert toggle_voice_mode(False) is False
        assert load_settings().voice_mode is False

    def test_toggle_flips_current_persisted_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path / "config"))
        from iterate_harness.config.settings import load_settings

        assert toggle_voice_mode() is True  # default off -> on
        assert load_settings().voice_mode is True
        assert toggle_voice_mode() is False  # on -> off
        assert load_settings().voice_mode is False

    def test_setting_same_state_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path / "config"))
        toggle_voice_mode(True)
        assert toggle_voice_mode(True) is True