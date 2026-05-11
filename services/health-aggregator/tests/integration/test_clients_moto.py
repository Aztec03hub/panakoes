"""Integration tests covering the boto3-backed client wrappers via moto.

These tests verify the wrappers correctly translate boto3 responses to
our typed shapes, and that error paths (missing ECS service, missing
log group) behave as the aggregator expects.
"""

from __future__ import annotations

from typing import Any

import boto3
import pytest

from panakoes_health_aggregator.clients.ecs import EcsClient
from panakoes_health_aggregator.clients.elbv2 import Elbv2Client
from panakoes_health_aggregator.clients.logs import LogsClient


@pytest.mark.integration
async def test_ecs_describe_returns_none_when_service_absent(
    aws_mocks: None,
) -> None:
    """moto: cluster exists but no service registered -> None."""
    ecs_raw = boto3.client("ecs", region_name="us-east-1")
    ecs_raw.create_cluster(clusterName="panakoes-dev")
    client = EcsClient(client=ecs_raw, cluster="panakoes-dev")
    result = await client.describe_service("panakoes-dev-auth")
    assert result is None


@pytest.mark.integration
async def test_logs_missing_group_returns_false(aws_mocks: None) -> None:
    """ResourceNotFoundException from CWL is converted to a False heartbeat."""
    logs_raw = boto3.client("logs", region_name="us-east-1")
    client = LogsClient(client=logs_raw)
    has_event = await client.has_recent_event("/panakoes/dev/missing", 300)
    assert has_event is False


@pytest.mark.integration
async def test_logs_present_group_no_events_returns_false(
    aws_mocks: None,
) -> None:
    """Empty log group inside the window returns False."""
    logs_raw = boto3.client("logs", region_name="us-east-1")
    logs_raw.create_log_group(logGroupName="/panakoes/dev/auth")
    client = LogsClient(client=logs_raw)
    has_event = await client.has_recent_event("/panakoes/dev/auth", 300)
    assert has_event is False


@pytest.mark.integration
async def test_elbv2_empty_target_group_is_not_healthy() -> None:
    """A TG with no registered targets cannot be considered healthy."""

    class EmptyTgClient:
        def describe_target_health(
            self, *, TargetGroupArn: str
        ) -> dict[str, Any]:
            return {"TargetHealthDescriptions": []}

    client = Elbv2Client(client=EmptyTgClient())
    assert await client.all_targets_healthy("arn:dummy") is False


@pytest.mark.integration
async def test_elbv2_mixed_states_returns_false() -> None:
    """If any target is non-healthy, the rollup is False."""

    class MixedTgClient:
        def describe_target_health(
            self, *, TargetGroupArn: str
        ) -> dict[str, Any]:
            return {
                "TargetHealthDescriptions": [
                    {"TargetHealth": {"State": "healthy"}},
                    {"TargetHealth": {"State": "draining"}},
                ]
            }

    client = Elbv2Client(client=MixedTgClient())
    assert await client.all_targets_healthy("arn:dummy") is False
