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
from panakoes_auth_client.jwks import (
    DEFAULT_JWKS_CACHE_TTL_SECONDS,
    JwksCache,
    JwksFetcher,
)


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

    @classmethod
    def from_jwks_url(
        cls,
        url: str,
        *,
        issuer: str,
        audience: str,
        ttl_seconds: int = DEFAULT_JWKS_CACHE_TTL_SECONDS,
        fetcher: JwksFetcher | None = None,
    ) -> JwksJwtValidator:
        """Construct a JWKS-backed validator for RS256 tokens.

        Phase 2 of ADR-041 flips production to this constructor; phase 1
        keeps `__init__` (HS256 + shared secret) as the default. The
        returned instance fetches the JWKS once on first verify and
        caches by `kid`; subsequent verifies are in-process.

        `fetcher` is injectable for tests; production uses the stdlib
        `urllib`-based default in `panakoes_auth_client.jwks`.
        """
        cache = JwksCache(url, ttl_seconds=ttl_seconds, fetcher=fetcher)
        return JwksJwtValidator(
            jwks_cache=cache,
            issuer=issuer,
            audience=audience,
        )


class JwksJwtValidator(JwtValidator):
    """RS256 JWT validator backed by a JWKS endpoint.

    Subclasses `JwtValidator` to share the issuer + audience plumbing
    and the `JwtClaims` parsing. `validate` reads the `kid` from the
    token header, looks up the matching JWK in the cache, and verifies
    against that key. Algorithm is locked to RS256: HS256 tokens are
    rejected outright because they cannot be JWKS-validated.
    """

    def __init__(
        self,
        *,
        jwks_cache: JwksCache,
        issuer: str,
        audience: str,
    ) -> None:
        # No shared secret in JWKS mode; pass an empty string so the
        # base class's `_secret` attribute stays well-typed. The base
        # `validate` is overridden so the empty secret is never used.
        super().__init__(secret="", issuer=issuer, audience=audience, algorithms=["RS256"])
        self._jwks_cache = jwks_cache

    def validate(self, token: str) -> JwtClaims:
        """Validate `token` using the JWK keyed by its `kid` header."""
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise JwtInvalidError("invalid token") from exc

        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise JwtInvalidError("invalid token: missing kid")

        jwk = self._jwks_cache.get(kid)

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                jwk,
                algorithms=["RS256"],
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
