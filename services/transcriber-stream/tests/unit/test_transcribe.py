"""Tests for the backend_factory adapter and the ws-chunking helper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from panakoes_transcriber_stream.config import load_config_from_env
from panakoes_transcriber_stream.transcribe import build_asr, chunk_tokens_for_ws


def test_build_asr_passes_expected_kwargs(valid_env: dict[str, str]) -> None:
    captured: dict[str, Any] = {}

    def fake_factory(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "asr-sentinel"

    cfg = load_config_from_env()
    result = build_asr(cfg, factory=fake_factory)
    assert result == "asr-sentinel"
    # All wired kwargs must reach the factory.
    assert captured["backend"] == "faster-whisper"
    assert captured["lan"] == cfg.language_hint
    assert captured["model_size"] == cfg.model_size
    assert captured["model_cache_dir"] == cfg.model_cache_dir
    assert captured["model_dir"] == cfg.model_dir
    assert captured["model_path"] is None
    assert captured["lora_path"] is None
    assert captured["direct_english_translation"] is False
    assert captured["buffer_trimming"] == cfg.buffer_trimming
    assert captured["buffer_trimming_sec"] == cfg.buffer_trimming_sec
    assert captured["confidence_validation"] is False
    assert captured["warmup_file"] == cfg.warmup_clip_path
    assert captured["min_chunk_size"] == cfg.min_chunk_seconds


@dataclass
class _Token:
    text: str
    start: float
    end: float
    probability: float | None = None


def test_chunk_tokens_for_ws_empty_returns_empty_list() -> None:
    assert chunk_tokens_for_ws([]) == []


def test_chunk_tokens_for_ws_small_payload_stays_one_bucket() -> None:
    tokens = [_Token(text=f"w{i}", start=float(i), end=float(i + 1)) for i in range(10)]
    buckets = chunk_tokens_for_ws(tokens)
    assert len(buckets) == 1
    assert sum(len(b) for b in buckets) == len(tokens)


def test_chunk_tokens_for_ws_oversize_splits_into_multiple_buckets() -> None:
    # Each token contributes >300 bytes JSON; 100 of them is ~30 KB and
    # must split into at least two buckets at the 24 KB target.
    payload_text = "x" * 300
    tokens = [_Token(text=payload_text, start=float(i), end=float(i + 1)) for i in range(100)]
    buckets = chunk_tokens_for_ws(tokens, max_bytes=24_000)
    assert len(buckets) >= 2
    assert sum(len(b) for b in buckets) == len(tokens)


def test_chunk_tokens_for_ws_each_bucket_under_threshold() -> None:
    import json as _json

    payload_text = "x" * 100
    tokens = [_Token(text=payload_text, start=float(i), end=float(i + 1)) for i in range(50)]
    buckets = chunk_tokens_for_ws(tokens, max_bytes=5_000)
    for bucket in buckets:
        encoded = _json.dumps(bucket, ensure_ascii=False).encode("utf-8")
        # Allow some slack for the running-size approximation; bucket
        # should be near or under the cap, not 2x over.
        assert len(encoded) <= 6_000
