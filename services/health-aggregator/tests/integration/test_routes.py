"""Integration tests for the health-aggregator HTTP routes.

These tests:
- Override `require_admin` so we never need a real signed JWT.
- Inject a stub `HealthAggregator` so routes are exercised without
  touching real AWS clients.
- Verify the response shape matches the contract the SPA expects
  (snake_case fields, `service`, `display_name`, `status`,
  `last_check`, optional `message`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from panakoes_auth_client import JwtClaims

from panakoes_health_aggregator.aggregator import HealthAggregator
from panakoes_health_aggregator.auth import get_jwt_claims, require_admin
from panakoes_health_aggregator.dependencies import get_aggregator
from panakoes_health_aggregator.main import app
from panakoes_health_aggregator.models import (
    HealthSnapshot,
    MetricSnapshot,
    ServiceDetail,
    ServiceHealth,
)
from panakoes_health_aggregator.registry import MonitoredService


def _admin_claims() -> JwtClaims:
    return JwtClaims(
        sub="user_admin",
        iss="https://auth.example",
        aud="health-aggregator",
        iat=int(datetime.now(UTC).timestamp()),
        exp=int(datetime.now(UTC).timestamp()) + 3600,
        role="admin",
    )


def _user_claims() -> JwtClaims:
    return JwtClaims(
        sub="user_regular",
        iss="https://auth.example",
        aud="health-aggregator",
        iat=int(datetime.now(UTC).timestamp()),
        exp=int(datetime.now(UTC).timestamp()) + 3600,
        role="user",
    )


class StubAggregator:
    """Minimal stub satisfying `HealthAggregator`'s used surface."""

    def __init__(self) -> None:
        self._now = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)

    async def snapshot(self) -> HealthSnapshot:
        return HealthSnapshot(
            generated_at=self._now,
            services=[
                ServiceHealth(
                    service="auth",
                    display_name="Auth",
                    status="healthy",
                    last_check=self._now,
                ),
                ServiceHealth(
                    service="transcriber-stream",
                    display_name="Transcriber (stream)",
                    status="unhealthy",
                    last_check=self._now,
                    message="GPU container failed health probe",
                ),
            ],
        )

    async def health_for(self, entry: MonitoredService) -> ServiceHealth:
        return ServiceHealth(
            service=entry.service,
            display_name=entry.display_name,
            status="healthy",
            last_check=self._now,
        )

    async def detail_for(self, entry: MonitoredService) -> ServiceDetail:
        return ServiceDetail(
            service=entry.service,
            display_name=entry.display_name,
            health=await self.health_for(entry),
            recent_logs=[],
            recent_errors=[],
            metrics=MetricSnapshot(cpu_percent=0.0, memory_mb=0, memory_limit_mb=0),
        )


@pytest_asyncio.fixture
async def routes_client() -> AsyncIterator[AsyncClient]:
    """Async client with require_admin + aggregator overrides installed."""
    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_aggregator] = lambda: cast(
        HealthAggregator, StubAggregator()
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.integration
async def test_healthz_liveness_is_open(async_client: AsyncClient) -> None:
    """`/healthz` does not require auth."""
    response = await async_client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": "health-aggregator"}


@pytest.mark.integration
async def test_health_snapshot_returns_canonical_shape(
    routes_client: AsyncClient,
) -> None:
    """`/health` matches the snake_case shape the SPA consumes."""
    response = await routes_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert "generated_at" in body
    assert isinstance(body["services"], list)
    assert {row["service"] for row in body["services"]} == {
        "auth",
        "transcriber-stream",
    }
    auth_row = next(r for r in body["services"] if r["service"] == "auth")
    assert auth_row["display_name"] == "Auth"
    assert auth_row["status"] == "healthy"
    assert "last_check" in auth_row
    # Healthy row omits the optional `message` field.
    assert auth_row.get("message") is None
    unhealthy = next(
        r for r in body["services"] if r["service"] == "transcriber-stream"
    )
    assert unhealthy["status"] == "unhealthy"
    assert unhealthy["message"] == "GPU container failed health probe"


@pytest.mark.integration
async def test_health_requires_admin(async_client: AsyncClient) -> None:
    """Unauthenticated `/health` returns 401."""
    response = await async_client.get("/health")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


@pytest.mark.integration
async def test_health_rejects_non_admin() -> None:
    """Non-admin JWT returns 403 from `require_admin`."""
    app.dependency_overrides[get_jwt_claims] = _user_claims
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/health", headers={"Authorization": "Bearer x"}
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
async def test_service_detail_returns_canonical_shape(
    routes_client: AsyncClient,
) -> None:
    """`/services/{name}` returns the SPA's `ServiceDetail` shape."""
    response = await routes_client.get("/services/auth")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "auth"
    assert body["display_name"] == "Auth"
    assert body["health"]["status"] == "healthy"
    assert body["recent_logs"] == []
    assert body["recent_errors"] == []
    assert body["metrics"] == {
        "cpu_percent": 0.0,
        "memory_mb": 0,
        "memory_limit_mb": 0,
    }


@pytest.mark.integration
async def test_service_detail_unknown_returns_404(
    routes_client: AsyncClient,
) -> None:
    """Unknown service id returns 404."""
    response = await routes_client.get("/services/does-not-exist")
    assert response.status_code == 404
    assert "unknown service" in response.json()["detail"]


@pytest.mark.integration
async def test_options_healthz(async_client: AsyncClient) -> None:
    """OPTIONS /healthz returns 200 (CORS preflight support)."""
    response = await async_client.options("/healthz")
    assert response.status_code == 200


@pytest.mark.integration
async def test_options_health(async_client: AsyncClient) -> None:
    """OPTIONS /health returns 200."""
    response = await async_client.options("/health")
    assert response.status_code == 200


@pytest.mark.integration
async def test_options_services_name(async_client: AsyncClient) -> None:
    """OPTIONS /services/{name} returns 200."""
    response = await async_client.options("/services/auth")
    assert response.status_code == 200


@pytest.mark.integration
async def test_health_returns_401_for_malformed_auth_header(
    async_client: AsyncClient,
) -> None:
    """Non-Bearer Authorization header returns 401."""
    response = await async_client.get(
        "/health", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


@pytest.mark.integration
async def test_health_returns_401_for_invalid_jwt(
    async_client: AsyncClient,
) -> None:
    """A syntactically valid Bearer token that fails signature validation returns 401."""
    response = await async_client.get(
        "/health", headers={"Authorization": "Bearer not.a.valid.jwt"}
    )
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


@pytest.mark.integration
def test_get_aggregator_reads_from_app_state() -> None:
    """get_aggregator surfaces whatever is stashed on app.state.aggregator."""
    from starlette.requests import Request

    from panakoes_health_aggregator.dependencies import get_aggregator
    from panakoes_health_aggregator.main import app

    sentinel = object()
    app.state.aggregator = sentinel
    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "app": app,
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    assert get_aggregator(request) is sentinel
