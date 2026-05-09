"""Shared dependency factories for cost-api routes.

These functions return the long-lived singletons (DynamoDB cache,
Cost Explorer wrapper) that the route layer consumes. The lifespan
in `main.py` populates `app.state` with the live instances at
startup; tests override `app.dependency_overrides[...]` to inject
moto-backed / stubbed equivalents without touching production code.
"""

from __future__ import annotations

from fastapi import Request

from panakoes_cost_api.alert_state import AlertStateStore
from panakoes_cost_api.anomaly_detector import AnomalyDetector
from panakoes_cost_api.cache import CostCache
from panakoes_cost_api.cost_explorer import CostExplorerClientWrapper
from panakoes_cost_api.models import CostBreakdown, TenantCostBreakdown
from panakoes_cost_api.tenant_rollup import TenantRollupStore


def get_cost_cache(request: Request) -> CostCache[CostBreakdown]:
    """Return the request-scoped by-service `CostCache` from app state."""
    cache: CostCache[CostBreakdown] = request.app.state.cost_cache
    return cache


def get_cost_explorer(request: Request) -> CostExplorerClientWrapper:
    """Return the request-scoped Cost Explorer wrapper from app state."""
    ce: CostExplorerClientWrapper = request.app.state.cost_explorer
    return ce


def get_tenant_rollup(request: Request) -> TenantRollupStore:
    """Return the request-scoped `TenantRollupStore` from app state."""
    store: TenantRollupStore = request.app.state.tenant_rollup
    return store


def get_tenant_cost_cache(request: Request) -> CostCache[TenantCostBreakdown]:
    """Return the request-scoped by-tenant `CostCache` from app state.

    A separate `CostCache` instance hydrates `TenantCostBreakdown`
    rather than `CostBreakdown`. Both caches share the underlying
    DynamoDB table because cache keys are namespaced by `query_kind`
    so a `by-service` row never collides with a `by-tenant` row.
    """
    cache: CostCache[TenantCostBreakdown] = request.app.state.tenant_cost_cache
    return cache


def get_alert_state(request: Request) -> AlertStateStore:
    """Return the request-scoped `AlertStateStore` from app state.

    The store wraps `panakoes-dev-alert-state` (HK `alert_signature`,
    TTL on `expires_at`) and is shared between the route layer's
    direct reads and the `AnomalyDetector`'s dedup checks.
    """
    store: AlertStateStore = request.app.state.alert_state
    return store


def get_anomaly_detector(request: Request) -> AnomalyDetector:
    """Return the request-scoped `AnomalyDetector` from app state."""
    detector: AnomalyDetector = request.app.state.anomaly_detector
    return detector
