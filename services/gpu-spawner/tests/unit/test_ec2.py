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


_TEST_IMAGE_URI = (
    "659225405128.dkr.ecr.us-east-1.amazonaws.com/panakoes-dev-transcriber-stream:test-sha"
)
_TEST_MGMT_ENDPOINT = "https://abc123.execute-api.us-east-1.amazonaws.com/dev"
_TEST_SESSIONS_TABLE = "panakoes-dev-streaming-sessions"
_TEST_POOL_TABLE = "panakoes-dev-stream-frame-pool"
_TEST_TRANSCRIPTS_BUCKET = "panakoes-dev-transcripts-31a7d1c0"
_TEST_FRAME_QUEUE_URL = (
    "https://sqs.us-east-1.amazonaws.com/659225405128/panakoes-dev-stream-frames-pool-7"
)


def _decode_user_data(*, frame_queue_url: str = _TEST_FRAME_QUEUE_URL) -> str:
    """Build + base64-decode a representative UserData script for assertions."""
    encoded = _build_user_data(
        session_id="sess_abc",
        frame_queue_url=frame_queue_url,
        image_uri=_TEST_IMAGE_URI,
        ws_mgmt_endpoint=_TEST_MGMT_ENDPOINT,
        sessions_table=_TEST_SESSIONS_TABLE,
        frame_pool_table=_TEST_POOL_TABLE,
        transcripts_bucket=_TEST_TRANSCRIPTS_BUCKET,
        aws_region="us-east-1",
    )
    return base64.b64decode(encoded).decode("utf-8")


@pytest.mark.unit
def test_build_user_data_emits_shebang_and_safety_flags() -> None:
    """The script starts with a shebang and `set -euo pipefail`."""
    decoded = _decode_user_data()
    assert decoded.startswith("#!/bin/bash")
    assert "set -euo pipefail" in decoded


@pytest.mark.unit
def test_build_user_data_writes_all_eight_required_env_vars() -> None:
    """Every var the transcriber-stream README lists is in the env-file block."""
    decoded = _decode_user_data()
    # The block runs from the heredoc opener (`<<EOF`) to its terminator
    # (a line that is just `EOF`). Skip past the opener so we do not
    # confuse the bracketing `<<EOF` with the closer.
    heredoc_open = decoded.index("cat > /etc/panakoes.env <<EOF\n")
    body_start = heredoc_open + len("cat > /etc/panakoes.env <<EOF\n")
    body_end = decoded.index("\nEOF\n", body_start)
    env_block = decoded[body_start:body_end]
    expected = (
        "PANAKOES_SESSION_ID=",
        "PANAKOES_CONNECTION_ID=",
        "FRAME_QUEUE_URL=",
        "WS_ENDPOINT=",
        "STREAMING_SESSIONS_TABLE=",
        "STREAMING_FRAME_POOL_TABLE=",
        "TRANSCRIPTS_BUCKET=",
        "AWS_REGION=",
    )
    for name in expected:
        assert name in env_block, f"missing env var {name} in /etc/panakoes.env block"


@pytest.mark.unit
def test_build_user_data_embeds_session_id_and_frame_queue() -> None:
    """The script interpolates the session id and the claimed pool queue URL."""
    decoded = _decode_user_data()
    assert "sess_abc" in decoded
    assert _TEST_FRAME_QUEUE_URL in decoded


@pytest.mark.unit
def test_build_user_data_runs_container_with_gpus_and_env_file() -> None:
    """`docker run` carries `--gpus all`, `--env-file`, and the image URI."""
    decoded = _decode_user_data()
    assert "docker run" in decoded
    assert "--gpus all" in decoded
    assert "--env-file /etc/panakoes.env" in decoded
    assert _TEST_IMAGE_URI in decoded
    # Whisper model weights baked into the AMI are mounted read-only.
    assert "-v /opt/whisper:/opt/whisper:ro" in decoded


@pytest.mark.unit
def test_build_user_data_logs_in_to_ecr_using_registry_from_image_uri() -> None:
    """ECR registry host is extracted from the image URI; no hardcoded account."""
    decoded = _decode_user_data()
    assert "aws ecr get-login-password" in decoded
    assert "docker login" in decoded
    assert "659225405128.dkr.ecr.us-east-1.amazonaws.com" in decoded


