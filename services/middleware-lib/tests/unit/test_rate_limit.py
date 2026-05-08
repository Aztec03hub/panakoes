"""Tests for `RateLimitMiddleware`, `InMemoryStore`, and `RedisStore`."""

from __future__ import annotations

import asyncio

import pytest
from fakeredis import aioredis as fake_aioredis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from panakoes_middleware.rate_limit import (
    InMemoryStore,
    RateLimitMiddleware,
    RateLimitStore,
    RedisStore,
    _default_key_fn,
)


def _build_app(
    *, requests_per_minute: int, store: RateLimitStore | None = None
) -> FastAPI:
    """Return an app guarded by a rate limit set to the given budget."""
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=requests_per_minute,
        store=store,
    )

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "true"}

    return app


@pytest.mark.unit
def test_within_budget_passes_through() -> None:
    """The Nth request inside the budget returns 200."""
    client = TestClient(_build_app(requests_per_minute=5))
    for _ in range(5):
        response = client.get("/ping")
        assert response.status_code == 200


@pytest.mark.unit
def test_over_budget_returns_429_with_retry_after() -> None:
    """The first over-budget request gets 429 with `Retry-After`."""
    client = TestClient(_build_app(requests_per_minute=2))
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    response = client.get("/ping")
    assert response.status_code == 429
    assert response.json() == {"detail": "Rate limit exceeded"}
    retry_after = int(response.headers["Retry-After"])
    assert 1 <= retry_after <= 61


@pytest.mark.unit
def test_custom_key_fn_partitions_callers() -> None:
    """Distinct keys do not consume each other's budget."""
    received: list[str] = []

    def key_fn(request: Request) -> str:
        key = request.headers.get("x-tenant", "default")
        received.append(key)
        return key

    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=1,
        key_fn=key_fn,
    )

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "true"}

    client = TestClient(app)
    assert client.get("/ping", headers={"X-Tenant": "a"}).status_code == 200
    assert client.get("/ping", headers={"X-Tenant": "b"}).status_code == 200
    # tenant a's second request hits the limit.
    assert client.get("/ping", headers={"X-Tenant": "a"}).status_code == 429
    assert "a" in received and "b" in received


@pytest.mark.unit
def test_zero_or_negative_limit_rejected() -> None:
    """A non-positive `requests_per_minute` is a programmer error."""
    from starlette.applications import Starlette

    base = Starlette()
    with pytest.raises(ValueError, match="positive"):
        RateLimitMiddleware(base, requests_per_minute=0)


@pytest.mark.unit
def test_property_exposes_configured_limit() -> None:
    """Tests and operators can read the configured ceiling."""
    from starlette.applications import Starlette

    base = Starlette()
    mw = RateLimitMiddleware(base, requests_per_minute=42)
    assert mw.requests_per_minute == 42


@pytest.mark.unit
async def test_in_memory_store_counts_within_window() -> None:
    """Sequential hits within the window increment the count."""
    store = InMemoryStore()
    count_a, _ = await store.hit("k", 60)
    count_b, _ = await store.hit("k", 60)
    assert count_a == 1
    assert count_b == 2


@pytest.mark.unit
async def test_in_memory_store_drops_expired() -> None:
    """Hits older than the window age out of the count."""
    store = InMemoryStore()
    # Use a very small window and an `await sleep` to age the first hit.
    await store.hit("k", 1)
    await asyncio.sleep(1.05)
    count, _ = await store.hit("k", 1)
    assert count == 1


@pytest.mark.unit
async def test_redis_store_counts_within_window() -> None:
    """`RedisStore` returns the count from a fakeredis-backed sorted set."""
    client = fake_aioredis.FakeRedis()
    store = RedisStore(client)
    count_a, _ = await store.hit("k", 60)
    count_b, _ = await store.hit("k", 60)
    assert count_a == 1
    assert count_b == 2


@pytest.mark.unit
async def test_redis_store_drops_expired() -> None:
    """Old entries beyond the window are trimmed before counting."""
    client = fake_aioredis.FakeRedis()
    store = RedisStore(client)
    await store.hit("k", 1)
    await asyncio.sleep(1.05)
    count, _ = await store.hit("k", 1)
    assert count == 1


@pytest.mark.unit
def test_default_key_fn_prefers_xff_first_hop() -> None:
    """The leftmost `X-Forwarded-For` entry wins over the ASGI peer."""
    scope: dict[str, object] = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.42, 10.0.0.1")],
        "client": ("127.0.0.1", 1234),
        "method": "GET",
        "path": "/",
        "query_string": b"",
    }
    request = Request(scope)
    assert _default_key_fn(request) == "203.0.113.42"


@pytest.mark.unit
def test_default_key_fn_falls_back_to_client_host() -> None:
    """Without `X-Forwarded-For` we read the ASGI peer host."""
    scope: dict[str, object] = {
        "type": "http",
        "headers": [],
        "client": ("198.51.100.7", 1234),
        "method": "GET",
        "path": "/",
        "query_string": b"",
    }
    request = Request(scope)
    assert _default_key_fn(request) == "198.51.100.7"


@pytest.mark.unit
def test_default_key_fn_returns_unknown_when_no_client() -> None:
    """When the ASGI scope has no client, the key is `unknown`."""
    scope: dict[str, object] = {
        "type": "http",
        "headers": [],
        "client": None,
        "method": "GET",
        "path": "/",
        "query_string": b"",
    }
    request = Request(scope)
    assert _default_key_fn(request) == "unknown"


@pytest.mark.unit
def test_default_key_fn_blank_xff_falls_through() -> None:
    """A blank `X-Forwarded-For` first segment falls back to the peer."""
    scope: dict[str, object] = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b" , 10.0.0.1")],
        "client": ("198.51.100.7", 1234),
        "method": "GET",
        "path": "/",
        "query_string": b"",
    }
    request = Request(scope)
    assert _default_key_fn(request) == "198.51.100.7"
