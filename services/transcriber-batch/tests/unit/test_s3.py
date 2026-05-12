"""Unit tests for the S3 helper functions."""

from __future__ import annotations

import json
import os

import pytest

from panakoes_transcriber_batch.s3 import (
    S3Uri,
    download_audio,
    parse_s3_uri,
    upload_transcript_json,
)

pytestmark = pytest.mark.unit


def test_parse_s3_uri_happy_path() -> None:
    parsed = parse_s3_uri("s3://bucket/path/to/file.wav")
    assert parsed == S3Uri(bucket="bucket", key="path/to/file.wav")
    assert parsed.uri == "s3://bucket/path/to/file.wav"


def test_parse_s3_uri_rejects_wrong_scheme() -> None:
    with pytest.raises(ValueError, match="s3://"):
        parse_s3_uri("https://bucket/key")


def test_parse_s3_uri_rejects_missing_bucket() -> None:
    with pytest.raises(ValueError, match="bucket"):
        parse_s3_uri("s3:///key")


def test_parse_s3_uri_rejects_missing_key() -> None:
    with pytest.raises(ValueError, match="key"):
        parse_s3_uri("s3://bucket")


def test_download_audio_writes_object_bytes_to_path(
    s3_client: object,
    audio_object: str,
    tmp_path: object,
) -> None:
    dest = os.path.join(str(tmp_path), "out.wav")
    download_audio(s3_client, audio_object, dest)
    assert os.path.exists(dest)
    with open(dest, "rb") as fh:
        assert fh.read().startswith(b"RIFF")


def test_upload_transcript_json_writes_canonical_filename(s3_client: object) -> None:
    payload = {"text": "hi", "word_count": 1, "language": "en"}
    result = upload_transcript_json(
        s3_client,
        "s3://panakoes-dev-transcripts/sess_abc/",
        payload,
    )
    assert result.bucket == "panakoes-dev-transcripts"
    assert result.key == "sess_abc/transcript.json"

    body = s3_client.get_object(Bucket=result.bucket, Key=result.key)["Body"].read()  # type: ignore[attr-defined]
    assert json.loads(body) == payload


def test_upload_transcript_json_sets_json_content_type(s3_client: object) -> None:
    result = upload_transcript_json(
        s3_client,
        "s3://panakoes-dev-transcripts/sess_abc",
        {"text": "x"},
    )
    head = s3_client.head_object(Bucket=result.bucket, Key=result.key)  # type: ignore[attr-defined]
    assert head["ContentType"] == "application/json"
