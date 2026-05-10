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

from panakoes_cost_api.alert_state import AlertStateStore
from panakoes_cost_api.anomaly_detector import AnomalyDetector
from panakoes_cost_api.auth import get_jwt_claims, require_admin
from panakoes_cost_api.cache import CostCache
from panakoes_cost_api.cost_explorer import CostExplorerClientWrapper
from panakoes_cost_api.dependencies import (
    get_alert_state,
    get_anomaly_detector,
    get_cost_cache,
    get_cost_explorer,
    get_tenant_cost_cache,
    get_tenant_rollup,
)
from panakoes_cost_api.main import app
from panakoes_cost_api.models import (
    CacheKey,
    CostAnomaly,
    CostBreakdown,
    CostByService,
    TenantCostBreakdown,
    TenantCostRow,
)
from panakoes_cost_api.tenant_rollup import TenantRollupStore


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
            response = await c.get("/api/v1/cost/by-service?from=2026-04-01&to=2026-05-01")
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
            response = await c.get("/api/v1/cost/by-service?from=2026-04-01&to=2026-05-01")
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
            response = await c.get("/api/v1/cost/by-service?from=2026-04-01&to=2026-05-01")
        assert response.status_code == 502
        assert "AccessDenied" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Phase 2.1: by-tenant route tests.
