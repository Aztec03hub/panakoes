"""Ingestion-mode helpers for the transcriber-batch worker.

The streaming-sessions update path (``sessions.py``) was the original
design target: each Batch job corresponded to a streaming session row
in ``panakoes-dev-streaming-sessions``. The async file-upload demo
path needs a different row update: the ``panakoes-dev-ingestion``
table rows that ``services/ingestion-api`` writes when a user requests
a pre-signed upload URL.

This module mirrors the shape of ``sessions.py`` but writes to the
ingestion table. The Pydantic model schema is owned by
``services/ingestion-api/src/panakoes_ingestion_api/models.py``; this
module only updates ``status``, ``transcript_status``, ``transcript``,
and ``transcript_error_message`` fields per that contract.

Why separate modules: the ingestion table uses a composite primary key
``(user_id, ingestion_id)`` while the streaming-sessions table uses a
simple ``id`` key. Same kind of write (UpdateItem with conditional
status field), different key shape and field set. Keeping them in
sibling modules makes the two paths explicit; ``main.py`` dispatches
to one or the other based on the ``TARGET_MODE`` env var.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


def _user_pk(user_id: str) -> str:
    """Compose the partition key the ingestion-api uses (``USER#<id>``)."""
    return f"USER#{user_id}"


def _ingestion_sk(ingestion_id: str) -> str:
    """Compose the sort key the ingestion-api uses (``INGESTION#<id>``)."""
    return f"INGESTION#{ingestion_id}"


def mark_uploaded(ddb_table: Any, *, user_id: str, ingestion_id: str) -> None:
    """Flip ``status`` to ``uploaded`` + ``transcript_status`` to ``pending``.

    Called immediately on Batch-job pickup so a re-delivery (visibility
    timeout, retry) does not race a concurrent worker into the same
    "still pending" state.
    """
    now = datetime.now(UTC).isoformat()
    ddb_table.update_item(
        Key={"pk": _user_pk(user_id), "sk": _ingestion_sk(ingestion_id)},
        UpdateExpression="SET #s = :uploaded, #ts = :pending, #ua = :ua",
        ExpressionAttributeNames={
            "#s": "status",
            "#ts": "transcript_status",
            "#ua": "updated_at",
        },
        ExpressionAttributeValues={
            ":uploaded": "uploaded",
            ":pending": "pending",
            ":ua": now,
        },
    )


def mark_succeeded(
    ddb_table: Any,
    *,
    user_id: str,
    ingestion_id: str,
    transcript: dict[str, Any],
) -> None:
    """Persist a successful transcript onto the ingestion row.

    ``transcript`` is the canonical Panakoes transcript dict produced
    by ``panakoes_transcriber_batch.transcribe.transcribe``. Floats in
    the segments + duration are coerced to ``Decimal`` because DDB
    rejects native floats in number attributes.
    """
    now = datetime.now(UTC).isoformat()
    transcript_decimal = _floats_to_decimal(transcript)
    ddb_table.update_item(
        Key={"pk": _user_pk(user_id), "sk": _ingestion_sk(ingestion_id)},
        UpdateExpression=(
            "SET #ts = :succeeded, #t = :transcript, #ua = :ua REMOVE #tem"
        ),
        ExpressionAttributeNames={
            "#ts": "transcript_status",
            "#t": "transcript",
            "#ua": "updated_at",
            "#tem": "transcript_error_message",
        },
        ExpressionAttributeValues={
            ":succeeded": "succeeded",
            ":transcript": transcript_decimal,
            ":ua": now,
        },
    )


def mark_failed(
    ddb_table: Any,
    *,
    user_id: str,
    ingestion_id: str,
    error_message: str,
) -> None:
    """Flip ``transcript_status`` to ``failed`` with a short error message.

    The message is truncated to 500 chars so a misbehaving exception
    repr cannot blow past DDB's 400 KB item limit on the row.
    """
    now = datetime.now(UTC).isoformat()
    truncated = error_message[:500]
    ddb_table.update_item(
        Key={"pk": _user_pk(user_id), "sk": _ingestion_sk(ingestion_id)},
        UpdateExpression="SET #ts = :failed, #tem = :tem, #ua = :ua",
        ExpressionAttributeNames={
            "#ts": "transcript_status",
            "#tem": "transcript_error_message",
            "#ua": "updated_at",
        },
        ExpressionAttributeValues={
            ":failed": "failed",
            ":tem": truncated,
            ":ua": now,
        },
    )


def _floats_to_decimal(value: Any) -> Any:
    """Recursively coerce native ``float`` to ``Decimal`` for DDB safety.

    boto3's DynamoDB.Table interface rejects native ``float`` values
    because IEEE-754 cannot represent every DDB number exactly. The
    standard fix is to convert via ``Decimal(str(...))`` which uses
    the float's repr to avoid binary-to-decimal round-trip drift.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_floats_to_decimal(v) for v in value]
    return value
