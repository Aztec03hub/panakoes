"""Integration tests for the `/api/v1/cost/by-service` route.

The route under test depends on:
- `require_admin` (401/403 gating)
- `get_cost_cache` (CostCache instance)
- `get_cost_explorer` (CostExplorerClientWrapper instance)

Each test installs override dependencies into `app.dependency_overrides`
to avoid touching real AWS or a real JWT validator. The CE wrapper and
CostCache are exercised through their public API, so the overrides hand
in the real types backed by moto / stubs rather than mocking the route's
internal behavior.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import boto3
import pytest
import pytest_asyncio
from botocore.exceptions import ClientError
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from panakoes_auth_client import JwtClaims

from panakoes_cost_api.auth import get_jwt_claims, require_admin
from panakoes_cost_api.cache import CostCache
from panakoes_cost_api.cost_explorer import CostExplorerClientWrapper
from panakoes_cost_api.dependencies import get_cost_cache, get_cost_explorer
from panakoes_cost_api.main import app
from panakoes_cost_api.models import CacheKey, CostBreakdown, CostByService


def _admin_claims() -> JwtClaims:
    return JwtClaims(
        sub="user_admin",
        iss="https://auth.example",
        aud="cost-api",
        iat=int(datetime.now(UTC).timestamp()),
        exp=int(datetime.now(UTC).timestamp()) + 3600,
        role="admin",
    )


def _user_claims() -> JwtClaims:
    return JwtClaims(
        sub="user_regular",
        iss="https://auth.example",
        aud="cost-api",
        iat=int(datetime.now(UTC).timestamp()),
        exp=int(datetime.now(UTC).timestamp()) + 3600,
        role="user",
    )


def _ce_response_two_services() -> dict[str, object]:
    return {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-04-01", "End": "2026-05-01"},
                "Groups": [
                    {
                        "Keys": ["Amazon Elastic Compute Cloud - Compute"],
                        "Metrics": {"UnblendedCost": {"Amount": "12.34", "Unit": "USD"}},
                    },
                    {
                        "Keys": ["Amazon Simple Storage Service"],
                        "Metrics": {"UnblendedCost": {"Amount": "1.08", "Unit": "USD"}},
                    },
                ],
                "Total": {},
                "Estimated": False,
            }
        ]
    }


@pytest.fixture
def cache_table() -> Iterator[CostCache]:
    """A fresh moto-backed cost-cache table per test."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="panakoes-test-cost-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield CostCache(table=table)


@pytest_asyncio.fixture
async def client_admin(cache_table: CostCache) -> AsyncIterator[tuple[AsyncClient, MagicMock]]:
    """Async client with admin auth + CE stub + moto-backed cache."""
    ce_mock = MagicMock()
    ce_mock.get_cost_and_usage.return_value = _ce_response_two_services()
    wrapper = CostExplorerClientWrapper(client=ce_mock)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_cost_cache] = lambda: cache_table
    app.dependency_overrides[get_cost_explorer] = lambda: wrapper

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c, ce_mock

    app.dependency_overrides.clear()


@pytest.mark.integration
async def test_unauth_returns_401() -> None:
    """No Authorization header -> 401 with WWW-Authenticate: Bearer."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.get("/api/v1/cost/by-service?from=2026-04-01&to=2026-05-01")

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.integration
async def test_non_admin_returns_403() -> None:
    """Valid JWT with `role != admin` -> 403."""
    app.dependency_overrides[get_jwt_claims] = _user_claims
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get(
                "/api/v1/cost/by-service?from=2026-04-01&to=2026-05-01"
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
async def test_invalid_date_returns_400(
    client_admin: tuple[AsyncClient, MagicMock],
) -> None:
    """`from > to` returns 400 with a descriptive message."""
    c, _ = client_admin
    response = await c.get("/api/v1/cost/by-service?from=2026-05-01&to=2026-04-01")
    assert response.status_code == 400
    assert "after to_date" in response.json()["detail"]


@pytest.mark.integration
async def test_happy_path_cache_miss_returns_data(
    client_admin: tuple[AsyncClient, MagicMock],
) -> None:
    """Cache miss path: CE called, response sorted descending by cost."""
    c, ce_mock = client_admin
    response = await c.get("/api/v1/cost/by-service?from=2026-04-01&to=2026-05-01")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cache_hit"] is False
    assert body["currency"] == "USD"
    assert body["total_cents"] == 1342
    assert len(body["services"]) == 2
    assert body["services"][0]["service"] == "Amazon Elastic Compute Cloud - Compute"
    assert body["services"][0]["cost_cents"] == 1234
    assert ce_mock.get_cost_and_usage.call_count == 1


@pytest.mark.integration
async def test_happy_path_cache_hit_returns_data_with_cache_hit_true(
    cache_table: CostCache,
) -> None:
    """Pre-populated cache: CE never called, response carries `cache_hit: true`."""
    pre_populated = CostBreakdown(
        from_date=date(2026, 4, 1),
        to_date=date(2026, 5, 1),
        currency="USD",
        services=(
            CostByService(service="Amazon EC2", cost_cents=10000, percent_of_total=80.0),
            CostByService(service="Amazon S3", cost_cents=2500, percent_of_total=20.0),
        ),
        total_cents=12500,
        cache_hit=False,
        queried_at=datetime(2026, 4, 30, 14, 32, 18, tzinfo=UTC),
    )
    cache_table.put(
        CacheKey(query_kind="by-service", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1)),
        pre_populated,
    )

    ce_mock = MagicMock()
    wrapper = CostExplorerClientWrapper(client=ce_mock)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_cost_cache] = lambda: cache_table
    app.dependency_overrides[get_cost_explorer] = lambda: wrapper

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get(
                "/api/v1/cost/by-service?from=2026-04-01&to=2026-05-01"
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cache_hit"] is True
        assert body["total_cents"] == 12500
        assert ce_mock.get_cost_and_usage.call_count == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
async def test_ce_error_returns_502(cache_table: CostCache) -> None:
    """An unrecoverable CE ClientError maps to a 502 (not 500)."""
    ce_mock = MagicMock()
    ce_mock.get_cost_and_usage.side_effect = ClientError(
        error_response={"Error": {"Code": "AccessDenied", "Message": "no"}},
        operation_name="GetCostAndUsage",
    )
    wrapper = CostExplorerClientWrapper(client=ce_mock)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_cost_cache] = lambda: cache_table
    app.dependency_overrides[get_cost_explorer] = lambda: wrapper

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get(
                "/api/v1/cost/by-service?from=2026-04-01&to=2026-05-01"
            )
        assert response.status_code == 502
        assert "AccessDenied" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
