# panakoes-audit

Structured audit-event library for Panakoes services. Every Python microservice imports this library to record audit events into a centralized, queryable store.

## Why this exists

ADR-017 (Audit trail) commits Panakoes to a DynamoDB-backed audit log alongside AWS CloudTrail. CloudTrail covers AWS-API-level operations; this library covers application-level events ("user X created transcript Y", "service Z ran summarization on resource W"). Together they form complete coverage.

ADR-018 puts audit code at the 100%-coverage tier, alongside auth and billing. This library carries that gate.

## Installation

Within the monorepo, declared as a path dependency in each consuming service's `pyproject.toml`:

```toml
[project]
dependencies = [
    "panakoes-audit @ file:../audit-lib",
]
```

## Quick start

```python
from panakoes_audit import record_event

await record_event(
    actor_id="user_abc",
    actor_type="user",
    action="transcript.created",
    resource_type="transcript",
    resource_id="trnscr_xyz",
    source_service="ingestion",
    request_id="req_def",
    details={"audio_duration_seconds": 245},
    source_ip="203.0.113.42",
)
```

`record_event` lazily acquires the configured backend (`AUDIT_BACKEND` env var) on first call and reuses it thereafter.

## Public API

```python
from panakoes_audit import (
    AuditEvent,
    AuditStore,
    DynamoDBAuditStore,
    MemoryAuditStore,
    StdoutAuditStore,
    record_event,
    set_store,
    reset_store,
)
```

### `AuditEvent` (Pydantic model)

| Field | Type | Notes |
|---|---|---|
| `timestamp` | `datetime` | UTC; defaults to `datetime.now(UTC)`; must be past or present |
| `actor_id` | `str` | non-empty |
| `actor_type` | `Literal["user", "service", "system", "anonymous"]` | |
| `action` | `str` | regex `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$` |
| `resource_type` | `str` | non-empty |
| `resource_id` | `str` | non-empty |
| `source_service` | `str` | non-empty |
| `request_id` | `str` | non-empty; auto-generated UUID4 if missing |
| `details` | `dict[str, Any]` | default `{}` |
| `source_ip` | `str \| None` | optional |

### Backends

- `MemoryAuditStore`: in-memory `list`; exposes `events` for assertions; `clear()` to reset.
- `StdoutAuditStore`: emits one-line JSON via `print()`. Suitable for local dev where CloudWatch picks up stdout.
- `DynamoDBAuditStore`: writes to the configured DynamoDB table.

DynamoDB schema:

```
pk = "AUDIT#" + source_service + "#" + actor_id
sk = timestamp_iso + "#" + request_id
```

All other fields are stored as top-level attributes for direct attribute-level queries via Global Secondary Indexes (defined in Terraform, not here).

### Configuration (env vars, via `pydantic-settings`)

| Variable | Default | Notes |
|---|---|---|
| `AUDIT_BACKEND` | `stdout` | `dynamodb` \| `stdout` \| `memory` |
| `AUDIT_TABLE_NAME` | `panakoes-audit-log` | DynamoDB table name |
| `AUDIT_AWS_REGION` | `us-east-1` | AWS region for the boto3 client |

## Testing

```bash
uv sync --group dev
uv run ruff check
uv run mypy src
uv run pytest --cov-fail-under=100
```

Integration tests use `moto`'s `mock_aws` to spin up an in-memory DynamoDB; no live AWS required.

## Coverage

100% per ADR-018. The `--cov-fail-under=100` gate is wired into `pyproject.toml`.
