"""Pydantic model for the validated JWT payload.

`JwtClaims` is the type returned by `JwtValidator.validate`. It is a
strict Pydantic model so any payload shape mismatch (missing required
claim, wrong type) surfaces as a `ValidationError` we can convert
to `JwtInvalidError` at the call site.

Only the claims the Auth service guarantees are typed here. Anything
else in the payload is dropped (extra='ignore') so downstream
consumers cannot accidentally take a dependency on undocumented
claims.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class JwtClaims(BaseModel):
    """The validated payload of a Panakoes-issued access token."""

    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
    )

    sub: str = Field(..., min_length=1)
    iss: str = Field(..., min_length=1)
    aud: str = Field(..., min_length=1)
    iat: int
    exp: int
    jti: str | None = None
    scopes: list[str] = Field(default_factory=list)
