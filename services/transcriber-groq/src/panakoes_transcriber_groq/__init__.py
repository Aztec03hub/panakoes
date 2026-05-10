"""Groq Whisper-large-v3 hosted-API backend for the Panakoes Transcriber Protocol.

This package wraps Groq's OpenAI-compatible ``/audio/transcriptions``
endpoint and returns a ``panakoes_transcriber.TranscriptionResult``. It
is the first concrete backend implementation; see the lib's README for
the full Protocol and the planned follow-up backends (OpenAI Whisper
API, self-hosted Whisper-on-GPU).
"""

from panakoes_transcriber_groq.backend import GroqTranscriberBackend

__all__ = ["GroqTranscriberBackend"]
