"""Unit tests for the Anthropic Claude summarizer wrapper."""

from __future__ import annotations

import pytest

from panakoes_summarization.summarizer import (
    MODEL_FOR_TIER,
    SYSTEM_PROMPT,
    Summarizer,
)
from tests.conftest import FakeAnthropicClient


@pytest.mark.unit
def test_haiku_tier_maps_to_haiku_model() -> None:
    """`tier="haiku"` resolves to `claude-haiku-4-5` per CLAUDE.md."""
    assert MODEL_FOR_TIER["haiku"] == "claude-haiku-4-5"


@pytest.mark.unit
def test_sonnet_tier_maps_to_sonnet_model() -> None:
    """`tier="sonnet"` resolves to `claude-sonnet-4-6` per CLAUDE.md."""
    assert MODEL_FOR_TIER["sonnet"] == "claude-sonnet-4-6"


@pytest.mark.unit
def test_summarize_returns_text_and_usage() -> None:
    """The summarizer parses Claude's text blocks and surfaces token counts."""
    fake = FakeAnthropicClient(response_text="Brief.")
    summarizer = Summarizer(client=fake)
    result = summarizer.summarize("Hello world.", tier="haiku")
    assert result.text == "Brief."
    assert result.model == "claude-haiku-4-5"
    assert result.input_tokens == 120
    assert result.output_tokens == 45


@pytest.mark.unit
def test_summarize_passes_tier_specific_model() -> None:
    """The `tier` argument routes to the matching Claude model id."""
    fake = FakeAnthropicClient()
    summarizer = Summarizer(client=fake)
    summarizer.summarize("a", tier="sonnet")
    assert fake.messages.calls[0]["model"] == "claude-sonnet-4-6"


@pytest.mark.unit
def test_summarize_caches_system_prompt() -> None:
    """The system block is sent with `cache_control` so the prefix is cached."""
    fake = FakeAnthropicClient()
    summarizer = Summarizer(client=fake)
    summarizer.summarize("a", tier="haiku")
    call = fake.messages.calls[0]
    system_blocks = call["system"]
    assert isinstance(system_blocks, list)
    assert system_blocks[0]["text"] == SYSTEM_PROMPT
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.unit
def test_summarize_sends_transcript_in_user_turn() -> None:
    """The transcript text appears in the user message content, not the system."""
    fake = FakeAnthropicClient()
    summarizer = Summarizer(client=fake)
    summarizer.summarize("the quick brown fox", tier="haiku")
    call = fake.messages.calls[0]
    messages = call["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "user"
    assert "the quick brown fox" in messages[0]["content"][0]["text"]


@pytest.mark.unit
def test_summarize_concatenates_multiple_text_blocks() -> None:
    """If Claude returns multiple text blocks, they concatenate in order."""

    class _Block:
        def __init__(self, kind: str, text: str) -> None:
            self.type = kind
            self.text = text

    class _Usage:
        input_tokens = 5
        output_tokens = 3

    class _Resp:
        content: list[_Block] = [_Block("text", "hello "), _Block("text", "world")]  # noqa: RUF012
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs: object) -> _Resp:
            return _Resp()

    class _Client:
        messages = _Messages()

    summarizer = Summarizer(client=_Client())
    result = summarizer.summarize("anything", tier="haiku")
    assert result.text == "hello world"


@pytest.mark.unit
def test_summarize_skips_non_text_blocks() -> None:
    """Non-text content blocks (e.g. tool_use) are ignored when extracting text."""

    class _Block:
        def __init__(self, kind: str, text: str) -> None:
            self.type = kind
            self.text = text

    class _Resp:
        content: list[_Block] = [  # noqa: RUF012
            _Block("tool_use", "junk"),
            _Block("text", "real summary"),
        ]
        usage = None

    class _Messages:
        def create(self, **kwargs: object) -> _Resp:
            return _Resp()

    class _Client:
        messages = _Messages()

    summarizer = Summarizer(client=_Client())
    result = summarizer.summarize("anything", tier="haiku")
    assert result.text == "real summary"
    assert result.input_tokens == 0
    assert result.output_tokens == 0


@pytest.mark.unit
def test_summarize_handles_response_without_content() -> None:
    """A response with `content=None` yields an empty summary, not an error."""

    class _Resp:
        content = None
        usage = None

    class _Messages:
        def create(self, **kwargs: object) -> _Resp:
            return _Resp()

    class _Client:
        messages = _Messages()

    summarizer = Summarizer(client=_Client())
    result = summarizer.summarize("anything", tier="haiku")
    assert result.text == ""
