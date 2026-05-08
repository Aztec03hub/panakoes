"""Pre-signed S3 PUT URL generation and S3 key construction."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

# Filename sanitization: ASCII alphanum, hyphen, dot, underscore. Anything
# else collapses to `_` so the S3 object key stays stable and predictable.
_FILENAME_SAFE_RE: re.Pattern[str] = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_filename(filename: str) -> str:
    """Reduce `filename` to ASCII alphanum, hyphen, dot, underscore.

    Empty or all-illegal inputs collapse to `unnamed`. We do NOT enforce
    extension validity here; that is the upload pipeline's job.
    """
    cleaned = _FILENAME_SAFE_RE.sub("_", filename.strip())
    cleaned = cleaned.lstrip(".")  # avoid hidden-file-style keys
    return cleaned or "unnamed"


def build_object_key(user_id: str, ingestion_id: str, filename: str) -> str:
    """Build the S3 object key for an ingestion."""
    return f"audio/{user_id}/{ingestion_id}/{sanitize_filename(filename)}"


class S3PresignedUrlGenerator:
    """Generate pre-signed PUT URLs for raw audio uploads."""

    def __init__(
        self,
        bucket: str,
        region_name: str = "us-east-1",
        client: S3Client | None = None,
    ) -> None:
        """Bind to a bucket and (optional) injected client.

        boto3 resolves credentials via the default chain, so this
        constructor carries no secret material. Tests inject a moto-
        backed client via the `client` argument.
        """
        self._bucket = bucket
        self._region_name = region_name
        self._client = client if client is not None else boto3.client("s3", region_name=region_name)

    @property
    def bucket(self) -> str:
        """The S3 bucket this generator targets."""
        return self._bucket

    def generate_put_url(
        self,
        *,
        key: str,
        content_type: str,
        size_bytes: int,
        expires_in_seconds: int,
    ) -> str:
        """Return a pre-signed PUT URL for the given key.

        The URL pins `Content-Type` so clients cannot smuggle a
        different MIME type than they declared. Content-length is NOT
        encoded into the URL itself for `put_object` (S3 enforces
        per-request `Content-Length` on PUT regardless); we keep the
        argument in the signature so the caller is forced to think about
        size up front and so future swaps to `generate_presigned_post`
        do not require call-site changes.
        """
        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "ContentType": content_type,
            "ContentLength": size_bytes,
        }
        url: str = self._client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=expires_in_seconds,
            HttpMethod="PUT",
        )
        return url
