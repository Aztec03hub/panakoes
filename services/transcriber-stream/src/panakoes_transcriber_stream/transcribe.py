"""Adapter around the vendored ``backend_factory``.

Centralizes the construction of the faster-whisper ASR with the
Panakoes-specific argument shape so the rest of the service does not
need to know about the upstream API surface.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import Config

logger = logging.getLogger(__name__)


def build_asr(cfg: Config, *, factory: Any | None = None) -> Any:
    """Build the faster-whisper ``asr`` object via the vendored factory.

    ``factory`` is a test seam; production callers leave it ``None`` and
    we import the real ``backend_factory`` from the vendored module.
    """

    if factory is None:
        from .vendor.whisperlivekit.local_agreement.whisper_online import (
            backend_factory,
        )

        factory = backend_factory

    logger.info(
        "transcribe_backend_factory_start",
        extra={
            "model_size": cfg.model_size,
            "model_dir": cfg.model_dir,
            "language_hint": cfg.language_hint,
            "buffer_trimming": cfg.buffer_trimming,
            "buffer_trimming_sec": cfg.buffer_trimming_sec,
        },
    )

    return factory(
        backend="faster-whisper",
        lan=cfg.language_hint,
        model_size=cfg.model_size,
        model_cache_dir=cfg.model_cache_dir,
        model_dir=cfg.model_dir,
        model_path=None,
        lora_path=None,
        direct_english_translation=False,
        buffer_trimming=cfg.buffer_trimming,
        buffer_trimming_sec=cfg.buffer_trimming_sec,
        confidence_validation=False,
        warmup_file=cfg.warmup_clip_path,
        min_chunk_size=cfg.min_chunk_seconds,
    )


def chunk_tokens_for_ws(
    tokens: list[Any], *, max_bytes: int = 24_000
) -> list[list[dict[str, Any]]]:
    """Split a list of ``ASRToken`` into ws-frame-sized buckets.

    The API GW WS frame cap is 32 KB; we target 24 KB to leave a margin
    for the envelope keys. Each returned bucket contains token dicts
    ready to be put in a ``{"type":"final-chunk","tokens":[...]}`` body.
    """

    import json as _json

    buckets: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 2  # leading "[]"
    for token in tokens:
        entry = {
            "text": getattr(token, "text", ""),
            "start": getattr(token, "start", None),
            "end": getattr(token, "end", None),
            "probability": getattr(token, "probability", None),
        }
        entry_bytes = len(_json.dumps(entry, ensure_ascii=False).encode("utf-8")) + 1
        if current and current_size + entry_bytes > max_bytes:
            buckets.append(current)
            current = []
            current_size = 2
        current.append(entry)
        current_size += entry_bytes

    if current:
        buckets.append(current)
    return buckets
