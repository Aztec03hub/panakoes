"""`JwtValidator` class: the core HS256 validation logic.

Wraps `python-jose` so consuming services do not need to import jose
directly or remember to pass issuer/audience on every call. Every
failure mode (expired, bad signature, wrong issuer, wrong audience,
malformed, missing claim) maps to a single `JwtInvalidError`. The
underlying `jose` exception is chained for debugging but never echoed
to clients.
"""

from __future__ import annotations

from typing import Any

from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import ValidationError

from panakoes_auth_client.claims import JwtClaims
from panakoes_auth_client.errors import JwtInvalidError


class JwtValidator:
    """Validate HS256 JWTs issued by the Panakoes Auth service.

    Construction is cheap; reuse a single instance per process. The
    instance is thread-safe because every call to `validate` is
    self-contained (no mutable state).
    """

    def __init__(
        self,
        secret: str,
        issuer: str,
        audience: str,
        algorithms: list[str] | None = None,
    ) -> None:
        """Initialize the validator with the shared signing material.

        `algorithms` defaults to `["HS256"]` to match what the Auth
        service issues today. We accept it as a parameter so future
        rotation to RS256/JWKS can land without changing call sites.
        """
        if algorithms is None:
            algorithms = ["HS256"]
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms

    def validate(self, token: str) -> JwtClaims:
        """Validate `token` and return the parsed claims.

        Raises `JwtInvalidError` on any failure: expired, bad
        signature, wrong issuer, wrong audience, malformed token,
        or missing/wrong-typed claim. The original exception is
        chained via `__cause__` so it appears in tracebacks but is
        never embedded in the error message handed back to clients.
        """
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
            )
        except ExpiredSignatureError as exc:
            raise JwtInvalidError("token expired") from exc
        except JWTError as exc:
            raise JwtInvalidError("invalid token") from exc

        try:
            return JwtClaims.model_validate(payload)
        except ValidationError as exc:
            raise JwtInvalidError("invalid token claims") from exc
