"""Unit tests for the whisper-wrapping transcribe module."""

from __future__ import annotations

from typing import Any

import pytest

from panakoes_transcriber_batch import transcribe as transcribe_module
from panakoes_transcriber_batch.transcribe import (
    TranscriptionFailedError,
    WhisperLoadError,
    load_model,
    transcribe,
)

pytestmark = pytest.mark.unit


class _StubModel:
    def __init__(self, result: dict[str, Any] | None = None, raises: Exception | None = None):
        self._result = result
        self._raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def transcribe(self, audio_path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((audio_path, kwargs))
        if self._raises is not None:
            raise self._raises
        return self._result or {}


class _StubWhisperModule:
    def __init__(self, on_load: Any = None) -> None:
        self._on_load = on_load
        self.load_calls: list[tuple[str, dict[str, Any]]] = []

    def load_model(self, path: str, **kwargs: Any) -> Any:
        self.load_calls.append((path, kwargs))
        if isinstance(self._on_load, Exception):
            raise self._on_load
        return self._on_load


def test_load_model_returns_loaded_module(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _StubModel()
    stub = _StubWhisperModule(on_load=expected)
    monkeypatch.setattr(transcribe_module, "_load_whisper_module", lambda: stub)
    model = load_model("/opt/whisper/models/large-v3.pt", device="cpu")
    assert model is expected
    assert stub.load_calls == [("/opt/whisper/models/large-v3.pt", {"device": "cpu"})]


def test_load_model_translates_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> Any:
        raise WhisperLoadError("missing")

    monkeypatch.setattr(transcribe_module, "_load_whisper_module", _raise)
    with pytest.raises(WhisperLoadError):
        load_model("/x")


def test_load_model_translates_load_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubWhisperModule(on_load=RuntimeError("cuda oom"))
    monkeypatch.setattr(transcribe_module, "_load_whisper_module", lambda: stub)
    with pytest.raises(WhisperLoadError, match="failed to load"):
        load_model("/x", device="cuda")


def test_transcribe_maps_to_canonical_shape(fake_whisper_result: dict[str, Any]) -> None:
    model = _StubModel(result=fake_whisper_result)
    out = transcribe(model, "/tmp/audio.wav")  # noqa: S108
    assert out["text"] == "hello world this is a test"
    assert out["language"] == "en"
    assert out["word_count"] == 6
    assert out["duration_seconds"] == pytest.approx(3.2)
    assert len(out["segments"]) == 2
    assert out["segments"][0]["words"][0] == {"text": "hello", "start": 0.0, "end": 0.5}


def test_transcribe_calls_with_fp16_and_word_timestamps(
    fake_whisper_result: dict[str, Any],
) -> None:
    model = _StubModel(result=fake_whisper_result)
    transcribe(model, "/tmp/audio.wav")  # noqa: S108
    _, kwargs = model.calls[0]
    assert kwargs == {"fp16": True, "word_timestamps": True}


def test_transcribe_drops_word_entries_missing_fields() -> None:
    result = {
        "text": "hi",
        "language": "en",
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "hi",
                "words": [
                    {"word": "hi", "start": 0.0, "end": 0.5},
                    {"word": "broken"},
                    {"start": 0.6, "end": 0.7},
                ],
            }
        ],
    }
    out = transcribe(_StubModel(result=result), "/tmp/a.wav")  # noqa: S108
    assert out["word_count"] == 1
    assert out["segments"][0]["words"] == [{"text": "hi", "start": 0.0, "end": 0.5}]


def test_transcribe_rejects_non_dict_result() -> None:
    model = _StubModel(result=None)
    model._result = "not a dict"  # type: ignore[assignment]
    with pytest.raises(TranscriptionFailedError):
        transcribe(model, "/tmp/a.wav")  # noqa: S108


def test_transcribe_retries_then_raises_on_persistent_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Shorten the wait so the retry loop completes inside the test budget.
    from tenacity import stop_after_attempt, wait_none

    monkeypatch.setattr(
        transcribe_module._transcribe_with_retry.retry,
        "wait",
        wait_none(),
    )
    monkeypatch.setattr(
        transcribe_module._transcribe_with_retry.retry,
        "stop",
        stop_after_attempt(2),
    )
    model = _StubModel(raises=RuntimeError("transient"))
    with pytest.raises(TranscriptionFailedError):
        transcribe(model, "/tmp/a.wav")  # noqa: S108
    assert len(model.calls) == 2


def test_transcribe_translates_unexpected_exception() -> None:
    model = _StubModel(raises=ValueError("bad audio"))
    with pytest.raises(TranscriptionFailedError, match="ValueError"):
        transcribe(model, "/tmp/a.wav")  # noqa: S108


def test_canonical_shape_handles_no_segments() -> None:
    model = _StubModel(result={"text": "", "language": None, "segments": []})
    out = transcribe(model, "/tmp/a.wav")  # noqa: S108
    assert out == {
        "text": "",
        "language": None,
        "duration_seconds": 0.0,
        "word_count": 0,
        "segments": [],
    }


def test_load_whisper_module_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the real _load_whisper_module path by ensuring import fails
    # (whisper is not installed in the test environment).
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "whisper":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(WhisperLoadError, match="gpu-transcribe AMI"):
        transcribe_module._load_whisper_module()