@pytest.mark.unit
def test_build_user_data_shell_quotes_interpolated_values() -> None:
    """Injected metachars in a session id are shell-quoted, not interpreted."""
    encoded = _build_user_data(
        session_id="sess'; rm -rf /; echo '",
        frame_queue_url=_TEST_FRAME_QUEUE_URL,
        image_uri=_TEST_IMAGE_URI,
        ws_mgmt_endpoint=_TEST_MGMT_ENDPOINT,
        sessions_table=_TEST_SESSIONS_TABLE,
        frame_pool_table=_TEST_POOL_TABLE,
        transcripts_bucket=_TEST_TRANSCRIPTS_BUCKET,
        aws_region="us-east-1",
    )
    decoded = base64.b64decode(encoded).decode("utf-8")
    # The raw `rm -rf /` substring would appear, but it must be inside
    # a single-quoted shlex.quote() wrapper, not as a free shell token.
    assert "'sess'\"'\"'; rm -rf /; echo '\"'\"''" in decoded


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
        streaming_ws_mgmt_endpoint=_TEST_MGMT_ENDPOINT,
        stream_transcriber_image_uri=_TEST_IMAGE_URI,
        streaming_sessions_table=_TEST_SESSIONS_TABLE,
        stream_frame_pool_table=_TEST_POOL_TABLE,
        transcripts_bucket=_TEST_TRANSCRIPTS_BUCKET,
        region_name=TEST_REGION,
        client=env.client,
    )


@pytest.mark.unit
def test_run_instance_returns_instance_id() -> None:
    """`run_instance` launches via moto and returns the new id."""
    with mock_aws():
        env = _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)
        manager = _make_manager(env)
        instance_id = manager.run_instance(
            session_id="sess_1",
            user_id="user_1",
            frame_queue_url=_TEST_FRAME_QUEUE_URL,
        )
        assert instance_id.startswith("i-")


@pytest.mark.unit
def test_run_instance_applies_tags() -> None:
    """Launched instances carry the four required tags."""
    with mock_aws():
        env = _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)
        manager = _make_manager(env)
        instance_id = manager.run_instance(
            session_id="sess_42",
            user_id="user_99",
            frame_queue_url=_TEST_FRAME_QUEUE_URL,
        )

        described = env.client.describe_instances(InstanceIds=[instance_id])
        instance = described["Reservations"][0]["Instances"][0]
        tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
        assert tags["Project"] == TEST_PROJECT_TAG
        assert tags["Spawner"] == TEST_SPAWNER_TAG
        assert tags["SessionId"] == "sess_42"
        assert tags["UserId"] == "user_99"


@pytest.mark.unit
def test_run_instance_threads_frame_queue_url_into_user_data() -> None:
    """The pool queue URL flows through to the cloud-init UserData script."""
    with mock_aws():
        env = _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)
        manager = _make_manager(env)
        instance_id = manager.run_instance(
            session_id="sess_fq",
            user_id="user_fq",
            frame_queue_url=_TEST_FRAME_QUEUE_URL,
        )
        attrs = env.client.describe_instance_attribute(InstanceId=instance_id, Attribute="userData")
        # moto stores the value already base64-encoded; we re-decode once
        # if it round-trips, twice if it does not.
        encoded = attrs.get("UserData", {}).get("Value", "")
        first = base64.b64decode(encoded)
        try:
            decoded = base64.b64decode(first).decode("utf-8")
        except Exception:
            decoded = first.decode("utf-8")
        assert _TEST_FRAME_QUEUE_URL in decoded
        assert "FRAME_QUEUE_URL=" in decoded


@pytest.mark.unit
def test_describe_instance_returns_details_for_known_instance() -> None:
    """`describe_instance` returns the state and tags."""
    with mock_aws():
        env = _provision_test_aws(TEST_REGION, TEST_INSTANCE_PROFILE)
        manager = _make_manager(env)
        instance_id = manager.run_instance(
            session_id="sess_d",
            user_id="user_d",
            frame_queue_url=_TEST_FRAME_QUEUE_URL,
        )

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
        instance_id = manager.run_instance(
            session_id="sess_t",
            user_id="user_t",
            frame_queue_url=_TEST_FRAME_QUEUE_URL,
        )

        previous, current = manager.terminate_instance(instance_id)
        assert previous in {"pending", "running"}
        assert current in {"shutting-down", "terminated"}
