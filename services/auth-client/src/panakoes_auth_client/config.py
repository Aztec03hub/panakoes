"""Environment-variable construction of `JwtValidator`.

Production services call `from_env()` once at startup. Reading the
settings here (rather than in `JwtValidator.__init__`) keeps the
validator pure: tests construct it with explicit arguments, and the
env-var path lives in one place that a deployment guide can point at.

Two modes (selected by `JWT_PUBLIC_JWKS_URL`):

- HS256 + shared secret (default, ADR-041 phase 1): the original path.
  Reads `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`.
- RS256 + JWKS (phase 2 path, opt-in today): set `JWT_PUBLIC_JWKS_URL`
  to the auth service's `/.well-known/jwks.json` URL. The library
  fetches and caches the JWKS, looks up keys by `kid` on every verify.
  `JWT_SECRET` is no longer required when JWKS mode is active.
"""

from __future__ import annotations

import os

from panakoes_auth_client.errors import JwtConfigError
from panakoes_auth_client.validator import JwtValidator

_HS256_REQUIRED_VARS = ("JWT_SECRET", "JWT_ISSUER", "JWT_AUDIENCE")
_JWKS_REQUIRED_VARS = ("JWT_PUBLIC_JWKS_URL", "JWT_ISSUER", "JWT_AUDIENCE")


def from_env() -> JwtValidator:
    """Construct a `JwtValidator` from environment variables.

    If `JWT_PUBLIC_JWKS_URL` is set, returns a JWKS-backed RS256
    validator (delegates to `from_jwks_url`). Otherwise returns the
    HS256 shared-secret validator from before this PR.

    Raises `JwtConfigError` if any required variable for the selected
    mode is missing or empty.
    """
    jwks_url = os.environ.get("JWT_PUBLIC_JWKS_URL", "")
    if jwks_url:
        return _from_env_jwks(jwks_url)
    return _from_env_hs256()


def _from_env_hs256() -> JwtValidator:
    values, missing = _read_required_env(_HS256_REQUIRED_VARS)
    if missing:
        joined = ", ".join(missing)
        raise JwtConfigError(f"missing required environment variables: {joined}")
    return JwtValidator(
        secret=values["JWT_SECRET"],
        issuer=values["JWT_ISSUER"],
        audience=values["JWT_AUDIENCE"],
    )


def _from_env_jwks(jwks_url: str) -> JwtValidator:
    values, missing = _read_required_env(_JWKS_REQUIRED_VARS)
    if missing:
        joined = ", ".join(missing)
        raise JwtConfigError(f"missing required environment variables: {joined}")
    return JwtValidator.from_jwks_url(
        url=jwks_url,
        issuer=values["JWT_ISSUER"],
        audience=values["JWT_AUDIENCE"],
    )


def _read_required_env(names: tuple[str, ...]) -> tuple[dict[str, str], list[str]]:
    """Read `names` from the environment; return (values, missing)."""
    missing: list[str] = []
    values: dict[str, str] = {}
    for name in names:
        raw = os.environ.get(name, "")
        if not raw:
            missing.append(name)
        else:
            values[name] = raw
    return values, missing
