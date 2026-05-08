"""Integration tests for `DELETE /spawn/{instance_id}`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore

from tests.conftest import (
    TEST_PROJECT_TAG,
    Ec2Environment,
)


@pytest.mark.integration
async def test_terminate_happy_path(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Service actor terminates an instance the spawner launched and audit fires."""
    create = await async_client.post(
        "/spawn",
        headers=service_auth_headers,
        json={"session_id": "sess_term", "user_id": "user_term"},
    )
    instance_id = create.json()["instance_id"]

    response = await async_client.delete(
        f"/spawn/{instance_id}",
        headers=service_auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["instance_id"] == instance_id
    assert body["previous_state"] in {"pending", "running"}
    assert body["current_state"] in {"shutting-down", "terminated"}

    actions = [event.action for event in _audit_memory_store.events]
    assert "gpu_spawner.terminated" in actions
    term_event = next(
        event for event in _audit_memory_store.events
        if event.action == "gpu_spawner.terminated"
    )
    assert term_event.resource_id == instance_id
    assert term_event.actor_type == "service"
    assert term_event.details["session_id"] == "sess_term"


@pytest.mark.integration
async def test_terminate_rejects_user_actor_token(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
    user_auth_headers: dict[str, str],
) -> None:
    """User actors cannot terminate (compute-spawning authority is service-only)."""
    create = await async_client.post(
        "/spawn",
        headers=service_auth_headers,
        json={"session_id": "sess_u", "user_id": "user_u"},
    )
    instance_id = create.json()["instance_id"]

    response = await async_client.delete(
        f"/spawn/{instance_id}",
        headers=user_auth_headers,
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_terminate_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.delete("/spawn/i-0123456789abcdef0")
    assert response.status_code == 401


@pytest.mark.integration
async def test_terminate_returns_404_for_unknown_id(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
) -> None:
    """Unknown instance id => 404."""
    response = await async_client.delete(
        "/spawn/i-0000000000000aaaa",
        headers=service_auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_terminate_refuses_instance_owned_by_other_spawner(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
    ec2_environment: Ec2Environment,
) -> None:
    """We refuse to terminate an instance we did not launch (confused-deputy guard)."""
    foreign = ec2_environment.client.run_instances(
        ImageId=ec2_environment.ami_id,
        InstanceType="t2.micro",
        MinCount=1,
        MaxCount=1,
        SubnetId=ec2_environment.subnet_id,
        SecurityGroupIds=[ec2_environment.security_group_id],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Project", "Value": TEST_PROJECT_TAG},
                    {"Key": "Spawner", "Value": "some-other-spawner"},
                ],
            }
        ],
    )
    foreign_id = foreign["Instances"][0]["InstanceId"]

    response = await async_client.delete(
        f"/spawn/{foreign_id}",
        headers=service_auth_headers,
    )
    assert response.status_code == 403

    # Confirm the foreign instance was NOT actually terminated.
    described = ec2_environment.client.describe_instances(InstanceIds=[foreign_id])
    state = described["Reservations"][0]["Instances"][0]["State"]["Name"]
    assert state in {"pending", "running"}
