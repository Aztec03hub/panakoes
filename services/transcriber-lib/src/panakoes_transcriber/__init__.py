"""Pluggable Transcriber abstraction for Panakoes services.

This package defines the minimal interface every transcription backend
implements. Concrete backends live in their own ``services/transcriber-*``
packages and depend on this lib for the shared types.

Per ADR-009 (transcription pluggable abstraction), call sites depend on the
``Transcriber`` Protocol rather than any concrete backend, so swapping
backends is configuration, not a code change.
"""

from panakoes_transcriber.errors import (
    TranscriberAuthError,
    TranscriberError,
    TranscriberRateLimitError,
    TranscriberRequestError,
    TranscriberTimeoutError,
    TranscriberUpstreamError,
)
from panakoes_transcriber.protocol import Transcriber
from panakoes_transcriber.types import (
    TranscriptionResult,
    TranscriptionSegment,
    Word,
)

__all__ = [
    "Transcriber",
    "TranscriberAuthError",
    "TranscriberError",
    "TranscriberRateLimitError",
    "TranscriberRequestError",
    "TranscriberTimeoutError",
    "TranscriberUpstreamError",
    "TranscriptionResult",
    "TranscriptionSegment",
    "Word",
]
