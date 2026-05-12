"""DynamoDB streaming-sessions row update helpers.

The transcriber-batch job updates a single row in the
``panakoes-dev-streaming-sessions`` table on every meaningful state
transition: ``transcribing`` when the job picks up the work,
``completed`` on a clean run (with ``duration_seconds``, ``word_count``,
and ``transcript_uri``), and ``errored`` on an unrecoverable failure.

Schema for the table is owned by ``services/session-manager`` (see
``panakoes_models.StreamingSession``); this module only writes fields
that schema permits. The primary key is ``id``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def mark_transcribing(ddb_table: Any, session_id: str) -> None:
    """Mark the session row as in-progress.

    Idempotent: re-applying the same update on a row that is already in
    a later state (``completed`` or ``errored``) does NOT roll the row
    backwards because the conditional only checks existence. Callers
    that need strict ordering should layer a condition expression on
    ``status`` above this.
    """
    _update_status(ddb_table, session_id, status="active", extra={})


def mark_completed(
    ddb_table: Any,
    session_id: str,
    *,
    transcript_uri: str,
    duration_seconds: float,
    word_count: int,
) -> None:
    """Mark the session row as completed with the final metrics.

    ``transcript_uri`` is the full ``s3://bucket/key`` URI of the
    canonical ``transcript.json`` written by :func:`s3.upload_transcript_json`.
    """
    _update_status(
        ddb_table,
        session_id,
        status="completed",
        extra={
            "transcript_uri": transcript_uri,
            "duration_seconds": _as_ddb_number(duration_seconds),
            "word_count": word_count,
        },
    )


def mark_errored(ddb_table: Any, session_id: str, *, error_message: str) -> None:
    """Mark the session row as errored with a short error message.

    The message is truncated to 500 chars so a misbehaving exception
    repr cannot blow past DDB's 400 KB item limit on the row.
    """
    truncated = error_message[:500]
    _update_status(
        ddb_table,
        session_id,
        status="errored",
        extra={"error_message": truncated},
    )


def _update_status(
    ddb_table: Any,
    session_id: str,
    *,
    status: str,
    extra: dict[str, Any],
) -> None:
    """Issue a single ``UpdateItem`` setting ``status``, ``updated_at``, and extras.

    Built as one call (not three) so the row stays consistent for any
    reader concurrent with our update.
    """
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    set_clauses: list[str] = ["#status = :status", "#updated_at = :updated_at"]
    expression_names: dict[str, str] = {"#status": "status", "#updated_at": "updated_at"}
    expression_values: dict[str, Any] = {":status": status, ":updated_at": now}

    for index, (field, value) in enumerate(extra.items()):
        name_placeholder = f"#f{index}"
        value_placeholder = f":v{index}"
        set_clauses.append(f"{name_placeholder} = {value_placeholder}")
        expression_names[name_placeholder] = field
        expression_values[value_placeholder] = value

    ddb_table.update_item(
        Key={"id": session_id},
        UpdateExpression="SET " + ", ".join(set_clauses),
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )


def _as_ddb_number(value: float) -> Any:
    """Convert a Python float to a DDB-safe Decimal.

    boto3's resource API rejects float values for Number attributes
    because of fp-precision concerns; the documented workaround is to
    cast through Decimal via ``str()`` to preserve the visible
    representation rather than the underlying binary fraction.
    """
    from decimal import Decimal

    return Decimal(str(value))