#
# Three tests covering the by-tenant vertical slice as wired by the
# `tenant-cost-rollup` table read-through plus the same DynamoDB cache
# the by-service route uses (different `query_kind`, same table).
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_test_stack() -> Iterator[tuple[CostCache[TenantCostBreakdown], TenantRollupStore]]:
    """Fresh moto-backed cost-cache + tenant-rollup tables per test.

    Two tables share the moto stub but live in separate boto3 calls so
    the fixture mirrors the production wiring (cost-cache HK = cache_key,
    rollup HK = tenant_id + RK = day).
    """
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        cache_t = ddb.create_table(
            TableName="panakoes-test-cost-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        rollup_t = ddb.create_table(
            TableName="panakoes-test-tenant-cost-rollup",
            KeySchema=[
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "day", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "tenant_id", "AttributeType": "S"},
                {"AttributeName": "day", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        cache_t.wait_until_exists()
        rollup_t.wait_until_exists()
        yield (
            CostCache(table=cache_t, model_class=TenantCostBreakdown),
            TenantRollupStore(table=rollup_t),
        )


@pytest.mark.integration
async def test_by_tenant_unauth_returns_401() -> None:
    """No Authorization header on `/by-tenant` -> 401 with WWW-Authenticate."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.get("/api/v1/cost/by-tenant?from=2026-04-01&to=2026-05-01")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.integration
async def test_by_tenant_happy_path_aggregates_rollup_rows(
    tenant_test_stack: tuple[CostCache[TenantCostBreakdown], TenantRollupStore],
) -> None:
    """Pre-populate the rollup table; the route aggregates and sorts desc."""
    cache, rollup = tenant_test_stack
    # Two tenants, three days each, inside the window.
    rollup.put_rollup("tenant-big", date(2026, 4, 1), 5000)
    rollup.put_rollup("tenant-big", date(2026, 4, 2), 5000)
    rollup.put_rollup("tenant-big", date(2026, 4, 3), 5000)
    rollup.put_rollup("tenant-small", date(2026, 4, 1), 100)
    rollup.put_rollup("tenant-small", date(2026, 4, 2), 100)
    rollup.put_rollup("tenant-small", date(2026, 4, 3), 50)
    # Outside the window (must be ignored): on the exclusive end day.
    rollup.put_rollup("tenant-big", date(2026, 4, 4), 99999)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_tenant_cost_cache] = lambda: cache
    app.dependency_overrides[get_tenant_rollup] = lambda: rollup

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/api/v1/cost/by-tenant?from=2026-04-01&to=2026-04-04")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cache_hit"] is False
        assert body["currency"] == "USD"
        assert body["total_cents"] == 15250  # 15000 + 250
        assert len(body["tenants"]) == 2
        # Sorted descending by cost.
        assert body["tenants"][0]["tenant_id"] == "tenant-big"
        assert body["tenants"][0]["cost_cents"] == 15000
        # Percent-of-total uses round(x, 2).
        assert body["tenants"][0]["percent_of_total"] == 98.36
        assert body["tenants"][1]["tenant_id"] == "tenant-small"
        assert body["tenants"][1]["cost_cents"] == 250
        assert body["tenants"][1]["percent_of_total"] == 1.64
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
async def test_by_tenant_cache_hit_returns_cache_hit_true(
    tenant_test_stack: tuple[CostCache[TenantCostBreakdown], TenantRollupStore],
) -> None:
    """Pre-populated cache: rollup never queried, response carries `cache_hit: true`."""
    cache, rollup = tenant_test_stack
    pre_populated = TenantCostBreakdown(
        from_date=date(2026, 4, 1),
        to_date=date(2026, 5, 1),
        currency="USD",
        tenants=(
            TenantCostRow(
                tenant_id="tenant-cached",
                display_name="tenant-cached",
                cost_cents=42000,
                percent_of_total=100.0,
            ),
        ),
        total_cents=42000,
        cache_hit=False,
        queried_at=datetime(2026, 4, 30, 14, 32, 18, tzinfo=UTC),
    )
    cache.put(
        CacheKey(query_kind="by-tenant", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1)),
        pre_populated,
    )

    # Spy: if the rollup is queried we know we missed the cache. We
    # wrap with a MagicMock that delegates to the real store; the
    # call_count should stay at 0.
    spy = MagicMock(wraps=rollup)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_tenant_cost_cache] = lambda: cache
    app.dependency_overrides[get_tenant_rollup] = lambda: spy

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/api/v1/cost/by-tenant?from=2026-04-01&to=2026-05-01")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cache_hit"] is True
        assert body["total_cents"] == 42000
        assert body["tenants"][0]["tenant_id"] == "tenant-cached"
        # The rollup store was never asked anything: cache served it all.
        assert spy.query_all_tenants_for_day.call_count == 0
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Phase 2.2: forecast route tests.
#
# Four integration tests covering the forecast vertical slice: unauth
# 401, invalid horizon 400, happy path, and CE error -> 502 mapping.
# ---------------------------------------------------------------------------


def _ce_forecast_response_two_days() -> dict[str, object]:
    return {
        "ForecastResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-05-11", "End": "2026-05-12"},
                "MeanValue": "12.34",
                "PredictionIntervalLowerBound": "11.00",
                "PredictionIntervalUpperBound": "14.00",
            },
            {
                "TimePeriod": {"Start": "2026-05-12", "End": "2026-05-13"},
                "MeanValue": "12.80",
                "PredictionIntervalLowerBound": "11.40",
                "PredictionIntervalUpperBound": "14.50",
            },
        ],
    }


@pytest.mark.integration
async def test_forecast_unauth_returns_401() -> None:
    """No Authorization header on `/forecast` -> 401 with WWW-Authenticate."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.get("/api/v1/cost/forecast?horizon_days=30")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.integration
async def test_forecast_invalid_horizon_returns_400() -> None:
    """`horizon_days` outside the allowed set -> 400 with descriptive message."""
    ce_mock = MagicMock()
    wrapper = CostExplorerClientWrapper(client=ce_mock)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_cost_explorer] = lambda: wrapper

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/api/v1/cost/forecast?horizon_days=21")
        assert response.status_code == 400
        assert "horizon_days must be one of" in response.json()["detail"]
        # CE was never called; bad-input rejection happens before CE.
        assert ce_mock.get_cost_forecast.call_count == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
async def test_forecast_happy_path_returns_buckets() -> None:
    """Happy path: CE called once; response has buckets, model, queried_at."""
    ce_mock = MagicMock()
    ce_mock.get_cost_forecast.return_value = _ce_forecast_response_two_days()
    wrapper = CostExplorerClientWrapper(client=ce_mock)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_cost_explorer] = lambda: wrapper

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/api/v1/cost/forecast?horizon_days=30")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["horizon_days"] == 30
        assert body["model"] == "ce-builtin"
        assert "queried_at" in body
        assert len(body["buckets"]) == 2
        # Ascending by date.
        assert body["buckets"][0]["date"] == "2026-05-11"
        assert body["buckets"][0]["predicted_cost_cents"] == 1234
        assert body["buckets"][0]["lower_bound_cents"] == 1100
        assert body["buckets"][0]["upper_bound_cents"] == 1400
        assert body["buckets"][1]["date"] == "2026-05-12"
        assert body["buckets"][1]["predicted_cost_cents"] == 1280
        assert ce_mock.get_cost_forecast.call_count == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
