"""Tests for sending an image to a model.

Only the OCR escalation uses this: a PDF page that local OCR could not read
is rendered and handed to a model to transcribe. Every conversational turn
still carries no images at all, and that is the case these tests pin hardest
-- several OpenAI-compatible servers reject the list-of-blocks content shape
for a text-only turn, so sending it unconditionally would break every local
model to support a feature they mostly cannot do.
"""

import json

import httpx
import pytest

from backend.agent.adapters import AgentError, Message, encode_image
from backend.agent.anthropic_api import AnthropicAPIAdapter, _to_anthropic
from backend.agent.gemini_api import to_gemini_contents
from backend.agent.generate import agent_describe_image
from backend.agent.openai_compat import _to_openai

PNG = b"\x89PNG\r\n\x1a\nfake bytes"


def test_a_text_turn_stays_a_plain_string_for_anthropic() -> None:
    assert _to_anthropic(Message("user", "hello")) == {"role": "user", "content": "hello"}


def test_a_text_turn_stays_a_plain_string_for_openai() -> None:
    """Ollama and friends reject the list form for a text-only turn."""
    assert _to_openai(Message("user", "hello")) == {"role": "user", "content": "hello"}


def test_anthropic_puts_the_image_before_the_text() -> None:
    """The API documents that ordering, and it reads correctly too."""
    payload = _to_anthropic(Message("user", "transcribe this", images=(PNG,)))
    kinds = [block["type"] for block in payload["content"]]
    assert kinds == ["image", "text"]
    source = payload["content"][0]["source"]
    assert source == {
        "type": "base64",
        "media_type": "image/png",
        "data": encode_image(PNG),
    }


def test_openai_sends_the_image_as_a_data_url() -> None:
    payload = _to_openai(Message("user", "transcribe this", images=(PNG,)))
    kinds = [block["type"] for block in payload["content"]]
    assert kinds == ["image_url", "text"]
    url = payload["content"][0]["image_url"]["url"]
    assert url == f"data:image/png;base64,{encode_image(PNG)}"


def test_gemini_sends_inline_data_before_the_text() -> None:
    contents = to_gemini_contents([Message("user", "transcribe this", images=(PNG,))])
    parts = contents[0]["parts"]
    assert parts[0] == {
        "inlineData": {"mimeType": "image/png", "data": encode_image(PNG)}
    }
    assert parts[1] == {"text": "transcribe this"}


def test_gemini_text_turns_are_unchanged() -> None:
    contents = to_gemini_contents([Message("user", "hello"), Message("assistant", "hi")])
    assert contents == [
        {"role": "user", "parts": [{"text": "hello"}]},
        {"role": "model", "parts": [{"text": "hi"}]},
    ]


@pytest.mark.anyio
async def test_the_image_reaches_the_provider(anyio_backend) -> None:
    """End to end through a real adapter with a fake transport."""
    sent: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        body = (
            b'event: content_block_delta\n'
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"class Person {}"}}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        return httpx.Response(200, content=body)

    adapter = AnthropicAPIAdapter(
        model="claude-sonnet-5", api_key="sk-test", transport=httpx.MockTransport(handle)
    )
    parts = [
        event
        async for event in adapter.run(
            system_prompt="",
            messages=[Message("user", "transcribe", images=(PNG,))],
            tools=[],
            max_turns=1,
        )
    ]

    assert parts, "the adapter yielded nothing"
    content = sent[0]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["data"] == encode_image(PNG)


@pytest.mark.anyio
async def test_the_subscription_path_says_so_instead_of_failing_later(
    monkeypatch, anyio_backend
) -> None:
    """There is no image path on the Claude Code CLI adapter.

    Discovering that as a provider-side 400 on page 30 of 55 is much worse
    than being told at the start, so the check is here and it names the fix.
    """

    class _SubscriptionAdapter:
        provider = "anthropic"
        model = "claude-opus-4-8"

    monkeypatch.setattr(
        "backend.agent.generate.resolve_adapter", lambda *a, **k: _SubscriptionAdapter()
    )
    with pytest.raises(AgentError, match="cannot take images"):
        await agent_describe_image(PNG, "transcribe", settings=object())
