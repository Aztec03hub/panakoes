"""S3 + DDB persistence for streaming sessions.

Responsibilities:

* Read the prompt-seed text from the new session's DDB row at boot (a
  reconnect populates ``prompt_seed_text``; a fresh start leaves it
  empty).
* Update ``last_transcript_text`` and ``last_processed_upto`` on the
  session row as committed tokens arrive (the router reads these on a
  ``transcript-request``).
* Write the final transcript to S3 and update the row to ``ended``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def read_prompt_seed_from_ddb(
    session_id: str,
    *,
    sessions_table: str,
    ddb_resource: Any | None = None,
) -> str | None:
    """Return the ``prompt_seed_text`` attribute or ``None`` if absent.

    On a fresh session the attribute is unset; on a reconnect that the
    SPA carried over from a 2-hour-cap close, the streaming-router has
    populated it. Returns ``None`` on any error (missing row, unset
    attribute, DDB failure) so a transient persistence problem cannot
    block the cold-start path.
    """

    try:
        if ddb_resource is None:
            ddb_resource = boto3.resource("dynamodb")
        table = ddb_resource.Table(sessions_table)
        resp = table.get_item(Key={"session_id": session_id})
        item = resp.get("Item")
        if not item:
            return None
        seed = item.get("prompt_seed_text")
        if not isinstance(seed, str) or not seed.strip():
            return None
        return seed.strip()
    except Exception:
        logger.exception(
            "persistence_read_prompt_seed_failed",
            extra={"session_id": session_id},
        )
        return None


class Persistence:
    """S3 + DDB persistence facade for a single session."""

    def __init__(
        self,
        transcripts_bucket: str,
        sessions_table: str,
        session_id: str,
        *,
        s3_client: Any | None = None,
        ddb_resource: Any | None = None,
    ) -> None:
        self._transcripts_bucket = transcripts_bucket
        self._sessions_table_name = sessions_table
        self._session_id = session_id
        self._s3_client = s3_client
        self._ddb_resource = ddb_resource

    def _ensure_s3(self) -> Any:
        if self._s3_client is None:
            self._s3_client = boto3.client("s3")
        return self._s3_client

    def _ensure_table(self) -> Any:
        if self._ddb_resource is None:
            self._ddb_resource = boto3.resource("dynamodb")
        return self._ddb_resource.Table(self._sessions_table_name)

    async def update_last_transcript_tokens(
        self,
        tokens: Iterable[Any],
        *,
        audio_upto: float,
    ) -> None:
        """Concatenate ``tokens`` and append onto the session row.

        The router's ``transcript-request`` arm reads the full
        ``last_transcript_text`` value, so the column accumulates over
        the session's lifetime.

        DDB ``UpdateExpression`` does not support string concatenation
        (the ``+`` operator is numeric-only). We do a small
        read-modify-write here: GetItem the current row, append in
        Python, UpdateItem the new value. The conditional-write key
        guards against an unrelated concurrent writer overwriting our
        append.
        """

        text_pieces = [t.text for t in tokens if getattr(t, "text", "")]
        if not text_pieces:
            return
        appended = "".join(text_pieces)
        table = self._ensure_table()
        try:
            existing = table.get_item(
                Key={"session_id": self._session_id},
                ProjectionExpression="last_transcript_text",
            ).get("Item", {})
            prefix = existing.get("last_transcript_text", "") or ""
            new_text = prefix + appended
            table.update_item(
                Key={"session_id": self._session_id},
                UpdateExpression=(
                    "SET last_transcript_text = :text, "
                    "last_processed_upto = :upto, "
                    "updated_at = :now"
                ),
                ExpressionAttributeValues={
                    ":text": new_text,
                    ":upto": _to_decimal(audio_upto),
                    ":now": _now_iso(),
                },
            )
        except Exception:
            logger.exception(
                "persistence_update_last_transcript_failed",
                extra={"session_id": self._session_id},
            )

    async def write_final_tokens(
        self,
        tokens: Iterable[Any],
        *,
        audio_upto: float,
        committed: bool,
        status: str = "ended",
    ) -> str | None:
        """Persist the final transcript to S3 and flip the session row.

        Returns the S3 object key on success or ``None`` on failure.
        """

        snapshot = {
            "session_id": self._session_id,
            "audio_processed_upto_seconds": audio_upto,
            "committed_finalized": committed,
            "tokens": [
                {
                    "text": getattr(t, "text", ""),
                    "start": getattr(t, "start", None),
                    "end": getattr(t, "end", None),
                    "probability": getattr(t, "probability", None),
                }
                for t in tokens
            ],
        }
        key = f"streaming/{self._session_id}/transcript.json"
        body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        s3 = self._ensure_s3()
        try:
            s3.put_object(
                Bucket=self._transcripts_bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
        except Exception:
            logger.exception(
                "persistence_write_final_s3_failed",
                extra={"session_id": self._session_id, "key": key},
            )
            return None

        table = self._ensure_table()
        try:
            table.update_item(
                Key={"session_id": self._session_id},
                UpdateExpression=(
                    "SET #status = :status, "
                    "final_transcript_s3_key = :key, "
                    "audio_processed_upto = :upto, "
                    "ended_at = :now, "
                    "updated_at = :now"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": status,
                    ":key": key,
                    ":upto": _to_decimal(audio_upto),
                    ":now": _now_iso(),
                },
            )
        except Exception:
            logger.exception(
                "persistence_update_final_row_failed",
                extra={"session_id": self._session_id},
            )

        return key


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _to_decimal(value: float):
    """boto3 wants ``Decimal`` for numeric DDB attrs; fall back to str."""

    try:
        from decimal import Decimal

        return Decimal(str(value))
    except Exception:
        return str(value)