async def test_forecast_default_horizon_is_30() -> None:
    """Omitting `horizon_days` defaults to 30."""
    ce_mock = MagicMock()
    ce_mock.get_cost_forecast.return_value = _ce_forecast_response_two_days()
    wrapper = CostExplorerClientWrapper(client=ce_mock)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_cost_explorer] = lambda: wrapper

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/api/v1/cost/forecast")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["horizon_days"] == 30
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
async def test_forecast_ce_error_returns_502() -> None:
    """An unrecoverable CE ClientError on forecast maps to a 502 (not 500)."""
    ce_mock = MagicMock()
    ce_mock.get_cost_forecast.side_effect = ClientError(
        error_response={"Error": {"Code": "AccessDenied", "Message": "no"}},
        operation_name="GetCostForecast",
    )
    wrapper = CostExplorerClientWrapper(client=ce_mock)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_cost_explorer] = lambda: wrapper

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/api/v1/cost/forecast?horizon_days=7")
        assert response.status_code == 502
        assert "AccessDenied" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Phase 2.3: anomalies route tests.
#
# Three integration tests covering the anomalies vertical slice: the
# unauthenticated path, the active-only-true happy path with a row
# pre-populated in alert-state, and the active-only-false path that
# exercises Cost Explorer's `get_anomalies` API.
# ---------------------------------------------------------------------------


@pytest.fixture
def alert_state_stack() -> Iterator[tuple[AlertStateStore, MagicMock]]:
    """Fresh moto-backed alert-state table + a CE mock for anomaly tests.

    The CE mock returns an empty Anomalies list by default; tests
    override `.return_value` per case. The alert-state table has the
    same shape as the production module's Terraform: HK
    `alert_signature`, TTL on `expires_at`.
    """
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="panakoes-test-alert-state",
            KeySchema=[{"AttributeName": "alert_signature", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "alert_signature", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        ce_mock = MagicMock()
        ce_mock.get_anomalies.return_value = {"Anomalies": []}
        yield AlertStateStore(table=table), ce_mock


@pytest.mark.integration
async def test_anomalies_unauth_returns_401() -> None:
    """No Authorization header on `/anomalies` -> 401 with WWW-Authenticate."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.get("/api/v1/cost/anomalies")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.integration
async def test_anomalies_active_only_returns_pre_populated_row(
    alert_state_stack: tuple[AlertStateStore, MagicMock],
) -> None:
    """Active-only=true reads alert-state directly; CE is never called."""
    alert_state, ce_mock = alert_state_stack
    seeded = CostAnomaly(
        signature="seeded-1",
        detector="ce-monitor",
        tenant_id=None,
        dimension_key="Amazon EC2",
        observed_cost_cents=20000,
        expected_cost_cents=5000,
        deviation_pct=300.0,
        first_seen=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 5, 2, 0, 0, 0, tzinfo=UTC),
        suppressed=False,
    )
    alert_state.put("seeded-1", seeded)

    detector = AnomalyDetector(ce_client=ce_mock, alert_state=alert_state)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_alert_state] = lambda: alert_state
    app.dependency_overrides[get_anomaly_detector] = lambda: detector

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/api/v1/cost/anomalies")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["anomalies"]) == 1
        row = body["anomalies"][0]
        assert row["signature"] == "seeded-1"
        assert row["observed_cost_cents"] == 20000
        assert row["expected_cost_cents"] == 5000
        assert row["suppressed"] is False
        assert ce_mock.get_anomalies.call_count == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
async def test_anomalies_active_only_false_invokes_ce(
    alert_state_stack: tuple[AlertStateStore, MagicMock],
) -> None:
    """Active-only=false fetches fresh anomalies from CE and merges with active rows."""
    alert_state, ce_mock = alert_state_stack
    ce_mock.get_anomalies.return_value = {
        "Anomalies": [
            {
                "AnomalyId": "ce-fresh",
                "AnomalyStartDate": "2026-04-15",
                "AnomalyEndDate": "2026-04-16",
                "MonitorArn": "arn:aws:ce::000000000000:anomalymonitor/abc",
                "Impact": {
                    "TotalActualSpend": 200.00,
                    "TotalExpectedSpend": 50.00,
                    "TotalImpactPercentage": 300.0,
                },
                "RootCauses": [{"Service": "Amazon S3"}],
            }
        ],
    }
    detector = AnomalyDetector(ce_client=ce_mock, alert_state=alert_state)

    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_alert_state] = lambda: alert_state
    app.dependency_overrides[get_anomaly_detector] = lambda: detector

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/api/v1/cost/anomalies?active_only=false")
        assert response.status_code == 200, response.text
        body = response.json()
        assert ce_mock.get_anomalies.call_count == 1
        signatures = {a["signature"] for a in body["anomalies"]}
        assert "ce-fresh" in signatures
        # Fresh anomaly with no matching active alert is `suppressed=False`.
        fresh_row = next(a for a in body["anomalies"] if a["signature"] == "ce-fresh")
        assert fresh_row["suppressed"] is False
        assert fresh_row["observed_cost_cents"] == 20000
    finally:
        app.dependency_overrides.clear()
