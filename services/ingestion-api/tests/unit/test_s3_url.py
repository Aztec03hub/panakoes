"""Unit tests for `panakoes_ingestion_api.storage.s3`."""

from __future__ import annotations

from typing import Any

import pytest

from panakoes_ingestion_api.storage.s3 import (
    S3PresignedUrlGenerator,
    build_object_key,
    sanitize_filename,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("clean.m4a", "clean.m4a"),
        ("with space.wav", "with_space.wav"),
        ("hello/world.txt", "hello_world.txt"),
        ("emoji-y\U0001f600.m4a", "emoji-y_.m4a"),
        ("", "unnamed"),
        ("....leading.dots", "leading.dots"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    """Filename sanitization collapses unsafe characters predictably."""
    assert sanitize_filename(raw) == expected


@pytest.mark.unit
def test_sanitize_filename_drops_path_traversal_segments() -> None:
    """Path-traversal-style input never contains a real path separator."""
    cleaned = sanitize_filename("../../etc/passwd")
    assert "/" not in cleaned
    assert cleaned.endswith("passwd")


@pytest.mark.unit
def test_sanitize_filename_falls_back_when_all_chars_unsafe() -> None:
    """All-illegal input still produces a usable, non-empty key segment."""
    cleaned = sanitize_filename("???")
    # The result is non-empty and contains no separators / unsafe chars.
    assert cleaned != ""
    assert "/" not in cleaned
    assert all(ch.isalnum() or ch in "._-" for ch in cleaned)


@pytest.mark.unit
def test_build_object_key_uses_sanitized_filename() -> None:
    """Object key concatenates user/ingestion ids with the safe filename."""
    key = build_object_key("user_1", "ing_42", "weird name?.m4a")
    assert key == "audio/user_1/ing_42/weird_name_.m4a"


class _FakeS3Client:
    """Minimal stand-in capturing the args passed to `generate_presigned_url`."""

    def __init__(self, returned_url: str = "https://example/signed") -> None:
        self.returned_url = returned_url
        self.last_call: dict[str, Any] | None = None

    def generate_presigned_url(
        self,
        operation: str,
        Params: dict[str, Any],  # mirror boto3 signature
        ExpiresIn: int,
        HttpMethod: str,
    ) -> str:
        self.last_call = {
            "operation": operation,
            "Params": Params,
            "ExpiresIn": ExpiresIn,
            "HttpMethod": HttpMethod,
        }
        return self.returned_url


@pytest.mark.unit
def test_generate_put_url_passes_expected_args() -> None:
    """The generator forwards bucket, key, content-type, and TTL to boto3."""
    fake = _FakeS3Client()
    gen = S3PresignedUrlGenerator(bucket="b", region_name="us-east-1", client=fake)  # type: ignore[arg-type]
    url = gen.generate_put_url(
        key="audio/u/i/f.m4a",
        content_type="audio/mp4",
        size_bytes=1234,
        expires_in_seconds=900,
    )
    assert url == "https://example/signed"
    assert fake.last_call is not None
    assert fake.last_call["operation"] == "put_object"
    assert fake.last_call["HttpMethod"] == "PUT"
    assert fake.last_call["ExpiresIn"] == 900
    assert fake.last_call["Params"]["Bucket"] == "b"
    assert fake.last_call["Params"]["Key"] == "audio/u/i/f.m4a"
    assert fake.last_call["Params"]["ContentType"] == "audio/mp4"
    assert fake.last_call["Params"]["ContentLength"] == 1234
    # SSE-KMS must be pinned in the signed params: the audio-uploads
    # bucket has SSE-KMS as its default encryption, and S3 rejects
    # SigV4 PUTs against KMS-encrypted buckets that do not signal KMS
    # in the signed request.
    assert fake.last_call["Params"]["ServerSideEncryption"] == "aws:kms"


@pytest.mark.unit
def test_default_client_uses_sigv4() -> None:
    """The default boto3 client must be configured with SigV4.

    SigV2 against KMS-encrypted buckets returns:
        Requests specifying Server Side Encryption with AWS KMS managed
        keys require AWS Signature Version 4.

    Verified by inspecting the constructed client's signature_version
    config; we do not actually issue a real boto request.
    """
    gen = S3PresignedUrlGenerator(bucket="b", region_name="us-east-1")
    assert gen._client.meta.config.signature_version == "s3v4"


@pytest.mark.unit
def test_bucket_property_exposes_constructor_arg() -> None:
    """The `bucket` property reflects what was passed in."""
    fake = _FakeS3Client()
    gen = S3PresignedUrlGenerator(bucket="my-bucket", client=fake)  # type: ignore[arg-type]
    assert gen.bucket == "my-bucket"
