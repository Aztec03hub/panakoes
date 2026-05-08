# panakoes-auth-client

Shared HS256 JWT validator for Panakoes Python microservices. Every service that needs to verify a token issued by the Auth service imports this library instead of reimplementing JWT logic.

## Why this exists

The Auth service (TypeScript + Better-Auth) signs HS256 access tokens with a shared secret, an issuer, and an audience. Several Python services (Ingestion API, Session Manager, future analytics endpoints) need to validate those tokens. Reimplementing the validation logic per service is how subtle security bugs ship: one service forgets to check `aud`, another logs the failure reason back to the caller, another mishandles malformed bearer headers. Centralising the logic in a small library that lives at the 100%-coverage tier (per ADR-018) eliminates that drift.

## Installation

Within the monorepo, declared as a path dependency in each consuming service's `pyproject.toml`:

```toml
[project]
dependencies = [
    "panakoes-auth-client @ file:../auth-client",
]

[tool.uv.sources]
panakoes-auth-client = { path = "../auth-client", editable = true }
```

For services that want the FastAPI dependency, install the `fastapi` extra:

```toml
dependencies = [
    "panakoes-auth-client[fastapi] @ file:../auth-client",
]
```

## Quick start

```python
from panakoes_auth_client import JwtValidator

validator = JwtValidator(
    secret="...",
    issuer="https://auth.panakoes.com",
    audience="panakoes-api",
)
claims = validator.validate(token)  # returns JwtClaims (Pydantic model)
```

Or, in production, read configuration from the environment:

```python
from panakoes_auth_client import from_env

validator = from_env()
```

## FastAPI integration

```python
from fastapi import Depends, FastAPI
from panakoes_auth_client import JwtClaims, fastapi_dependency, from_env

app = FastAPI()
get_current_claims = fastapi_dependency(from_env())

@app.get("/me")
def me(claims: JwtClaims = Depends(get_current_claims)) -> dict[str, str]:
    return {"user_id": claims.sub}
```

The dependency reads the bearer token from the `Authorization` header, validates it via the supplied `JwtValidator`, and returns the parsed `JwtClaims`. Any failure (missing header, malformed bearer, expired, bad signature, wrong issuer/audience, missing claim) raises `HTTPException(401)` with `WWW-Authenticate: Bearer`. The underlying error is never echoed to the client.

## Public API

```python
from panakoes_auth_client import (
    JwtClaims,
    JwtValidator,
    JwtInvalidError,
    JwtConfigError,
    fastapi_dependency,
    from_env,
)
```

### `JwtClaims` (Pydantic model)

| Field | Type | Notes |
|---|---|---|
| `sub` | `str` | Subject; the authenticated user id |
| `iss` | `str` | Issuer claim, validated against the configured issuer |
| `aud` | `str` | Audience claim, validated against the configured audience |
| `iat` | `int` | Issued-at, seconds since epoch |
| `exp` | `int` | Expires-at, seconds since epoch |
| `jti` | `str \| None` | Optional token id |
| `scopes` | `list[str]` | Default empty list |

### `JwtValidator`

```python
JwtValidator(
    secret: str,
    issuer: str,
    audience: str,
    algorithms: list[str] = ["HS256"],
)
```

`validator.validate(token: str) -> JwtClaims` raises `JwtInvalidError` for any failure: expired, bad signature, wrong issuer or audience, malformed token, missing required claim.

### `from_env()`

Convenience constructor reading `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`. Raises `JwtConfigError` if any is missing or empty.

### `fastapi_dependency(validator)`

Returns a callable suitable for `Depends(...)`. Extracts the bearer token from the `Authorization` header, validates, returns `JwtClaims`. Maps every failure to `HTTPException(401)` with `WWW-Authenticate: Bearer`.

## Testing

```bash
uv sync --group dev
uv run ruff check
uv run mypy src
uv run pytest --cov-fail-under=100
```

Tests use `freezegun` for time-controlled scenarios (expired tokens, future tokens).

## Coverage

100% per ADR-018 (auth code is security-critical).
