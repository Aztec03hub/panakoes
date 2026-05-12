"""Reusable FastAPI middleware and decorators for Panakoes services.

The library bundles the cross-cutting HTTP concerns every Panakoes
service shares: rate limiting, CORS, request validation, correlation
IDs, and audit-event emission. Each submodule exports a small surface
that consuming services can wire into their FastAPI apps without
re-implementing boilerplate.

Submodules:

- `panakoes_middleware.rate_limit`: sliding-window rate limiter with
  pluggable stores (in-memory for dev, Redis for prod).
- `panakoes_middleware.cors`: opinionated wrapper around Starlette's
  CORS middleware with safe defaults and env-driven origins.
- `panakoes_middleware.request_validation`: max-body-size and
  required-headers gates that reject bad requests at the edge.
- `panakoes_middleware.correlation`: `X-Request-Id` propagation via
  contextvar plus a logging filter, so logs join up across hops.
- `panakoes_middleware.audit`: `@audit_event` decorator that emits a
  structured audit event after a route handler runs (success or fail).
"""

from panakoes_middleware.audit import audit_event
from panakoes_middleware.correlation import (
    CorrelationIdMiddleware,
    RequestIdLogFilter,
    get_request_id,
)
from panakoes_middleware.cors import from_env, make_cors_middleware
from panakoes_middleware.plan_gating import Plan, require_plan
from panakoes_middleware.rate_limit import (
    InMemoryStore,
    RateLimitMiddleware,
    RateLimitStore,
    RedisStore,
)
from panakoes_middleware.request_validation import (
    MaxRequestSizeMiddleware,
    RequiredHeadersMiddleware,
)

__all__ = [
    "CorrelationIdMiddleware",
    "InMemoryStore",
    "MaxRequestSizeMiddleware",
    "Plan",
    "RateLimitMiddleware",
    "RateLimitStore",
    "RedisStore",
    "RequestIdLogFilter",
    "RequiredHeadersMiddleware",
    "audit_event",
    "from_env",
    "get_request_id",
    "make_cors_middleware",
    "require_plan",
]
