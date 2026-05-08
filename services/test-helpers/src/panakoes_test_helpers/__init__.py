"""Shared pytest helpers for Panakoes services.

This package collects three families of test utilities every Python
service in the monorepo can import:

- `panakoes_test_helpers.jwt`: deterministic HS256 token builders that
  match the project's local-dev JWT conventions, plus an
  Authorization-header helper for httpx clients.
- `panakoes_test_helpers.aws`: pytest fixtures and context managers
  that spin up moto-backed S3 buckets, DynamoDB tables, and
  EventBridge custom buses with the project's standard hardening
  (versioning + public-access-block on S3, PAY_PER_REQUEST on DDB).
- `panakoes_test_helpers.factories`: factory functions that produce
  domain objects shaped like `panakoes_models` types. If the models
  library is installed, factories return real Pydantic instances;
  otherwise they degrade gracefully to plain dicts so this library
  has no hard dependency on the models package.

All defaults are documented in the per-module docstrings. Test secrets
(e.g. the default `"test-secret"` in `jwt.make_test_token`) are
explicitly documented as test-only and must never be reused in real
code paths; see CLAUDE.md "No Secrets, Ever".
"""

from panakoes_test_helpers.aws import (
    dynamodb_test_table,
    eventbridge_test_bus,
    s3_test_bucket,
)
from panakoes_test_helpers.factories import (
    make_ingestion_record,
    make_streaming_session,
    make_summary,
    make_user,
)
from panakoes_test_helpers.jwt import (
    bearer_header,
    make_expired_token,
    make_test_token,
)

__all__ = [
    "bearer_header",
    "dynamodb_test_table",
    "eventbridge_test_bus",
    "make_expired_token",
    "make_ingestion_record",
    "make_streaming_session",
    "make_summary",
    "make_test_token",
    "make_user",
    "s3_test_bucket",
]
