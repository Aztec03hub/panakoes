"""Tier 3.3 audit-log read view.

Read-only HTTP surface that lets an admin operator inspect the Tier 3
lifecycle audit trail. Backed by the `Tier3ActionIndex` GSI on
`panakoes-dev-audit-log` (sparse: only events with a `tier3_action`
attribute appear, which by convention only the admin-api lifecycle
operations populate).

Auth: `require_admin` (read-only Tier 3 surface; per ADR-032 step-up
MFA is reserved for state-changing operations). Admin role is still
required because audit content includes operator identifiers + target
identifiers that the cost-api / dashboard surfaces should not expose.

Pagination: opaque base64-encoded JSON cursor wrapping DynamoDB's
`LastEvaluatedKey`. Callers treat it as a string; the server is the
only thing that decodes it. Limit defaults to 25 and is capped at 100
so a buggy client cannot accidentally force a multi-megabyte response.

Filter: `tier3_action` (optional) selects exactly one Tier 3 action
type and uses a `Query` against the GSI hash key. When omitted we fall
back to a `Scan` against the same GSI; that is acceptable for v0.1
because the index is sparse and Tier 3 events are bounded (operator
mistakes are not a high-volume event source). If volumes grow we can
revisit by adding a synthetic "all-tier3" hash-key value at write
time, but that is a write-side change we do not need yet.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, Depends, HTTPException, Query, status
from panakoes_auth_client import JwtClaims
from pydantic import BaseModel, ConfigDict, Field

from panakoes_admin_api.auth import require_admin
from panakoes_admin_api.dependencies import get_audit_table

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["audit-log"])

DEFAULT_LIMIT = 25
MAX_LIMIT = 100
GSI_NAME = "Tier3ActionIndex"


class AuditLogEntry(BaseModel):
    """One row of the Tier 3 audit log surfaced to the dashboard.

    Mirrors `services/admin/src/lib/types.ts:AuditLogEntry`. The
    `payload` field captures every audit-row attribute the read view
    does not lift to a top-level field, so the UI can render targets,
    reasons, outcomes, and error messages without a schema change for
    each new operation.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: str
    timestamp: str
    source_service: str
    actor_id: str
    action: str
    tier3_action: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditLogPage(BaseModel):
    """Response envelope for `GET /api/v1/admin/audit-log`."""

    model_config = ConfigDict(extra="forbid")

    entries: list[AuditLogEntry]
    next_cursor: str | None = None
    generated_at: str


_TOP_LEVEL_FIELDS = frozenset(
    {"request_id", "timestamp", "source_service", "actor_id", "action", "tier3_action"}
)


def _row_to_entry(row: Mapping[str, Any]) -> AuditLogEntry:
    """Lift a raw DynamoDB row into the typed `AuditLogEntry`.

    Top-level fields are picked out individually with safe fallbacks
    (older audit rows pre-Tier-3 may be missing `request_id`; we fall
    back to the `sk` which always carries it). Everything else is
    bundled into `payload` so the UI can render it generically.
    """
    payload: dict[str, Any] = {
        k: v for k, v in row.items() if k not in _TOP_LEVEL_FIELDS and k not in {"pk", "sk"}
    }
    request_id = str(row.get("request_id") or row.get("sk", "")).split("#", 1)[0]
    return AuditLogEntry(
        request_id=request_id,
        timestamp=str(row.get("timestamp", "")),
        source_service=str(row.get("source_service", "")),
        actor_id=str(row.get("actor_id", "")),
        action=str(row.get("action", "")),
        tier3_action=(str(row["tier3_action"]) if row.get("tier3_action") is not None else None),
        payload=payload,
    )


def _encode_cursor(last_evaluated_key: Mapping[str, Any] | None) -> str | None:
    """Serialize DynamoDB's LastEvaluatedKey into an opaque base64 string."""
    if not last_evaluated_key:
        return None
    raw = json.dumps(dict(last_evaluated_key), separators=(",", ":"), sort_keys=True, default=str)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    """Decode an opaque cursor back into a LastEvaluatedKey dict.

    Malformed cursors raise a 400; the dashboard should never produce
    one, but a curious operator pasting a stale URL into a fresh
    deployment shouldn't see a 500.
    """
    if cursor is None or cursor == "":
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        decoded = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="malformed cursor",
        ) from exc
    if not isinstance(decoded, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="malformed cursor",
        )
    return decoded


@router.get("/audit-log", response_model=AuditLogPage)
async def read_audit_log(
    claims: Annotated[JwtClaims, Depends(require_admin)],
    audit_table: Annotated[Any, Depends(get_audit_table)],
    tier3_action: Annotated[
        str | None,
        Query(
            description=(
                "Exact-match filter on the Tier3ActionIndex hash key "
                "(e.g. 'terminate-session'). Omit to scan the full GSI."
            ),
            min_length=1,
            max_length=128,
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(description="Opaque pagination cursor returned in the previous response."),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_LIMIT, description="Maximum entries per page."),
    ] = DEFAULT_LIMIT,
) -> AuditLogPage:
    """Return a paginated page of Tier 3 audit-log entries.

    When `tier3_action` is set, issues a `Query` on the GSI hash key
    so DynamoDB returns rows in chronological order (range key `sk`
    is the timestamp + request_id). When omitted, falls back to a
    `Scan` against the same GSI; the GSI is sparse so the scan only
    sees Tier 3 events. The full event payload is in the projected
    GSI (projection ALL), so no follow-up GetItem is ever needed.
    """
    last_key = _decode_cursor(cursor)

    query_kwargs: dict[str, Any] = {
        "IndexName": GSI_NAME,
        "Limit": limit,
    }
    if last_key is not None:
        query_kwargs["ExclusiveStartKey"] = last_key

    if tier3_action is not None:
        query_kwargs["KeyConditionExpression"] = Key("tier3_action").eq(tier3_action)
        # Default DynamoDB Query order is ascending on the range key (sk),
        # which here is the ISO timestamp; chronological asc reads naturally
        # in an audit log ("here is what happened, in order").
        response = audit_table.query(**query_kwargs)
    else:
        response = audit_table.scan(**query_kwargs)

    items: list[Mapping[str, Any]] = response.get("Items", [])
    entries = [_row_to_entry(row) for row in items]
    next_cursor = _encode_cursor(response.get("LastEvaluatedKey"))

    logger.info(
        "audit_log_read",
        actor=claims.sub,
        tier3_action=tier3_action,
        returned=len(entries),
        has_next=next_cursor is not None,
    )

    return AuditLogPage(
        entries=entries,
        next_cursor=next_cursor,
        generated_at=datetime.now(UTC).isoformat(),
    )
