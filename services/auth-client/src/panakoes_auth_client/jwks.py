"""JWKS fetch + cache.

Phase 2 of ADR-041 will flip every Python service to validate JWTs
against the auth service's RS256 public key, retrieved from a JWKS
endpoint. Phase 1 (this module) ships the fetch + cache machinery so
services can opt in via `JwtValidator.from_jwks_url(...)` ahead of the
cutover.

Design:
- One in-process cache per validator instance. Keyed by `kid` so a
  future multi-key rotation works without code changes (today the auth
  service publishes a single key).
- TTL is conservative (10 minutes) because the public key never rotates
  without an explicit admin event; the cost of staleness is at most
  one TTL window of "old kid still serves" after a rotation. The auth
  service phase-2 plan dual-publishes both kids for at least one TTL.
- Fetcher is injectable so tests can drive deterministic responses
  without a network round-trip.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from panakoes_auth_client.errors import JwtConfigError, JwtInvalidError

DEFAULT_JWKS_CACHE_TTL_SECONDS = 600
"""Default TTL for cached JWKS documents (10 minutes)."""


JwksFetcher = Callable[[str], dict[str, Any]]
"""Callable that fetches a JWKS document at the given URL.

Returns the parsed JSON document (a `dict` with a `keys` list). Tests
override this with a stub; production uses `_default_fetcher`.
"""


def _default_fetcher(url: str) -> dict[str, Any]:
    """Fetch the JWKS document via stdlib `urllib`.

    `urllib` (not `httpx`) is the default so the library stays
    dependency-free for services that do not already pull httpx. The
    request times out after 5 seconds; the URL is expected to be the
    auth service inside the VPC, not a third-party.
    """
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 (https URL)
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        body = response.read()
    document: Any = json.loads(body)
    if not isinstance(document, dict):
        raise JwtConfigError(f"JWKS endpoint {url!r} returned non-object JSON")
    return document


@dataclass
class _CacheEntry:
    keys_by_kid: dict[str, dict[str, Any]]
    expires_at: float


class JwksCache:
    """Cache of `kid -> JWK` pulled from a JWKS endpoint.

    Construct one per validator. Thread-safety is not required: the
    cache is loaded lazily on first lookup, and a stale-entry refresh
    is idempotent (the worst case under contention is a second
    network call that overwrites the cache with the same value).
    """

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: int = DEFAULT_JWKS_CACHE_TTL_SECONDS,
        fetcher: JwksFetcher | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._url = url
        self._ttl_seconds = ttl_seconds
        self._fetcher = fetcher or _default_fetcher
        self._now = now or time.monotonic
        self._cached: _CacheEntry | None = None

    def get(self, kid: str) -> dict[str, Any]:
        """Return the JWK for the given `kid`.

        Refreshes the cache if it is empty or past the TTL. If the kid
        is still missing after a refresh, raises `JwtInvalidError`:
        a token signed under an unknown kid is not validatable, full
        stop. (Phase 2 considers this a rotation race; the dual-publish
        window prevents it in practice.)
        """
        entry = self._cached
        if entry is None or entry.expires_at <= self._now():
            entry = self._refresh()
        if kid in entry.keys_by_kid:
            return entry.keys_by_kid[kid]

        # Cache miss on a kid; force one refresh in case the cache was
        # stale and the auth service just rotated. After the refresh,
        # if the kid is still missing we fail closed.
        entry = self._refresh()
        if kid not in entry.keys_by_kid:
            raise JwtInvalidError(f"jwks endpoint has no key for kid {kid!r}")
        return entry.keys_by_kid[kid]

    def _refresh(self) -> _CacheEntry:
        document = self._fetcher(self._url)
        keys = document.get("keys")
        if not isinstance(keys, list):
            raise JwtConfigError(f"JWKS endpoint {self._url!r} returned no 'keys' array")
        keys_by_kid: dict[str, dict[str, Any]] = {}
        for jwk in keys:
            if not isinstance(jwk, dict):
                continue
            kid = jwk.get("kid")
            if isinstance(kid, str):
                keys_by_kid[kid] = jwk
        entry = _CacheEntry(
            keys_by_kid=keys_by_kid,
            expires_at=self._now() + self._ttl_seconds,
        )
        self._cached = entry
        return entry
