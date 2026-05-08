"""Unit tests for the EC2 client wrapper."""

from __future__ import annotations

import base64

import pytest
from moto import mock_aws

from panakoes_gpu_spawner.aws.ec2 import (
    GpuInstanceManager,
    InstanceDetails,
    _build_user_data,
    _coerce_state,
)
from tests.conftest import (
    TEST_INSTANCE_PROFILE,
    TEST_PROJECT_TAG,
    TEST_REGION,
    TEST_SPAWNER_TAG,
    TEST_WS_ENDPOINT,
    Ec2Environment,
    _provision_test_aws,
)


@pytest.mark.unit
def test_coerce_state_returns_known_states() -> None:
    """Known EC2 state strings pass through unchanged."""
    assert _coerce_state("pending") == "pending"
    assert _coerce_state("running") == "running"
    assert _coerce_state("shutting-down") == "shutting-down"
    assert _coerce_state("terminated") == "terminated"
    assert _coerce_state("stopping") == "stopping"
    assert _coerce_state("stopped") == "stopped"


@pytest.mark.unit
def test_coerce_state_falls_back_to_unknown() -> None:
    """Unknown / missing state strings collapse to `unknown`."""
    assert _coerce_state(None) == "unknown"
    assert _coerce_state("rebooting") == "unknown"
    assert _coerce_state("") == "unknown"


@pytest.mark.unit
def test_build_user_data_embeds_session_id_and_endpoint() -> None:
    """The user-data script embeds the session id and ws endpoint."""
    encoded = _build_user_data(
        session_id="sess_abc",
        ws_endpoint="wss://example.test",
    )
    decoded = base64.b64decode(encoded).decode("utf-8")
    assert "PANAKOES_SESSION_ID=sess_abc" in decoded
    assert "PANAKOES_WS_ENDPOINT=wss://example.test" in decoded
    assert decoded.startswith("#!/bin/bash")


def _make_manager(env: Ec2Environment) -> GpuInstanceManager:
    """Build a manager bound to a moto-provisioned environment."""
    return GpuInstanceManager(
        ami_id=env.ami_id,
        instance_type="t2.micro",
        security_group_id=env.security_group_id,
        subnet_id=env.subnet_id,
        iam_instance_profile=TEST_INSTANCE_PROFILE,
        project_tag=TEST_PROJECT_TAG,
        spawner_tag=TEST_SPAWNER_TAG,
        session_manager_ws_endpoint=TEST_WS_ENDPOINT,
        region_name=TEST_REGION,
        client=env.client,
    )


@pytest.mark.unit
def test_run_instance_returns_instance_id() -> None:
    """`run_instance` launches via moto and returns the new id."""
    with mock_aws():
        env = _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)
        manager = _make_manager(env)
        instance_id = manager.run_instance(session_id="sess_1", user_id="user_1")
        assert instance_id.startswith("i-")


@pytest.mark.unit
def test_run_instance_applies_tags() -> None:
    """Launched instances carry the four required tags."""
    with mock_aws():
        env = _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)
        manager = _make_manager(env)
        instance_id = manager.run_instance(session_id="sess_42", user_id="user_99")

        described = env.client.describe_instances(InstanceIds=[instance_id])
        instance = described["Reservations"][0]["Instances"][0]
        tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
        assert tags["Project"] == TEST_PROJECT_TAG
        assert tags["Spawner"] == TEST_SPAWNER_TAG
        assert tags["SessionId"] == "sess_42"
        assert tags["UserId"] == "user_99"


@pytest.mark.unit
def test_describe_instance_returns_details_for_known_instance() -> None:
    """`describe_instance` returns the state and tags."""
    with mock_aws():
        env = _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)
        manager = _make_manager(env)
        instance_id = manager.run_instance(session_id="sess_d", user_id="user_d")

        details = manager.describe_instance(instance_id)
        assert isinstance(details, InstanceDetails)
        assert details.instance_id == instance_id
        assert details.state in {"pending", "running"}
        assert details.tags["Spawner"] == TEST_SPAWNER_TAG
        assert details.tags["SessionId"] == "sess_d"


@pytest.mark.unit
def test_describe_instance_returns_none_for_unknown_id() -> None:
    """A non-existent id collapses to `None` (caller turns this into 404)."""
    with mock_aws():
        env = _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)
        manager = _make_manager(env)
        details = manager.describe_instance("i-0000000000000aaaa")
        assert details is None


@pytest.mark.unit
def test_terminate_instance_returns_state_pair() -> None:
    """`terminate_instance` returns the previous and current states."""
    with mock_aws():
        env = _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)
        manager = _make_manager(env)
        instance_id = manager.run_instance(session_id="sess_t", user_id="user_t")

        previous, current = manager.terminate_instance(instance_id)
        assert previous in {"pending", "running"}
        assert current in {"shutting-down", "terminated"}
