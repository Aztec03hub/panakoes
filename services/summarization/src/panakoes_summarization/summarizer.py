"""Anthropic Claude wrapper for transcript summarization.

The model selection is the locked decision in CLAUDE.md: `haiku` maps to
`claude-haiku-4-5` (cost default) and `sonnet` maps to `claude-sonnet-4-6`
(paid-tier deep summary). The system prompt is held stable and tagged
with `cache_control` so repeated calls hit the prefix cache; the
transcript itself is the only variable input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import anthropic

from panakoes_summarization.models import Tier

MODEL_FOR_TIER: dict[Tier, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
}

SYSTEM_PROMPT = (
    "You are the Panakoes summarization service. Write a concise, faithful "
    "summary of the user's transcript in Markdown. Use a short paragraph "
    "for the gist, then a bulleted list of the main points. Do not invent "
    "facts not present in the transcript. Do not add a preamble; start "
    "directly with the summary."
)


@dataclass(frozen=True)
class SummaryResult:
    """The Claude output plus the bookkeeping fields the storage layer needs."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int


class AnthropicMessages(Protocol):
    """Subset of the Anthropic SDK we actually call.

    Wrapping the SDK in a Protocol lets unit tests swap in a fake
    without monkey-patching the global `anthropic` module.
    """

    def create(self, **kwargs: object) -> object:
        """Forward to `anthropic.Anthropic().messages.create`."""
        ...


class AnthropicClient(Protocol):
    """The single attribute we actually consume from the SDK.

    Declared as a property so the Protocol matches both the real SDK
    (where `messages` is exposed via a property) and our test fake
    (where it is a plain attribute).
    """

    @property
    def messages(self) -> AnthropicMessages:
        """Return the SDK's `Messages` resource."""
        ...


class Summarizer:
    """Thin wrapper that calls the Claude Messages API and returns a `SummaryResult`."""

    def __init__(
        self,
        client: AnthropicClient | None = None,
        *,
        api_key: str | None = None,
    ) -> None:
        """Build a summarizer.

        Pass `client` to inject a fake (used in tests). Otherwise the
        default `anthropic.Anthropic` client is constructed from the
        provided `api_key`, which falls back to the SDK's own
        `ANTHROPIC_API_KEY` env-var resolution when omitted.
        """
        if client is not None:
            self._client: AnthropicClient = client
        elif api_key:
            # The real SDK is structurally compatible with `AnthropicClient`
            # but mypy cannot see that through `anthropic-py` without stubs.
            self._client = anthropic.Anthropic(api_key=api_key)  # type: ignore[assignment]
        else:
            self._client = anthropic.Anthropic()  # type: ignore[assignment]

    def summarize(self, transcript: str, *, tier: Tier) -> SummaryResult:
        """Call Claude and return the parsed summary plus usage counters.

        The system prompt is sent as a single text block tagged with
        `cache_control={"type": "ephemeral"}`, which lets repeated
        summarization calls re-use the cached system prefix at ~10% of
        the input cost (see `shared/prompt-caching.md`). The transcript
        is the only volatile input, so it sits in the user turn.
        """
        model = MODEL_FOR_TIER[tier]
        response = self._client.messages.create(
            model=model,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Summarize this transcript:\n\n{transcript}",
                        }
                    ],
                }
            ],
        )
        text = self._extract_text(response)
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return SummaryResult(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _extract_text(response: object) -> str:
        """Concatenate the text blocks from a Claude `Message` response.

        The Messages API returns `content` as a list of content blocks;
        only the `text` blocks contribute to the summary string.
        """
        blocks = getattr(response, "content", None) or []
        parts: list[str] = []
        for block in blocks:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_value = getattr(block, "text", "")
                if text_value:
                    parts.append(str(text_value))
        return "".join(parts).strip()
