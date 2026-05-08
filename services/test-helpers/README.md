# panakoes-test-helpers

Shared pytest helpers for Panakoes services. Three submodules: JWT
builders, moto-backed AWS resource fixtures, and domain-object
factories. Every Python service in the monorepo imports from this
library so test fixtures stay aligned across services.

## Why this exists

Three services already had near-identical `conftest.py` blocks for the
same three concerns: forging HS256 JWTs, spinning up moto-backed S3
buckets and DynamoDB tables, and fabricating domain objects. The copies
drifted (different issuer strings, missing public-access-block on S3,
missing GSI declarations) and each drift caused a real bug.

This library is the single source of truth. Update once here, every
service inherits the fix.

## Installation

Within the monorepo, declared as a path dependency in each consuming
service's `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "panakoes-test-helpers @ file:../test-helpers",
]
```

`panakoes-models` is an optional peer: if it is installed in the
consuming environment, the factory helpers return real Pydantic
instances; if not, they return plain dicts with the same shape.

## Public API

```python
from panakoes_test_helpers import (
    # JWT
    make_test_token,
    make_expired_token,
    bearer_header,
    # AWS
    s3_test_bucket,
    dynamodb_test_table,
    eventbridge_test_bus,
    # Factories
    make_user,
    make_ingestion_record,
    make_summary,
    make_streaming_session,
)
```

## JWT helpers

```python
from panakoes_test_helpers import make_test_token, bearer_header

token = make_test_token("user_abc")
headers = bearer_header(token)
# {"Authorization": "Bearer eyJ..."}
```

| Parameter | Default | Notes |
|---|---|---|
| `secret` | `"test-secret"` | Documented test-only string |
| `issuer` | `"panakoes-auth"` | Local-dev `iss` convention |
| `audience` | `"panakoes-api"` | Local-dev `aud` convention |
| `expires_in_seconds` | `3600` | One-hour validity |
| `scopes` | `None` | Omitted from payload when empty / None |

For 401-on-expiry tests, use `make_expired_token(sub, expired_seconds_ago=120)`.

## AWS helpers

Each helper is a context manager that activates `moto.mock_aws`,
provisions a hardened resource, yields the handle, and cleans up on
exit. They double as fixture bodies.

```python
from panakoes_test_helpers import s3_test_bucket, dynamodb_test_table, eventbridge_test_bus

def test_uploads_audio() -> None:
    with s3_test_bucket("panakoes-audio-test") as name:
        # bucket has versioning enabled and public access fully blocked
        ...

def test_writes_ingestion_record() -> None:
    schema = [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]
    with dynamodb_test_table("panakoes-ingestion-test", schema) as table:
        # table is PAY_PER_REQUEST; ready to put_item
        ...

def test_publishes_event() -> None:
    with eventbridge_test_bus("panakoes-events-test") as bus_name:
        # custom bus exists; ready for put_events
        ...
```

`dynamodb_test_table` accepts an optional `gsi=[...]` list of GSI
definitions. The helper derives `AttributeDefinitions` automatically
from the key schemas.

## Factory helpers

Hand-rolled factories that produce domain objects with sensible
defaults. Override any field via keyword.

```python
from panakoes_test_helpers import (
    make_user,
    make_ingestion_record,
    make_summary,
    make_streaming_session,
)

user = make_user(tier="pro")
ingestion = make_ingestion_record(user_id=user.id if hasattr(user, "id") else user["id"])
summary = make_summary(tier="sonnet", prompt_tokens=10_000)
session = make_streaming_session(status="active")
```

| Factory | Default tier / status | Default identifier prefix |
|---|---|---|
| `make_user` | `tier="free"`, `role="user"` | `user_` |
| `make_ingestion_record` | `status="pending"` | `ingest_` |
| `make_summary` | `tier="haiku"` | `sum_` |
| `make_streaming_session` | `status="starting"` | `sess_` |

## No real secrets, ever

The default `secret="test-secret"` in `make_test_token` is a documented
test-only string. It is never read from production configuration; ruff's
bandit `S105` rule is suppressed only on `panakoes_test_helpers.jwt` via
`pyproject.toml`. See CLAUDE.md "No Secrets, Ever".

## Testing

```bash
uv sync --group dev
uv run ruff check
uv run mypy src
uv run pytest --cov-fail-under=90
```

## Coverage

90% per ADR-014 (general service tier). The audit / auth / billing
libraries enforce 100%; this is a test helper, not a security path.
