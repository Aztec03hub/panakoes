"""Whisper-large-v3 fp16 transcription wrapper.

The ``whisper`` Python module is baked into the GPU AMI (per
``infra/ami/gpu-transcribe/``); the wheel is NOT a dependency of this
package. Import is lazy so unit tests can monkeypatch the seam without
the wheel being installed in the test environment.

Output shape: a ``dict`` matching the canonical Panakoes transcript
shape (segments + words + language + duration). Mapping from the raw
whisper output is done here so the rest of the service stays
backend-agnostic.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)


class WhisperLoadError(RuntimeError):
    """Raised when the whisper module or model weights cannot be loaded.

    Distinct from a transcription failure so the caller can decide
    whether to mark the session ``errored`` (load error == permanent,
    do not retry the job) versus retry-the-step.
    """


class TranscriptionFailedError(RuntimeError):
    """Raised when whisper.transcribe fails after all retries."""


def _load_whisper_module() -> Any:
    """Import the system-installed ``whisper`` module.

    Lazy so unit tests can monkeypatch ``_load_whisper_module`` and run
    without the openai-whisper wheel installed. Raises
    :class:`WhisperLoadError` with a useful message if the import fails
    (e.g., the container was built off a non-AMI base by mistake).
    """
    try:
        import whisper
    except ImportError as exc:
        raise WhisperLoadError(
            "whisper module is not importable; this container must run on the "
            "panakoes gpu-transcribe AMI which bakes openai-whisper into the "
            "system Python"
        ) from exc
    return whisper


def load_model(model_path: str, *, device: str = "cuda") -> Any:
    """Load the whisper model from the on-disk path baked into the AMI.

    Whisper's ``load_model`` accepts either a model name (which would
    trigger a download) or a path to a ``.pt`` file. We pass the path
    so a network failure cannot turn into a model-download retry storm
    in a fleet of Batch jobs.
    """
    whisper = _load_whisper_module()
    start = time.monotonic()
    try:
        model = whisper.load_model(model_path, device=device)
    except Exception as exc:
        raise WhisperLoadError(f"failed to load whisper model from {model_path!r}") from exc
    logger.info(
        "transcriber_batch_model_loaded",
        model_path=model_path,
        device=device,
        load_seconds=round(time.monotonic() - start, 3),
    )
    return model


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(RuntimeError),
)
def _transcribe_with_retry(model: Any, audio_path: str) -> dict[str, Any]:
    """Call ``model.transcribe`` with retry on transient ``RuntimeError``.

    Whisper raises bare ``RuntimeError`` on CUDA-side transient hiccups
    (out-of-memory recoverable cases, kernel launch timeouts).
    Three attempts with exponential backoff is the documented pattern.
    Permanent failures (bad audio, model load issues) surface a non-
    RuntimeError and bypass the retry.
    """
    result = model.transcribe(audio_path, fp16=True, word_timestamps=True)
    if not isinstance(result, dict):
        raise TranscriptionFailedError(
            f"whisper.transcribe returned a non-dict ({type(result).__name__})"
        )
    return result


def transcribe(model: Any, audio_path: str) -> dict[str, Any]:
    """Transcribe ``audio_path`` with the loaded whisper model.

    Returns the canonical Panakoes transcript shape (a JSON-serializable
    dict with keys ``text``, ``language``, ``duration_seconds``,
    ``segments``, ``word_count``). Raises :class:`TranscriptionFailedError`
    on a definitive failure after retries.
    """
    start = time.monotonic()
    try:
        raw = _transcribe_with_retry(model, audio_path)
    except RetryError as exc:
        raise TranscriptionFailedError(
            f"whisper.transcribe failed after retries for {audio_path!r}"
        ) from exc
    except TranscriptionFailedError:
        raise
    except Exception as exc:
        raise TranscriptionFailedError(
            f"whisper.transcribe raised {type(exc).__name__} for {audio_path!r}: {exc}"
        ) from exc

    transcript = _to_canonical(raw)
    logger.info(
        "transcriber_batch_transcription_complete",
        audio_path=audio_path,
        duration_seconds=transcript.get("duration_seconds"),
        word_count=transcript.get("word_count"),
        elapsed_seconds=round(time.monotonic() - start, 3),
    )
    return transcript


def _to_canonical(raw: dict[str, Any]) -> dict[str, Any]:
    """Map whisper's verbose output onto the Panakoes transcript shape.

    Whisper returns:
      - ``text``: full transcript string
      - ``language``: detected language (ISO 639-1)
      - ``segments``: list of dicts each with ``start``, ``end``,
        ``text``, and ``words`` (list of ``{word, start, end}``)

    We add ``duration_seconds`` (max segment end, falls back to 0.0)
    and ``word_count`` (count of words across segments) so downstream
    consumers do not re-derive them.
    """
    text = str(raw.get("text", "")).strip()
    language_value = raw.get("language")
    language = str(language_value) if language_value is not None else None
    raw_segments = raw.get("segments") or []

    segments: list[dict[str, Any]] = []
    word_count = 0
    max_end = 0.0
    for seg in raw_segments:
        seg_end = float(seg.get("end", 0.0))
        max_end = max(max_end, seg_end)
        seg_words: list[dict[str, Any]] = []
        for word in seg.get("words") or []:
            word_text = word.get("word")
            w_start = word.get("start")
            w_end = word.get("end")
            if word_text is None or w_start is None or w_end is None:
                continue
            seg_words.append(
                {
                    "text": str(word_text).strip(),
                    "start": float(w_start),
                    "end": float(w_end),
                }
            )
        word_count += len(seg_words)
        segments.append(
            {
                "text": str(seg.get("text", "")).strip(),
                "start": float(seg.get("start", 0.0)),
                "end": seg_end,
                "words": seg_words,
            }
        )

    return {
        "text": text,
        "language": language,
        "duration_seconds": max_end,
        "word_count": word_count,
        "segments": segments,
    }
