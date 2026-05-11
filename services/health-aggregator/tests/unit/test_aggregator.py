"""Unit tests for the `HealthAggregator` status-decision matrix.

These tests inject fake clients (no boto3, no moto) so each branch
of the rollup matrix is exercised deterministically.
"""

from __future__ import annotations

from typing import Any

import pytest

from panakoes_health_aggregator.aggregator import HealthAggregator
from panakoes_health_aggregator.clients.ecs import EcsClient
from panakoes_health_aggregator.clients.elbv2 import Elbv2Client
from panakoes_health_aggregator.clients.logs import LogsClient
from panakoes_health_aggregator.models import ServiceProbe
from panakoes_health_aggregator.registry import MonitoredService


class FakeEcs:
    """Stand-in for boto3 ecs client; canned `describe_services` response."""

    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self._response = response

    def describe_services(
        self, *, cluster: str, services: list[str]
    ) -> dict[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class FakeElbv2:
    """Stand-in for boto3 elbv2 client; canned target-health responses."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def describe_target_health(
        self, *, TargetGroupArn: str
    ) -> dict[str, Any]:
        return self._response


class FakeLogs:
    """Stand-in for boto3 logs client; canned filter_log_events response."""

    def __init__(self, has_event: bool) -> None:
        self._has_event = has_event

    def filter_log_events(
        self, *, logGroupName: str, startTime: int, limit: int
    ) -> dict[str, Any]:
        return {"events": [{"message": "hi"}] if self._has_event else []}


def _make_aggregator(
    ecs_response: dict[str, Any] | Exception,
    elbv2_response: dict[str, Any] | None = None,
    logs_has_event: bool = True,
) -> HealthAggregator:
    elbv2_response = elbv2_response or {
        "TargetHealthDescriptions": [
            {"TargetHealth": {"State": "healthy"}}
        ]
    }
    return HealthAggregator(
        ecs=EcsClient(FakeEcs(ecs_response), cluster="panakoes-dev"),
        elbv2=Elbv2Client(FakeElbv2(elbv2_response)),
        logs=LogsClient(FakeLogs(logs_has_event)),
        log_heartbeat_window_seconds=300,
    )


def _ecs_response(
    running: int, desired: int, deployments: int
) -> dict[str, Any]:
    return {
        "services": [
            {
                "runningCount": running,
                "desiredCount": desired,
                "deployments": [{"id": f"d{i}"} for i in range(deployments)],
            }
        ]
    }


@pytest.mark.unit
def test_status_healthy_when_all_green() -> None:
    """Healthy probe (1 deployment, run==desired, TG healthy, log seen)."""
    probe = ServiceProbe(
        ecs_running=2,
        ecs_desired=2,
        ecs_deployments=1,
        targets_all_healthy=True,
        log_heartbeat=True,
        error=None,
    )
    status, message = HealthAggregator.status_from_probe(probe)
    assert status == "healthy"
    assert message is None


@pytest.mark.unit
def test_status_unhealthy_when_run_below_desired() -> None:
    """running != desired -> unhealthy with diagnostic message."""
    probe = ServiceProbe(
        ecs_running=1,
        ecs_desired=2,
        ecs_deployments=1,
        targets_all_healthy=True,
        log_heartbeat=True,
        error=None,
    )
    status, message = HealthAggregator.status_from_probe(probe)
    assert status == "unhealthy"
    assert message is not None
    assert "running=1" in message
    assert "desired=2" in message


@pytest.mark.unit
def test_status_unhealthy_when_targets_unhealthy() -> None:
    """ECS green but TG has unhealthy targets -> unhealthy."""
    probe = ServiceProbe(
        ecs_running=2,
        ecs_desired=2,
        ecs_deployments=1,
        targets_all_healthy=False,
        log_heartbeat=True,
        error=None,
    )
    status, _ = HealthAggregator.status_from_probe(probe)
    assert status == "unhealthy"


@pytest.mark.unit
def test_status_unknown_when_rollout_in_progress() -> None:
    """deployments != 1 -> unknown (rollout in flight)."""
    probe = ServiceProbe(
        ecs_running=2,
        ecs_desired=2,
        ecs_deployments=2,
        targets_all_healthy=True,
        log_heartbeat=True,
        error=None,
    )
    status, message = HealthAggregator.status_from_probe(probe)
    assert status == "unknown"
    assert message is not None
    assert "rollout" in message


@pytest.mark.unit
def test_status_unknown_when_log_heartbeat_absent() -> None:
    """All ECS / TG green but no recent log -> unknown."""
    probe = ServiceProbe(
        ecs_running=2,
        ecs_desired=2,
        ecs_deployments=1,
        targets_all_healthy=True,
        log_heartbeat=False,
        error=None,
    )
    status, message = HealthAggregator.status_from_probe(probe)
    assert status == "unknown"
    assert message is not None
    assert "heartbeat" in message


@pytest.mark.unit
def test_status_unknown_when_probe_errored() -> None:
    """Any probe error -> unknown (preserves snapshot for other services)."""
    probe = ServiceProbe(
        ecs_running=0,
        ecs_desired=0,
        ecs_deployments=0,
        targets_all_healthy=None,
        log_heartbeat=None,
        error="boom",
    )
    status, message = HealthAggregator.status_from_probe(probe)
    assert status == "unknown"
    assert message == "boom"


@pytest.mark.unit
def test_status_healthy_when_no_targets_or_logs_configured() -> None:
    """Service with no TGs and check_logs=False is healthy iff ECS is green."""
    probe = ServiceProbe(
        ecs_running=1,
        ecs_desired=1,
        ecs_deployments=1,
        targets_all_healthy=None,
        log_heartbeat=None,
        error=None,
    )
    status, _ = HealthAggregator.status_from_probe(probe)
    assert status == "healthy"


@pytest.mark.unit
async def test_probe_healthy_end_to_end() -> None:
    """End-to-end probe with all three clients returning green data."""
    agg = _make_aggregator(_ecs_response(running=1, desired=1, deployments=1))
    entry = MonitoredService(
        service="auth",
        display_name="Auth",
        ecs_service="panakoes-dev-auth",
        target_group_arns=("arn:aws:elasticloadbalancing:::targetgroup/auth/abc",),
        log_group="/panakoes/dev/auth",
    )
    health = await agg.health_for(entry)
    assert health.status == "healthy"
    assert health.message is None
    assert health.service == "auth"
    assert health.display_name == "Auth"


@pytest.mark.unit
async def test_probe_ecs_service_missing() -> None:
    """ECS service absent rolls up to unknown with diagnostic message."""
    agg = _make_aggregator({"services": []})
    entry = MonitoredService(
        service="auth",
        display_name="Auth",
        ecs_service="panakoes-dev-auth",
        check_logs=False,
    )
    health = await agg.health_for(entry)
    assert health.status == "unknown"
    assert health.message == "ecs service not found"


@pytest.mark.unit
async def test_probe_swallows_client_error() -> None:
    """A boto3 exception is caught and surfaced as probe.error."""
    agg = _make_aggregator(RuntimeError("ECS API exploded"))
    entry = MonitoredService(
        service="auth",
        display_name="Auth",
        ecs_service="panakoes-dev-auth",
        check_logs=False,
    )
    health = await agg.health_for(entry)
    assert health.status == "unknown"
    assert health.message is not None
    assert "ECS API exploded" in health.message


@pytest.mark.unit
async def test_probe_no_target_groups_skips_elbv2() -> None:
    """Services without TG ARNs report targets_all_healthy=None."""
    agg = _make_aggregator(_ecs_response(running=1, desired=1, deployments=1))
    entry = MonitoredService(
        service="event-router",
        display_name="Event Router",
        ecs_service="panakoes-dev-event-router",
        target_group_arns=(),
        check_logs=False,
    )
    probe = await agg.probe(entry)
    assert probe.targets_all_healthy is None
    assert probe.log_heartbeat is None


@pytest.mark.unit
async def test_probe_log_heartbeat_absent_returns_unknown() -> None:
    """Log heartbeat returns False -> rollup is unknown."""
    agg = _make_aggregator(
        _ecs_response(running=1, desired=1, deployments=1),
        logs_has_event=False,
    )
    entry = MonitoredService(
        service="session-manager",
        display_name="Session Manager",
        ecs_service="panakoes-dev-session-manager",
        log_group="/panakoes/dev/session-manager",
    )
    health = await agg.health_for(entry)
    assert health.status == "unknown"


@pytest.mark.unit
async def test_snapshot_runs_full_registry() -> None:
    """`snapshot()` covers every registry entry and preserves order."""
    agg = _make_aggregator(
        _ecs_response(running=1, desired=1, deployments=1),
        logs_has_event=True,
    )
    from panakoes_health_aggregator.registry import SERVICE_REGISTRY

    snap = await agg.snapshot()
    assert len(snap.services) == len(SERVICE_REGISTRY)
    assert [s.service for s in snap.services] == [
        e.service for e in SERVICE_REGISTRY
    ]
    for entry in snap.services:
        assert entry.status == "healthy"
