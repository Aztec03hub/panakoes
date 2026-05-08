"""Integration tests for `GET /spawn/{instance_id}`."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import (
    TEST_PROJECT_TAG,
    Ec2Environment,
)


@pytest.mark.integration
async def test_get_state_happy_path_for_service_actor(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
) -> None:
    """A service actor can read state for any instance the spawner launched."""
    create = await async_client.post(
        "/spawn",
        headers=service_auth_headers,
        json={"session_id": "sess_a", "user_id": "user_a"},
    )
    instance_id = create.json()["instance_id"]

    response = await async_client.get(f"/spawn/{instance_id}", headers=service_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["instance_id"] == instance_id
    assert body["state"] in {"pending", "running"}
    assert body["session_id"] == "sess_a"
    assert body["user_id"] == "user_a"


@pytest.mark.integration
async def test_get_state_user_actor_must_match_session(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
    make_token: Any,
) -> None:
    """A user actor whose `jti` matches the instance's SessionId can read it."""
    create = await async_client.post(
        "/spawn",
        headers=service_auth_headers,
        json={"session_id": "sess_match", "user_id": "user_x"},
    )
    instance_id = create.json()["instance_id"]

    user_token = make_token(sub="user_x", actor_type="user", jti="sess_match")
    response = await async_client.get(
        f"/spawn/{instance_id}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200


@pytest.mark.integration
async def test_get_state_user_actor_with_mismatched_session_gets_404(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
    make_token: Any,
) -> None:
    """A user actor on a different session sees 404, not the instance."""
    create = await async_client.post(
        "/spawn",
        headers=service_auth_headers,
        json={"session_id": "sess_owner", "user_id": "user_x"},
    )
    instance_id = create.json()["instance_id"]

    other_user_token = make_token(sub="user_y", actor_type="user", jti="sess_other")
    response = await async_client.get(
        f"/spawn/{instance_id}",
        headers={"Authorization": f"Bearer {other_user_token}"},
    )
    # 404 not 403: do not leak the existence of unrelated sessions' instances.
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_state_returns_404_for_unknown_id(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
) -> None:
    """An id that does not exist returns 404."""
    response = await async_client.get(
        "/spawn/i-0000000000000aaaa",
        headers=service_auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_state_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/spawn/i-0123456789abcdef0")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_state_rejects_instance_owned_by_other_spawner(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
    ec2_environment: Ec2Environment,
) -> None:
    """An instance without our `Spawner` tag is treated as not-found.

    Defence in depth: even if EC2 returned an instance we do not own
    (impossible under the current IAM policy, possible in a future
    misconfiguration), we never leak its state.
    """
    # Launch an instance directly via boto3 with a *different* spawner tag.
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
                    {"Key": "SessionId", "Value": "sess_foreign"},
                ],
            }
        ],
    )
    foreign_id = foreign["Instances"][0]["InstanceId"]

    response = await async_client.get(
        f"/spawn/{foreign_id}",
        headers=service_auth_headers,
    )
    assert response.status_code == 404
