"""Unit tests for the static service registry."""

from __future__ import annotations

import pytest

from panakoes_health_aggregator.registry import SERVICE_REGISTRY, get_service


@pytest.mark.unit
def test_registry_is_nonempty_and_unique() -> None:
    """Registry has every Tier 1 service and no duplicates."""
    assert len(SERVICE_REGISTRY) > 0
    names = [e.service for e in SERVICE_REGISTRY]
    assert len(names) == len(set(names))


@pytest.mark.unit
def test_registry_matches_static_mock_services() -> None:
    """Live shape must cover every name in the bundled static mock."""
    expected = {
        "auth",
        "admin-api",
        "cost-api",
        "ingestion-api",
        "summarization",
        "notification",
        "query-api",
        "session-manager",
        "gpu-spawner",
        "transcriber-batch",
        "transcriber-stream",
        "event-router",
        "billing",
    }
    assert {e.service for e in SERVICE_REGISTRY} == expected


@pytest.mark.unit
def test_get_service_returns_matching_entry() -> None:
    """`get_service` returns the entry whose `service` matches."""
    entry = get_service("auth")
    assert entry is not None
    assert entry.display_name == "Auth"


@pytest.mark.unit
def test_get_service_returns_none_for_unknown() -> None:
    """Unknown names return None for the route layer to map to 404."""
    assert get_service("nonexistent") is None
