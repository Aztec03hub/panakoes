"""Shared HS256 JWT validator for Panakoes Python services.

Every Python microservice that consumes tokens issued by the Auth
service imports this package instead of reimplementing JWT logic. The
library exposes a small, intentional surface:

- `JwtValidator`: the validator class, constructed explicitly.
- `from_env`: convenience constructor reading environment variables.
- `JwtClaims`: the validated token payload (Pydantic model).
- `JwtInvalidError`: raised for any token-level failure.
- `JwtConfigError`: raised when configuration is missing.
- `fastapi_dependency`: an optional helper for FastAPI services.

The FastAPI helper is opt-in via the `[fastapi]` extra; importing it
elsewhere does not require FastAPI to be installed.
"""

from panakoes_auth_client.claims import JwtClaims
from panakoes_auth_client.config import from_env
from panakoes_auth_client.errors import JwtConfigError, JwtInvalidError
from panakoes_auth_client.fastapi import fastapi_dependency
from panakoes_auth_client.validator import JwtValidator

__all__ = [
    "JwtClaims",
    "JwtConfigError",
    "JwtInvalidError",
    "JwtValidator",
    "fastapi_dependency",
    "from_env",
]
