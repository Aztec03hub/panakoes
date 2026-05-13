"""Shared pytest fixtures for the GPU Spawner.

Per ADR-018 we do NOT mock AWS in integration tests; we use moto to
stand up a real-shape EC2 in-memory. JWTs are signed with a
deterministic test secret and verified end-to-end.

A note on moto and EC2: moto's `RunInstances` accepts most of the real
launch flags (AMI id, instance type, market options, tag specs) but it
does NOT enforce the IAM tag-on-create conditions, instance profile
existence, or spot-pricing semantics that real AWS would. For those we
rely on the IAM policy in `infra/dev/iam` and on integration tests
against a real AWS account, which are deferred (see README).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import boto3
import jwt
import pytest
import pytest_asyncio
from moto import mock_aws
from panakoes_audit import MemoryAuditStore, reset_store, set_store

from panakoes_gpu_spawner.aws.ec2 import GpuInstanceManager
from panakoes_gpu_spawner.config import Settings
from panakoes_gpu_spawner.routes.spawn import (
    get_instance_manager,
    get_settings,
)

if TYPE_CHECKING:
    from httpx import AsyncClient

# Deterministic 32+ byte secret for HS256 in tests.
TEST_JWT_SECRET = "unit-test-shared-secret-32bytes-min!!"
TEST_JWT_ISSUER = "https://auth.panakoes.test"
TEST_JWT_AUDIENCE = "panakoes-api-test"

TEST_REGION = "us-east-1"
# AMI id used at the boto3 level. moto accepts arbitrary ami-* strings
# in its EC2 mock as long as it has a matching image fixture; the
# default account ships with a generic AMI we look up at fixture time.
TEST_AMI_ID = "ami-0123456789abcdef0"
TEST_INSTANCE_PROFILE = "panakoes-dev-gpu-instance"
TEST_PROJECT_TAG = "panakoes"
TEST_SPAWNER_TAG = "panakoes-dev-gpu-spawner"
TEST_WS_ENDPOINT = "wss://session-manager.panakoes.test"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin env vars to deterministic test values for every test."""
    for key in (
        "JWT_SECRET",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "GPU_AMI_ID",
        "GPU_SECURITY_GROUP_ID",
        "GPU_SUBNET_ID",
        "GPU_INSTANCE_TYPE",
        "GPU_IAM_INSTANCE_PROFILE",
        "PROJECT_TAG",
        "GPU_SPAWNER_TAG",
        "SESSION_MANAGER_WS_ENDPOINT",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AUDIT_BACKEND",
        "AUDIT_TABLE_NAME",
        "AUDIT_AWS_REGION",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("JWT_AUDIENCE", TEST_JWT_AUDIENCE)
    monkeypatch.setenv("GPU_AMI_ID", TEST_AMI_ID)
    monkeypatch.setenv("GPU_SECURITY_GROUP_ID", "sg-placeholder")
    monkeypatch.setenv("GPU_SUBNET_ID", "subnet-placeholder")
    monkeypatch.setenv("GPU_IAM_INSTANCE_PROFILE", TEST_INSTANCE_PROFILE)
    monkeypatch.setenv("PROJECT_TAG", TEST_PROJECT_TAG)
    monkeypatch.setenv("GPU_SPAWNER_TAG", TEST_SPAWNER_TAG)
    monkeypatch.setenv("SESSION_MANAGER_WS_ENDPOINT", TEST_WS_ENDPOINT)
    # moto needs *something* in the AWS creds slots so boto3 client init is happy.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_REGION)
    yield


@pytest.fixture(autouse=True)
def _audit_memory_store() -> Iterator[MemoryAuditStore]:
    """Swap the audit library to its in-memory store for every test."""
    store = MemoryAuditStore()
    set_store(store)
    yield store
    reset_store()


@pytest.fixture
def test_settings() -> Settings:
    """Return a `Settings` instance pinned to the test env values."""
    return Settings()


def _make_token(
    *,
    sub: str = "service:session-manager",
    actor_type: str = "service",
    jti: str = "session_abc",
    issuer: str = TEST_JWT_ISSUER,
    audience: str = TEST_JWT_AUDIENCE,
    secret: str = TEST_JWT_SECRET,
    expires_delta: timedelta = timedelta(hours=1),
    issued_at: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Sign an HS256 JWT with the documented payload shape."""
    issued = issued_at or datetime.now(UTC)
    expires = issued + expires_delta
    payload: dict[str, Any] = {
        "sub": sub,
        "actor_type": actor_type,
        "jti": jti,
        "iss": issuer,
        "aud": audience,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    if extra is not None:
        payload.update(extra)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def make_token() -> Any:
    """Expose `_make_token` as a fixture so tests can build custom JWTs."""
    return _make_token


@pytest.fixture
def service_auth_headers() -> dict[str, str]:
    """Authorization header signed as a service actor."""
    return {"Authorization": f"Bearer {_make_token(actor_type='service')}"}


@pytest.fixture
def user_auth_headers() -> dict[str, str]:
    """Authorization header signed as a user actor."""
    return {
        "Authorization": (
            f"Bearer {_make_token(sub='user_42', actor_type='user', jti='session_abc')}"
        )
    }


@pytest.fixture
def aws_mocks() -> Iterator[None]:
    """Activate moto for EC2 for the duration of a test."""
    with mock_aws():
        yield


@dataclass(frozen=True)
class Ec2Environment:
    """Bundle of moto-provisioned EC2 + IAM resources for tests.

    Holds the boto3 EC2 client plus the real-shape ids of the subnet,
    security group, and AMI moto generated. Tests build a manager
    against these ids so RunInstances actually succeeds inside the mock.
    """

    client: Any
    ami_id: str
    subnet_id: str
    security_group_id: str


def _provision_test_aws(region: str, instance_profile_name: str) -> Ec2Environment:
    """Provision the IAM instance profile + a real VPC/SG/subnet inside moto.

    Returns the EC2 client plus the freshly-minted resource ids. moto's
    `RunInstances` validates the AMI, subnet, and security group against
    its in-memory state; we cannot pass arbitrary `sg-...` strings.
    """
    iam = boto3.client("iam", region_name=region)
    iam.create_role(
        RoleName=instance_profile_name,
        AssumeRolePolicyDocument=(
            '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
            '"Principal":{"Service":"ec2.amazonaws.com"},'
            '"Action":"sts:AssumeRole"}]}'
        ),
    )
    iam.create_instance_profile(InstanceProfileName=instance_profile_name)
    iam.add_role_to_instance_profile(
        InstanceProfileName=instance_profile_name,
        RoleName=instance_profile_name,
    )

    ec2 = boto3.client("ec2", region_name=region)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]
    subnet = ec2.create_subnet(VpcId=vpc["VpcId"], CidrBlock="10.0.1.0/24")["Subnet"]
    sg = ec2.create_security_group(
        VpcId=vpc["VpcId"],
        GroupName="panakoes-gpu-spawner-test",
        Description="test sg",
    )
    images = ec2.describe_images(Owners=["amazon"]).get("Images", [])
    ami_id = images[0]["ImageId"] if images else "ami-12345678"
    return Ec2Environment(
        client=ec2,
        ami_id=ami_id,
        subnet_id=subnet["SubnetId"],
        security_group_id=sg["GroupId"],
    )


@pytest.fixture
def ec2_environment(aws_mocks: None) -> Ec2Environment:
    """Return a moto-backed EC2 environment with IAM + VPC + SG + subnet."""
    assert aws_mocks is None  # consume the fixture; moto must be active
    return _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)


@pytest_asyncio.fixture
async def async_client(
    ec2_environment: Ec2Environment,
    test_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    """Async httpx client with all dependencies overridden for tests."""
    from httpx import ASGITransport, AsyncClient

    from panakoes_gpu_spawner.main import app

    # Build the boto3-backed manager under the active moto mock so it
    # shares state with the ec2_environment fixture.
    manager = GpuInstanceManager(
        ami_id=ec2_environment.ami_id,
        instance_type="t2.micro",  # moto does not require a real GPU type
        security_group_id=ec2_environment.security_group_id,
        subnet_id=ec2_environment.subnet_id,
        iam_instance_profile=TEST_INSTANCE_PROFILE,
        project_tag=TEST_PROJECT_TAG,
        spawner_tag=TEST_SPAWNER_TAG,
        session_manager_ws_endpoint=TEST_WS_ENDPOINT,
        region_name=TEST_REGION,
        client=ec2_environment.client,
    )

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_instance_manager] = lambda: manager

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# Re-export so individual tests can call it without importing private helpers.
make_jwt = _make_token
