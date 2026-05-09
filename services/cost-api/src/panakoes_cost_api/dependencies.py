"""Shared dependency factories for cost-api routes.

These functions return the long-lived singletons (DynamoDB cache,
Cost Explorer wrapper) that the route layer consumes. The lifespan
in `main.py` populates `app.state` with the live instances at
startup; tests override `app.dependency_overrides[...]` to inject
moto-backed / stubbed equivalents without touching production code.
"""

from __future__ import annotations

from fastapi import Request

from panakoes_cost_api.cache import CostCache
from panakoes_cost_api.cost_explorer import CostExplorerClientWrapper


def get_cost_cache(request: Request) -> CostCache:
    """Return the request-scoped `CostCache` from app state."""
    cache: CostCache = request.app.state.cost_cache
    return cache


def get_cost_explorer(request: Request) -> CostExplorerClientWrapper:
    """Return the request-scoped Cost Explorer wrapper from app state."""
    ce: CostExplorerClientWrapper = request.app.state.cost_explorer
    return ce
