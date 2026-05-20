"""Tests for SeededOnlineASRProcessor's prompt-seed semantics.

The subclass MUST inject the seed into ``prompt()`` only while
``committed`` is empty; it must NEVER mutate ``committed`` or any other
state. We use a stub ``asr`` object (no real GPU) so the test runs in
milliseconds and does not depend on faster-whisper at all.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from panakoes_transcriber_stream.asr_proxy import SeededOnlineASRProcessor


@dataclass
class _StubAsr:
    """Just enough surface for OnlineASRProcessor.__init__ + prompt()."""

    sep: str = ""
    tokenizer: Any = None
    confidence_validation: bool = False
    buffer_trimming: str = "segment"
    buffer_trimming_sec: float = 15.0
    backend_choice: str = "stub"


def _build(seed: str | None) -> SeededOnlineASRProcessor:
    return SeededOnlineASRProcessor(_StubAsr(), prompt_seed_text=seed, logfile=sys.stderr)


def test_prompt_seed_injects_when_committed_empty() -> None:
    proc = _build("the quick brown fox")
    base_prompt, context = proc.prompt()
    assert base_prompt.startswith("the quick brown fox")
    assert context == ""
    # committed must NOT have been touched.
    assert proc.committed == []


def test_prompt_seed_dropped_once_committed_present() -> None:
    proc = _build("seed text")
    from panakoes_transcriber_stream.vendor.whisperlivekit.timed_objects import (
        ASRToken,
    )

    proc.committed = [ASRToken(start=1.0, end=2.0, text="hello")]
    base_prompt, _context = proc.prompt()
    # Seed must not appear anymore; only the committed-derived prompt.
    assert "seed text" not in base_prompt


def test_empty_seed_is_noop() -> None:
    proc = _build("")
    base_prompt, _ = proc.prompt()
    assert base_prompt == ""


def test_none_seed_is_noop() -> None:
    proc = _build(None)
    base_prompt, _ = proc.prompt()
    assert base_prompt == ""


def test_seed_text_is_stripped() -> None:
    proc = _build("   padded   ")
    base_prompt, _ = proc.prompt()
    assert base_prompt == "padded"
