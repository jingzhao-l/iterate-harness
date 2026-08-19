from __future__ import annotations

from iterate_harness.engine.messages import ConversationMessage, ImageBlock, TextBlock
from iterate_harness.api.client import AnthropicApiClient


def test_anthropic_client_uses_api_key(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("iterate_harness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)

    AnthropicApiClient(api_key="api-key")

    assert captured["api_key"] == "api-key"
    assert "default_headers" not in captured
    assert "auth_token" not in captured


def test_anthropic_client_passes_base_url(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("iterate_harness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)

    AnthropicApiClient(api_key="api-key", base_url="https://proxy.example/v1")

    assert captured["api_key"] == "api-key"
    assert captured["base_url"] == "https://proxy.example/v1"


def test_anthropic_client_without_credentials_creates_plain_client(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("iterate_harness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)

    AnthropicApiClient()

    assert captured == {}


def test_conversation_message_serializes_image_block_for_anthropic():
    message = ConversationMessage(
        role="user",
        content=[
            TextBlock(text="Describe this."),
            ImageBlock(media_type="image/png", data="YWJj", source_path="/tmp/example.png"),
        ],
    )

    assert message.to_api_param() == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this."},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "YWJj",
                },
            },
        ],
    }
