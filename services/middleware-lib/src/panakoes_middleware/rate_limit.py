"""Sliding-window rate limiter middleware.

Two stores are provided. `InMemoryStore` keeps timestamps in a per-key
deque; it is fast and dependency-free but only meaningful inside one
process, so it is intended for development and tests. `RedisStore`
uses a Redis sorted-set per key to maintain the same sliding window
across many app instances; it is the production choice.

`RateLimitMiddleware` wires either store into a Starlette middleware
that rejects over-budget requests with HTTP 429 and a `Retry-After`
header expressing seconds until the next slot opens.

The rate limit key defaults to the client IP, preferring a forwarded
header chain when the service runs behind an ALB or CloudFront. Custom
keying (e.g. by API key or user id) is supplied via `key_fn`.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from redis.asyncio import Redis
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp


WINDOW_SECONDS = 60
"""Sliding window length in seconds."""


@runtime_checkable
class RateLimitStore(Protocol):
    """Pluggable backend for the sliding-window rate limiter.

    Implementations record one timestamp per request keyed by the
    caller-supplied identifier and return how many requests have
    occurred inside the trailing `window_seconds` plus the unix time
    of the oldest timestamp inside the window. The middleware uses the
    oldest-timestamp value to compute a fair `Retry-After`.
    """

    async def hit(self, key: str, window_seconds: int) -> tuple[int, float]:
        """Record a hit for `key`; return (count_in_window, oldest_ts)."""
        ...


class InMemoryStore:
    """Single-process rate-limit backend.

    Stores a deque of timestamps per key. On each `hit`, expired
    timestamps (older than `window_seconds`) are popped from the left
    and the new timestamp is appended on the right. The lock keeps
    multi-coroutine access safe inside one event loop.
    """

    def __init__(self) -> None:
        """Initialize empty per-key state and a coroutine lock."""
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def hit(self, key: str, window_seconds: int) -> tuple[int, float]:
        """Append a fresh timestamp and return `(count, oldest_in_window)`."""
        now = time.time()
        async with self._lock:
            bucket = self._buckets[key]
            cutoff = now - window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            bucket.append(now)
            return len(bucket), bucket[0]


class RedisStore:
    """Redis-backed rate-limit backend using a per-key sorted set.

    Each request is recorded as a member with score equal to its unix
    timestamp. Old entries are trimmed by `ZREMRANGEBYSCORE`, the new
    entry is added with `ZADD`, and `ZRANGE` reads the oldest entry
    that survived the trim. We pipeline the four commands so the
    operation is a single round-trip.

    The sorted-set member is `f"{ts}:{nanos}"` so two hits within the
    same second do not collide on member id. Redis `ZADD` with the
    same score and same member would silently dedupe; we avoid that.
    """

    def __init__(self, client: Redis) -> None:
        """Bind to an async redis client (use `redis.asyncio.Redis`)."""
        self._client = client

    async def hit(self, key: str, window_seconds: int) -> tuple[int, float]:
        """Record one hit; return `(count_in_window, oldest_ts_in_window)`."""
        now = time.time()
        cutoff = now - window_seconds
        member = f"{now:.6f}"
        pipe = self._client.pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zadd(key, {member: now})
        pipe.zrange(key, 0, 0, withscores=True)
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 1)
        results = await pipe.execute()
        # results: [trim_count, add_count, [(member, score)], zcard, expire_ok]
        oldest = results[2][0][1] if results[2] else now
        count = int(results[3])
        return count, float(oldest)


def _default_key_fn(request: Request) -> str:
    """Resolve the client IP, preferring `X-Forwarded-For` first hop.

    Behind ALB/CloudFront the client IP arrives in `X-Forwarded-For`
    as a comma-separated list; we want the leftmost entry. When the
    header is absent, we fall back to the ASGI client peer; that is
    correct for direct-connect clients in development.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests beyond `requests_per_minute` for a given key.

    Wraps the ASGI app and consults the configured store on every
    request. When the store reports a count above the budget, returns
    HTTP 429 with a `Retry-After` header set to the number of seconds
    until the oldest in-window hit ages out.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        requests_per_minute: int = 60,
        key_fn: Callable[[Request], str] | None = None,
        store: RateLimitStore | None = None,
        window_seconds: int = WINDOW_SECONDS,
    ) -> None:
        """Wrap the app with a sliding-window rate limit."""
        super().__init__(app)
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self._limit = requests_per_minute
        self._key_fn = key_fn if key_fn is not None else _default_key_fn
        self._store: RateLimitStore = store if store is not None else InMemoryStore()
        self._window = window_seconds

    @property
    def requests_per_minute(self) -> int:
        """Return the configured request budget per window."""
        return self._limit

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Record a hit for the caller and either pass through or 429."""
        key = self._key_fn(request)
        count, oldest = await self._store.hit(key, self._window)
        if count > self._limit:
            retry_after = max(1, int(self._window - (time.time() - oldest)) + 1)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
