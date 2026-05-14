"""Integration tests covering the boto3-backed client wrappers via moto.

These tests verify the wrappers correctly translate boto3 responses to
our typed shapes, and that error paths (missing ECS service, missing
log group) behave as the aggregator expects.
"""

from __future__ import annotations

import time
from typing import Any

import boto3
import pytest

from panakoes_health_aggregator.clients.cloudwatch import CloudWatchClient
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


# ---------------------------------------------------------------------------
# CloudWatchClient (stub-based, no moto needed for Container Insights)
# ---------------------------------------------------------------------------


class _CwEmpty:
    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        return {"MetricDataResults": []}


class _CwWithValues:
    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "MetricDataResults": [
                {"Id": "cpuutilized", "Values": [256.0]},
                {"Id": "cpureserved", "Values": [512.0]},
                {"Id": "memoryutilized", "Values": [128.0]},
                {"Id": "memoryreserved", "Values": [512.0]},
            ]
        }


class _CwExplodes:
    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("CW unavailable")


@pytest.mark.integration
async def test_cloudwatch_empty_results_returns_zeroed_snapshot() -> None:
    """Empty MetricDataResults -> zeroed MetricSnapshot."""
    client = CloudWatchClient(client=_CwEmpty(), cluster="panakoes-dev")
    snap = await client.get_service_metrics("panakoes-dev-auth")
    assert snap.cpu_percent == 0.0
    assert snap.memory_mb == 0
    assert snap.memory_limit_mb == 0


@pytest.mark.integration
async def test_cloudwatch_with_values_computes_cpu_and_memory() -> None:
    """Populated MetricDataResults -> computed cpu_percent and memory values."""
    client = CloudWatchClient(client=_CwWithValues(), cluster="panakoes-dev")
    snap = await client.get_service_metrics("panakoes-dev-auth")
    assert snap.cpu_percent == 50.0  # 256 / 512 * 100
    assert snap.memory_mb == 128
    assert snap.memory_limit_mb == 512


@pytest.mark.integration
async def test_cloudwatch_exception_returns_zeroed_snapshot() -> None:
    """Client exception is swallowed; zeroed snapshot is returned."""
    client = CloudWatchClient(client=_CwExplodes(), cluster="panakoes-dev")
    snap = await client.get_service_metrics("panakoes-dev-auth")
    assert snap.cpu_percent == 0.0
    assert snap.memory_mb == 0
    assert snap.memory_limit_mb == 0


# ---------------------------------------------------------------------------
# LogsClient: additional paths (has_recent_event=True, get_recent_events,
# get_recent_errors with dedup, missing-group graceful returns)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_logs_present_group_with_events_returns_true(
    aws_mocks: None,
) -> None:
    """Log group with a recent event -> has_recent_event returns True."""
    logs_raw = boto3.client("logs", region_name="us-east-1")
    logs_raw.create_log_group(logGroupName="/panakoes/dev/ha-auth")
    logs_raw.create_log_stream(
        logGroupName="/panakoes/dev/ha-auth", logStreamName="stream"
    )
    logs_raw.put_log_events(
        logGroupName="/panakoes/dev/ha-auth",
        logStreamName="stream",
        logEvents=[{"timestamp": int(time.time() * 1000), "message": "started"}],
    )
    client = LogsClient(client=logs_raw)
    assert await client.has_recent_event("/panakoes/dev/ha-auth", 300) is True


@pytest.mark.integration
async def test_logs_get_recent_events_json_message(aws_mocks: None) -> None:
    """Structlog JSON message is parsed to level=INFO."""
    logs_raw = boto3.client("logs", region_name="us-east-1")
    logs_raw.create_log_group(logGroupName="/panakoes/dev/ha-b")
    logs_raw.create_log_stream(
        logGroupName="/panakoes/dev/ha-b", logStreamName="stream"
    )
    logs_raw.put_log_events(
        logGroupName="/panakoes/dev/ha-b",
        logStreamName="stream",
        logEvents=[
            {
                "timestamp": int(time.time() * 1000),
                "message": '{"level": "info", "event": "started"}',
            }
        ],
    )
    client = LogsClient(client=logs_raw)
    entries = await client.get_recent_events("/panakoes/dev/ha-b", window_seconds=300)
    assert len(entries) == 1
    assert entries[0].level == "INFO"


