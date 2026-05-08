"""Shared Pydantic models for Panakoes service contracts.

`panakoes-models` is the canonical home for every cross-service domain
type in Panakoes. Each Python service imports the models it needs
instead of defining its own; the library is the single source of truth
for field shapes, identifier patterns, and validation rules.

Modules:

- `users`: `User`, `UserId`, role + tier literals.
- `ingestion`: `IngestionRecord`, `IngestionId`, `IngestionStatus`.
- `transcripts`: `Transcript`, `TranscriptSegment`, `TranscriptId`.
- `summaries`: `Summary`, `SummaryId`, `SummaryTier`.
- `sessions`: `StreamingSession`, `SessionId`, `SessionStatus`.
- `notifications`: `NotificationRecord`, `NotificationId`,
  `NotificationKind`, `NotificationStatus`.
- `billing`: `Subscription`, `BillingTier` (slice-1 skeletons).
- `errors`: `ApiError`.

Every model uses `frozen=True` and `extra="forbid"`, so an unknown
field at construction time raises `ValidationError`. This is
intentional: the library is a contract, and silent acceptance of
unknown fields would make breaking changes invisible.
"""

from panakoes_models.billing import BillingTier, Subscription
from panakoes_models.errors import ApiError
from panakoes_models.ingestion import (
    MAX_INGESTION_SIZE_BYTES,
    IngestionId,
    IngestionRecord,
    IngestionStatus,
)
from panakoes_models.notifications import (
    NotificationId,
    NotificationKind,
    NotificationRecord,
    NotificationStatus,
)
from panakoes_models.sessions import SessionId, SessionStatus, StreamingSession
from panakoes_models.summaries import Summary, SummaryId, SummaryTier
from panakoes_models.transcripts import Transcript, TranscriptId, TranscriptSegment
from panakoes_models.users import User, UserId, UserRole, UserTier

__all__ = [
    "MAX_INGESTION_SIZE_BYTES",
    "ApiError",
    "BillingTier",
    "IngestionId",
    "IngestionRecord",
    "IngestionStatus",
    "NotificationId",
    "NotificationKind",
    "NotificationRecord",
    "NotificationStatus",
    "SessionId",
    "SessionStatus",
    "StreamingSession",
    "Subscription",
    "Summary",
    "SummaryId",
    "SummaryTier",
    "Transcript",
    "TranscriptId",
    "TranscriptSegment",
    "User",
    "UserId",
    "UserRole",
    "UserTier",
]
