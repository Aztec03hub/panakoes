# panakoes-middleware

Reusable FastAPI middleware and decorators for Panakoes services. Every Python microservice imports this library to get the project-default rate limiter, CORS allowlist, request validation, correlation IDs, and the `@audit_event` decorator without re-implementing the boilerplate.

## Why this exists

The Panakoes microservices share the same edge concerns: throttle abusive clients, restrict cross-origin access to the public domains, reject oversize bodies before they touch RAM, propagate a stable request id through every log line and downstream call, and emit one audit event per handler invocation. Bundling these into one library keeps the rules consistent across services and turns "add cross-cutting concern X" into a one-line wire-up.

## Installation

Within the monorepo, declared as a path dependency in each consuming service's `pyproject.toml`:

```toml
[project]
dependencies = [
    "panakoes-middleware @ file:../middleware-lib",
]
```

For the optional Redis-backed rate-limit store:

```toml
[project]
dependencies = [
    "panakoes-middleware[redis] @ file:../middleware-lib",
]
```

## Quick start

```python
from fastapi import FastAPI
from panakoes_middleware import (
    CorrelationIdMiddleware,
    MaxRequestSizeMiddleware,
    RateLimitMiddleware,
    RequiredHeadersMiddleware,
    audit_event,
    from_env as cors_from_env,
)

app = FastAPI(middleware=[cors_from_env()])

app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(MaxRequestSizeMiddleware, max_bytes=10 * 1024 * 1024)
app.add_middleware(RequiredHeadersMiddleware, required=["X-Tenant-Id"])
app.add_middleware(RateLimitMiddleware, requests_per_minute=120)

@app.post("/widgets")
@audit_event(action="widget.created", source_service="widget-api")
async def create_widget(request: Request) -> Widget:
    ...
```

## Public API

### `panakoes_middleware.rate_limit`

- `RateLimitMiddleware(app, *, requests_per_minute=60, key_fn=None, store=None)`. Sliding-window limiter. Defaults: client-IP keying (preferring `X-Forwarded-For` first hop) and an in-process store. Returns 429 with `Retry-After` once the budget is exhausted.
- `InMemoryStore`: per-process deque per key. Dev only.
- `RedisStore(client)`: production store using a sorted set per key. Pass an async `redis.Redis` instance.

### `panakoes_middleware.cors`

- `make_cors_middleware(allowed_origins, allowed_methods=None, allow_credentials=True)`. Wraps Starlette's `CORSMiddleware` with project defaults (GET, POST, DELETE, OPTIONS).
- `from_env(env_var="CORS_ALLOWED_ORIGINS")`. Reads a comma-separated env var and forwards to `make_cors_middleware`.

### `panakoes_middleware.request_validation`

- `MaxRequestSizeMiddleware(app, max_bytes=10 * 1024 * 1024)`. Rejects requests with a `Content-Length` over the limit (413).
- `RequiredHeadersMiddleware(app, required)`. Rejects requests missing any header in the list (400).

### `panakoes_middleware.correlation`

- `CorrelationIdMiddleware(app)`. Reads or generates `X-Request-Id` (ULID), stashes it in a contextvar, mirrors it onto `request.state.request_id`, echoes the same id back in the response.
- `get_request_id() -> str`. Helper for handlers and downstream callers.
- `RequestIdLogFilter`. Logging filter that attaches `request_id` to every log record so format strings can reference `%(request_id)s`.

### `panakoes_middleware.audit`

- `@audit_event(action, source_service)`. Wraps a FastAPI handler. After the handler runs, emits an `AuditEvent` via `panakoes_audit.record_event` with `actor_id` resolved from `request.state.user.sub` (or `anonymous`), the configured `action`, the request method, path, and resolved status code. On exception: emits with `details["status"]="failed"` and re-raises so the framework's normal error path runs.

## Configuration (env vars)

| Variable | Used by | Notes |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | `cors.from_env()` | Comma-separated list |
| `AUDIT_*` | `panakoes_audit` | See `services/audit-lib/README.md` |

## Testing

```bash
uv sync --group dev
uv run ruff check
uv run mypy src
uv run pytest --cov-fail-under=90
```

## Coverage

90% per ADR-018 application-services tier.
