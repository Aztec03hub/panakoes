"""Request-scoped dependency factories for health-aggregator routes."""

from __future__ import annotations

from fastapi import Request

from panakoes_health_aggregator.aggregator import HealthAggregator


def get_aggregator(request: Request) -> HealthAggregator:
    """Return the long-lived `HealthAggregator` instance from app state."""
    agg: HealthAggregator = request.app.state.aggregator
    return agg
