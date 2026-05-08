# panakoes-models

Shared Pydantic v2 models that every Panakoes Python service imports for typed cross-service contracts. The library is the single source of truth for the shape of every domain object: users, ingestions, transcripts, summaries, streaming sessions, notifications, subscriptions, and the API error envelope.

## Public API

```python
from panakoes_models import (
    # users
    User, UserId, UserRole, UserTier,
    # ingestion
    IngestionRecord, IngestionId, IngestionStatus, MAX_INGESTION_SIZE_BYTES,
    # transcripts
    Transcript, TranscriptSegment, TranscriptId,
    # summaries
    Summary, SummaryId, SummaryTier,
    # sessions
    StreamingSession, SessionId, SessionStatus,
    # notifications
    NotificationRecord, NotificationId, NotificationKind, NotificationStatus,
    # billing (slice-1 skeleton)
    Subscription, BillingTier,
    # errors
    ApiError,
)
```

### Identifier patterns

| Type | Regex |
|---|---|
| `UserId` | `^user_[a-zA-Z0-9]{12,}$` |
| `IngestionId` | `^ingest_[a-zA-Z0-9]{16,}$` |
| `TranscriptId` | `^tx_[a-zA-Z0-9]{16,}$` |
| `SummaryId` | `^sum_[a-zA-Z0-9]{16,}$` |
| `SessionId` | `^sess_[a-zA-Z0-9]{16,}$` |
| `NotificationId` | `^[0-9A-HJKMNP-TV-Z]{26}$` (ULID-shape) |

### Timestamps

All `datetime` fields require timezone-aware UTC values. Naive datetimes and non-UTC offsets are rejected at construction. JSON serialization round-trips cleanly via `model_dump_json` / `model_validate_json`.

### Status enums

- `IngestionStatus`: `pending`, `uploaded`, `transcribing`, `transcribed`, `summarizing`, `summarized`, `failed`.
- `SessionStatus`: `starting`, `active`, `paused`, `completed`, `errored`.
- `NotificationKind`: `email`, `webhook`, `sms`. `NotificationStatus`: `queued`, `sent`, `failed`.
- `SummaryTier`: `haiku`, `sonnet`. `BillingTier`: `free`, `pro`, `team`.

### Model contract guarantees

Every model carries `frozen=True` (immutable across service boundaries), `extra="forbid"` (additive changes are loud), and `str_strip_whitespace=True` (callers can stop sprinkling `.strip()` everywhere). To produce a "modified" copy, use `record.model_copy(update={...})`.

## Installation

Within the monorepo, declared as a path dependency in each consuming service's `pyproject.toml`:

```toml
[project]
dependencies = [
    "panakoes-models @ file:../models-lib",
]
```

## Usage examples

**Construct an ingestion record:**

```python
from datetime import UTC, datetime

from panakoes_models import IngestionRecord, IngestionStatus

record = IngestionRecord(
    id="ingest_abcd1234efgh5678",
    user_id="user_abcdefghijkl",
    filename="meeting.mp3",
    content_type="audio/mpeg",
    size_bytes=4_200_000,
    status=IngestionStatus.PENDING,
    created_at=datetime.now(UTC),
    updated_at=datetime.now(UTC),
    s3_key="ingestions/user_abcdefghijkl/ingest_abcd1234efgh5678.mp3",
    error=None,
)

print(record.model_dump_json())
```

**Round-trip through JSON across a service boundary:**

```python
payload = record.model_dump_json()
# ... payload travels over HTTP / SQS / EventBridge ...
parsed = IngestionRecord.model_validate_json(payload)
assert parsed == record
```

**Produce a "modified" copy without mutating the original:**

```python
uploaded = record.model_copy(
    update={"status": IngestionStatus.UPLOADED, "updated_at": datetime.now(UTC)}
)
```

## Coverage requirement

95% per ADR-018. The `--cov-fail-under=95` gate is wired into `pyproject.toml`; CI fails the PR below threshold.

```bash
uv sync --group dev
uv run ruff check
uv run mypy src
uv run pytest
```

## Architecture notes

Before this library, each service defined its own `IngestionRecord`-shaped class. That works for one service but breaks the moment another service needs to read or write the same data: any field rename in one service silently diverges from the others, and unit tests pass on both sides while production fails.

Centralizing the shapes here makes contracts explicit:

- A field rename is a breaking change to a single import, surfaced at type-check time across every consumer.
- `extra="forbid"` makes additive changes loud (downstream services must opt in).
- `frozen=True` keeps domain objects immutable across service boundaries; mutation is intentionally not how Panakoes propagates state.
- `str_strip_whitespace=True` normalizes input at construction so callers can stop sprinkling `.strip()` everywhere.

**Slice-2 follow-ups:**

- Flesh out `billing` (invoices, line items, usage meters).
- Add a `WebhookEvent` envelope once the webhook service spec settles.
- Consider per-tier capacity caps as `Annotated` constraints once the pricing-page numbers stabilize.
