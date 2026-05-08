"""Parse S3 object keys produced by the Ingestion API.

The Ingestion API writes objects under `audio/{user_id}/{ingestion_id}/{filename}`
(see `services/ingestion-api/.../storage/s3.py::build_object_key`). We
mirror that contract here. Keys that do not match are returned as
`None` so the handler can log a metric and skip cleanly.

S3 event records URL-encode keys (`+` for space, `%XX` for special
chars). Decode before parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote_plus

_PREFIX = "audio/"


@dataclass(frozen=True, slots=True)
class ParsedKey:
    """Decomposed S3 object key for an ingestion upload."""

    user_id: str
    ingestion_id: str
    filename: str


def parse_object_key(raw_key: str) -> ParsedKey | None:
    """Parse a raw (URL-encoded) S3 object key into ids + filename.

    Returns `None` when the key does not match the expected layout, so
    callers can treat unrecognized uploads as a routing miss without
    raising. We accept exactly four `/`-separated segments after the
    `audio/` prefix because that is what the Ingestion API writes.
    """
    decoded = unquote_plus(raw_key)
    if not decoded.startswith(_PREFIX):
        return None
    rest = decoded[len(_PREFIX) :]
    parts = rest.split("/", 2)
    if len(parts) != 3:
        return None
    user_id, ingestion_id, filename = parts
    if not user_id or not ingestion_id or not filename:
        return None
    return ParsedKey(user_id=user_id, ingestion_id=ingestion_id, filename=filename)