@pytest.mark.integration
async def test_logs_get_recent_events_plain_warn_message(aws_mocks: None) -> None:
    """Plain-text WARN message falls back to keyword extraction."""
    logs_raw = boto3.client("logs", region_name="us-east-1")
    logs_raw.create_log_group(logGroupName="/panakoes/dev/ha-c")
    logs_raw.create_log_stream(
        logGroupName="/panakoes/dev/ha-c", logStreamName="stream"
    )
    logs_raw.put_log_events(
        logGroupName="/panakoes/dev/ha-c",
        logStreamName="stream",
        logEvents=[
            {"timestamp": int(time.time() * 1000), "message": "WARN: something odd"}
        ],
    )
    client = LogsClient(client=logs_raw)
    entries = await client.get_recent_events("/panakoes/dev/ha-c", window_seconds=300)
    assert len(entries) == 1
    assert entries[0].level == "WARN"


@pytest.mark.integration
async def test_logs_get_recent_events_warning_normalises_to_warn(
    aws_mocks: None,
) -> None:
    """JSON level='warning' is normalised to 'WARN'."""
    logs_raw = boto3.client("logs", region_name="us-east-1")
    logs_raw.create_log_group(logGroupName="/panakoes/dev/ha-d")
    logs_raw.create_log_stream(
        logGroupName="/panakoes/dev/ha-d", logStreamName="stream"
    )
    logs_raw.put_log_events(
        logGroupName="/panakoes/dev/ha-d",
        logStreamName="stream",
        logEvents=[
            {
                "timestamp": int(time.time() * 1000),
                "message": '{"level": "warning", "event": "slow"}',
            }
        ],
    )
    client = LogsClient(client=logs_raw)
    entries = await client.get_recent_events("/panakoes/dev/ha-d", window_seconds=300)
    assert len(entries) == 1
    assert entries[0].level == "WARN"


@pytest.mark.integration
async def test_logs_get_recent_events_missing_group_returns_empty(
    aws_mocks: None,
) -> None:
    """ResourceNotFoundException from get_recent_events -> empty list."""
    logs_raw = boto3.client("logs", region_name="us-east-1")
    client = LogsClient(client=logs_raw)
    entries = await client.get_recent_events("/panakoes/dev/nope", window_seconds=300)
    assert entries == []


@pytest.mark.integration
async def test_logs_get_recent_errors_deduplicates_same_message(
    aws_mocks: None,
) -> None:
    """Identical error messages are deduped; count reflects occurrences."""
    now_ms = int(time.time() * 1000)
    logs_raw = boto3.client("logs", region_name="us-east-1")
    logs_raw.create_log_group(logGroupName="/panakoes/dev/ha-e")
    logs_raw.create_log_stream(
        logGroupName="/panakoes/dev/ha-e", logStreamName="stream"
    )
    logs_raw.put_log_events(
        logGroupName="/panakoes/dev/ha-e",
        logStreamName="stream",
        logEvents=[
            {"timestamp": now_ms, "message": "ERROR: boom"},
            {"timestamp": now_ms + 1, "message": "ERROR: boom"},
        ],
    )
    client = LogsClient(client=logs_raw)
    errors = await client.get_recent_errors("/panakoes/dev/ha-e", window_seconds=300)
    assert len(errors) == 1
    assert errors[0].count == 2
    assert "ERROR: boom" in errors[0].message


@pytest.mark.integration
async def test_logs_get_recent_errors_missing_group_returns_empty(
    aws_mocks: None,
) -> None:
    """ResourceNotFoundException from get_recent_errors -> empty list."""
    logs_raw = boto3.client("logs", region_name="us-east-1")
    client = LogsClient(client=logs_raw)
    errors = await client.get_recent_errors("/panakoes/dev/nope", window_seconds=300)
    assert errors == []
