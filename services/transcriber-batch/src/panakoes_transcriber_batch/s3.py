"""S3 download / upload helpers for the transcriber-batch worker.

Two small wrappers around boto3 plus a URI parser. Pulled out of
``main`` so the unit tests can drive them with ``moto`` and the main
flow stays a thin orchestration layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class S3Uri:
    """Parsed ``s3://bucket/key`` URI."""

    bucket: str
    key: str

    @property
    def uri(self) -> str:
        """Reconstruct the canonical ``s3://`` form."""
        return f"s3://{self.bucket}/{self.key}"


def parse_s3_uri(uri: str) -> S3Uri:
    """Parse ``s3://bucket/key`` into an :class:`S3Uri`.

    Rejects empty bucket or empty key with ``ValueError`` so a
    misconfigured job fails fast at parse time instead of later inside
    a boto3 call with a less obvious error.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"expected s3:// uri, got: {uri!r}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket:
        raise ValueError(f"s3 uri is missing a bucket: {uri!r}")
    if not key:
        raise ValueError(f"s3 uri is missing a key: {uri!r}")
    return S3Uri(bucket=bucket, key=key)


def download_audio(s3_client: Any, uri: str, dest_path: str) -> None:
    """Download the audio object at ``uri`` to ``dest_path``.

    Uses the boto3 managed ``download_file`` transfer so large files
    stream to disk without buffering the entire payload in memory.
    """
    parsed = parse_s3_uri(uri)
    s3_client.download_file(parsed.bucket, parsed.key, dest_path)


def upload_transcript_json(
    s3_client: Any,
    output_prefix: str,
    transcript: dict[str, Any],
) -> S3Uri:
    """Write ``transcript`` to ``<output_prefix>/transcript.json``.

    ``output_prefix`` is the ``S3_OUTPUT_PREFIX`` env var which is a
    full ``s3://bucket/prefix`` URI. The final object key is the
    prefix's path joined with ``transcript.json`` (the canonical
    filename per the runbook contract; downstream consumers look for
    that exact name).
    """
    parsed = parse_s3_uri(_join_prefix(output_prefix, "transcript.json"))
    body = json.dumps(transcript, separators=(",", ":"), sort_keys=True).encode("utf-8")
    s3_client.put_object(
        Bucket=parsed.bucket,
        Key=parsed.key,
        Body=body,
        ContentType="application/json",
    )
    return parsed


def _join_prefix(prefix_uri: str, filename: str) -> str:
    """Join ``prefix_uri`` (an ``s3://bucket/prefix`` URI) with ``filename``."""
    return prefix_uri.rstrip("/") + "/" + filename.lstrip("/")
