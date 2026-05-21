"""EC2 client wrapper for spawning, describing, and terminating GPU instances.

Wraps three EC2 verbs the GPU Spawner cares about:

- `run_instances`: launches one Spot g4dn.xlarge from the configured
  AMI, tagged with `Project`, `Spawner`, `SessionId`, `UserId` so the
  IAM policy can gate subsequent lifecycle calls.
- `describe_instance`: looks up the latest state and tags for one
  instance. Returns `None` if the instance is not visible to our role,
  letting the route layer collapse not-found and not-authorized into
  the same 404 response.
- `terminate_instance`: shuts the instance down only after verifying
  the `Spawner` tag matches our identity. The IAM policy enforces this
  too, but we replicate the check at the application layer as defence
  in depth: a bug or policy regression that lets us terminate an
  unrelated instance still gets caught here.

Tests substitute the boto3 client with a moto-backed mock; production
callers let boto3 resolve the default credential chain.
"""

from __future__ import annotations

import base64
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import boto3

from panakoes_gpu_spawner.models import InstanceState

if TYPE_CHECKING:
    from mypy_boto3_ec2.client import EC2Client


# Per the design doc (gpu-spawner RunInstances failure paths table):
# map botocore error-code strings to a stable internal `error_code`
# the WebSocket-side error envelope can surface to the client. Codes
# absent from this map collapse to `unknown-spawn-failure`.
RUN_INSTANCES_ERROR_MAP: dict[str, str] = {
    "InsufficientInstanceCapacity": "spot-no-capacity",
    "MaxSpotInstanceCountExceeded": "spot-no-capacity",
    "SpotMaxPriceTooLow": "spot-no-capacity",
    "InvalidAMIID.NotFound": "ami-missing",
    "InvalidAMIID.Malformed": "ami-missing",
    "InvalidAMIID.Unavailable": "ami-missing",
    "VcpuLimitExceeded": "quota-exceeded",
    "RequestLimitExceeded": "quota-exceeded",
    "InstanceLimitExceeded": "quota-exceeded",
    "InvalidIamInstanceProfile.NotFound": "iam-not-ready",
    "InvalidIamInstanceProfile.Malformed": "iam-not-ready",
}


class RunInstancesFailure(RuntimeError):
    """Structured RunInstances failure surfaced to the spawn caller.

    Carries both the stable `error_code` (one of the values in
    `RUN_INSTANCES_ERROR_MAP`) and the raw `aws_error_code` returned
    by botocore so the run report + CloudWatch metric dimension can
    distinguish e.g. `InsufficientInstanceCapacity` from
    `MaxSpotInstanceCountExceeded` even though both collapse to the
    same client-visible `spot-no-capacity` code.
    """

    def __init__(self, *, error_code: str, aws_error_code: str, aws_message: str) -> None:
        super().__init__(f"{error_code} ({aws_error_code}): {aws_message}")
        self.error_code = error_code
        self.aws_error_code = aws_error_code
        self.aws_message = aws_message


def classify_run_instances_error(exc: Exception) -> RunInstancesFailure:
    """Map a botocore exception to a `RunInstancesFailure`.

    Accepts any exception that exposes a `.response['Error']['Code']`
    field (botocore.exceptions.ClientError). Other exception types
    collapse to `unknown-spawn-failure`.
    """
    aws_code = ""
    aws_message = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        err = response.get("Error") or {}
        if isinstance(err, dict):
            aws_code = str(err.get("Code") or "")
            aws_message = str(err.get("Message") or "")
    if not aws_message:
        aws_message = str(exc)
    classified_code = RUN_INSTANCES_ERROR_MAP.get(aws_code, "unknown-spawn-failure")
    return RunInstancesFailure(
        error_code=classified_code,
        aws_error_code=aws_code,
        aws_message=aws_message,
    )


@dataclass(frozen=True)
class InstanceDetails:
    """Subset of EC2 instance fields the spawner cares about."""

    instance_id: str
    state: InstanceState
    tags: dict[str, str]


def _coerce_state(raw: str | None) -> InstanceState:
    """Map an EC2 state string to our `InstanceState` literal.

    Anything unrecognised collapses to `unknown` so the response schema
    stays sound even if AWS adds a new state in the future.
    """
    known: set[str] = {
        "pending",
        "running",
        "shutting-down",
        "terminated",
        "stopping",
        "stopped",
    }
    if raw in known:
        return cast(InstanceState, raw)
    return "unknown"


def _ecr_registry_from_image_uri(image_uri: str) -> str:
    """Extract the ECR registry host from a full ECR image URI.

    Example:
        `659225405128.dkr.ecr.us-east-1.amazonaws.com/repo:tag`
        ->
        `659225405128.dkr.ecr.us-east-1.amazonaws.com`

    The registry host is the prefix before the first `/`. We do not
    hardcode the AWS account id; the image URI itself is the source of
    truth and the instance role's `ecr:GetAuthorizationToken` covers
    whichever registry we point it at.
    """
    return image_uri.split("/", 1)[0]


def _build_user_data(
    *,
    session_id: str,
    frame_queue_url: str,
    image_uri: str,
    ws_mgmt_endpoint: str,
    sessions_table: str,
    frame_pool_table: str,
    transcripts_bucket: str,
    aws_region: str = "us-east-1",
) -> str:
    """Build the cloud-init user-data script handed to the GPU instance.

    The script does four things on first boot:

    1. Tees stdout + stderr to `/var/log/panakoes-bootstrap.log` so the
       EC2 system log shows what happened on a failed boot.
    2. Authenticates docker to the ECR registry derived from
       `image_uri` and pulls the transcriber-stream image.
    3. Writes `/etc/panakoes.env` with every env var the container
       requires at boot (8 total).
    4. Runs the container detached with `--gpus all` and
       `--restart on-failure:1`, mounting the AMI's baked Whisper
       model weights at `/opt/whisper` read-only.

    All interpolated values are passed through `shlex.quote` so any
    future tag-injection or shell-metachar surprise in a session id /
    queue url / image tag does not break the script. We base64-encode
    the result the way the EC2 API expects.
    """
    sq = shlex.quote
    registry = _ecr_registry_from_image_uri(image_uri)
    script = f"""#!/bin/bash
set -euo pipefail
exec > >(tee -a /var/log/panakoes-bootstrap.log) 2>&1
echo "[panakoes-bootstrap] starting at $(date -u +%FT%TZ)"

# Ensure SSM agent is running so the operator can `aws ssm start-session`
# into the instance for diagnostics + `docker exec`. Deep Learning AMIs
# ship SSM agent via snap; we enable + start it defensively (no-op if
# already running). Failure is non-fatal: the rest of the bootstrap still
# runs even if SSM never comes up.
systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service || \\
    systemctl enable --now amazon-ssm-agent.service || \\
    true

REGION={sq(aws_region)}
REGISTRY={sq(registry)}
IMAGE_URI={sq(image_uri)}
SESSION_ID={sq(session_id)}
WS_MGMT_ENDPOINT={sq(ws_mgmt_endpoint)}

# Defensive: jq is required for the status-event JSON below. The
# Deep Learning Base GPU AMI ships it but we install it idempotently
# so a base-AMI swap does not silently break observability. Failure
# is non-fatal; post_status falls back to a no-op when jq is missing.
command -v jq >/dev/null 2>&1 || apt-get install -y --no-install-recommends jq || true

# Best-effort status publisher: posts a `status` envelope back to the
# session's WS connection via the API Gateway management API. The IAM
# instance profile carries `execute-api:ManageConnections`. Every
# failure path is silenced because the bootstrap MUST NOT abort over
# a status emit (e.g. client already disconnected, jq missing, AWS
# CLI throttled).
post_status() {{
    local stage="$1"
    local detail="$2"
    if [ -z "$WS_MGMT_ENDPOINT" ] || [ -z "$SESSION_ID" ]; then
        return 0
    fi
    if ! command -v jq >/dev/null 2>&1; then
        return 0
    fi
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
    local payload
    payload=$(printf '{{"type":"status","stage":%s,"detail":%s,"ts":%s}}' \\
        "$(printf '%s' "$stage" | jq -Rs .)" \\
        "$(printf '%s' "$detail" | jq -Rs .)" \\
        "$(printf '%s' "$ts" | jq -Rs .)")
    aws apigatewaymanagementapi post-to-connection \\
        --endpoint-url "$WS_MGMT_ENDPOINT" \\
        --connection-id "$SESSION_ID" \\
        --data "$payload" \\
        --region "$REGION" >/dev/null 2>&1 || true
}}

# Authenticate docker to ECR using the instance role.
aws ecr get-login-password --region "$REGION" \\
    | docker login --username AWS --password-stdin "$REGISTRY"
post_status "ec2-ecr-login" "ECR authenticated"

# Pre-warm the Whisper model + warmup clip in parallel with the docker
# pull. The model files live on the root EBS volume that was created from
# the whisper-stream AMI snapshot. EBS snapshots are lazy-loaded from S3 on
# first touch, so the very first read of a 3 GB model.bin takes ~5-8 min at
# ~10 MB/s when faster-whisper finally mmaps it. Reading the file through
# `dd` once at boot forces the snapshot fetch synchronously here so the
# container's `WhisperModel(...)` call hits a hot page cache and returns in
# the ~30-60 s GPU init cost the design budgets for.
#
# Pre-warm runs in the background so it overlaps with the `docker pull`
# (also ~3-5 min) instead of serializing. `wait` before `docker run`
# guarantees the model is hot before the container starts. Failure is
# non-fatal: if the warmup fails the container still works, just slow.
MODEL_BIN=/opt/whisper/models/large-v2-ct2/model.bin
# Drive EBS lazy-load with 8 parallel readers each covering a 400 MB
# slice of the 3 GB model.bin. Sequential single-dd was bottlenecked at
# ~10 MB/s (one S3 fetch at a time); 8 parallel readers concurrently
# request different blocks, so EBS pulls them in parallel from S3 and
# the wall-clock drops from ~9 min to ~1-2 min.
warmup_in_background() {{
    local slice
    for slice in 0 400 800 1200 1600 2000 2400 2800; do
        ( dd if="$MODEL_BIN" of=/dev/null bs=1M \\
            skip=$slice count=400 status=none 2>/dev/null || true ) &
    done
    wait
}}
post_status "ec2-prewarm-start" "Pre-warming Whisper model (8 parallel readers)"
warmup_in_background &
WARMUP_PID=$!

# Pull the transcriber-stream image baked by CI.
post_status "ec2-image-pull-start" "Pulling transcriber container image"
docker pull "$IMAGE_URI"
post_status "ec2-image-pull-done" "Container image pulled"

# Wait for the model pre-warm to finish before running the container.
echo "[panakoes-bootstrap] waiting for model pre-warm pid $WARMUP_PID at $(date -u +%FT%TZ)"
wait "$WARMUP_PID" || true
echo "[panakoes-bootstrap] model pre-warm complete at $(date -u +%FT%TZ)"
post_status "ec2-prewarm-done" "Pre-warm complete"

# Write the env file the container reads at startup. Every var the
# transcriber-stream README documents as required is set here.
cat > /etc/panakoes.env <<EOF
PANAKOES_SESSION_ID={sq(session_id)}
PANAKOES_CONNECTION_ID={sq(session_id)}
FRAME_QUEUE_URL={sq(frame_queue_url)}
WS_ENDPOINT={sq(ws_mgmt_endpoint)}
STREAMING_SESSIONS_TABLE={sq(sessions_table)}
STREAMING_FRAME_POOL_TABLE={sq(frame_pool_table)}
TRANSCRIPTS_BUCKET={sq(transcripts_bucket)}
AWS_REGION={sq(aws_region)}
AWS_DEFAULT_REGION={sq(aws_region)}
EOF
chmod 600 /etc/panakoes.env

# Run the container detached. Logs go to journald via the default
# docker logging driver so `journalctl -u docker` shows container
# output. The Whisper model weights are baked into the AMI at
# /opt/whisper and mounted read-only.
docker run -d \\
    --name panakoes-stream-transcriber \\
    --restart on-failure:1 \\
    --gpus all \\
    --env-file /etc/panakoes.env \\
    --log-driver awslogs \\
    --log-opt awslogs-region="$REGION" \\
    --log-opt awslogs-group=/panakoes/dev/transcriber-stream \\
    --log-opt awslogs-stream={sq(session_id)} \\
    -v /opt/whisper:/opt/whisper:ro \\
    "$IMAGE_URI"

echo "[panakoes-bootstrap] container launched at $(date -u +%FT%TZ)"
post_status "ec2-container-launched" "Container starting"
"""
    return base64.b64encode(script.encode("utf-8")).decode("ascii")


class GpuInstanceManager:
    """Thin EC2 wrapper centralising the spawner's three verbs."""

    def __init__(
        self,
        *,
        ami_id: str,
        instance_type: str,
        security_group_id: str,
        subnet_id: str,
        iam_instance_profile: str,
        project_tag: str,
        spawner_tag: str,
        session_manager_ws_endpoint: str,
        streaming_ws_mgmt_endpoint: str = "",
        stream_transcriber_image_uri: str = "",
        streaming_sessions_table: str = "",
        stream_frame_pool_table: str = "",
        transcripts_bucket: str = "",
        region_name: str = "us-east-1",
        client: EC2Client | None = None,
    ) -> None:
        """Bind launch configuration and (optional) injected boto3 client.

        The streaming-pipeline parameters (`streaming_ws_mgmt_endpoint`,
        `stream_transcriber_image_uri`, `streaming_sessions_table`,
        `stream_frame_pool_table`, `transcripts_bucket`) feed into the
        UserData script for spawned instances. They default to empty
        strings so legacy callers (the HTTP `POST /spawn` route, unit
        tests that do not exercise the spawn-callback path) still
        construct, but a real spawn against an empty value produces a
        non-functional GPU container; production wires every value
        through the Settings module.
        """
        self._ami_id = ami_id
        self._instance_type = instance_type
        self._security_group_id = security_group_id
        self._subnet_id = subnet_id
        self._iam_instance_profile = iam_instance_profile
        self._project_tag = project_tag
        self._spawner_tag = spawner_tag
        self._ws_endpoint = session_manager_ws_endpoint
        self._streaming_ws_mgmt_endpoint = streaming_ws_mgmt_endpoint
        self._stream_transcriber_image_uri = stream_transcriber_image_uri
        self._streaming_sessions_table = streaming_sessions_table
        self._stream_frame_pool_table = stream_frame_pool_table
        self._transcripts_bucket = transcripts_bucket
        self._region_name = region_name
        self._client: EC2Client = (
            client if client is not None else boto3.client("ec2", region_name=region_name)
        )

    @property
    def spawner_tag(self) -> str:
        """The tag value we stamp on every instance we launch."""
        return self._spawner_tag

    def run_instance(self, *, session_id: str, user_id: str, frame_queue_url: str) -> str:
        """Launch one g4dn.xlarge Spot instance for `session_id`.

        `frame_queue_url` is the pool queue the streaming-router fans
        audio frames into; the spawn callback claims it from the pool
        before calling this method. It flows through to the cloud-init
        UserData as `FRAME_QUEUE_URL` so the transcriber-stream
        container can subscribe at boot.

        Returns the instance id. Tags are applied at launch (not after
        creation) so the IAM policy's `aws:RequestTag/Spawner` condition
        is satisfied; without this the launch is denied.
        """
        tag_specifications: list[dict[str, Any]] = [
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Project", "Value": self._project_tag},
                    {"Key": "Spawner", "Value": self._spawner_tag},
                    {"Key": "SessionId", "Value": session_id},
                    {"Key": "UserId", "Value": user_id},
                ],
            },
            {
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "Project", "Value": self._project_tag},
                    {"Key": "Spawner", "Value": self._spawner_tag},
                    {"Key": "SessionId", "Value": session_id},
                ],
            },
        ]

        kwargs: dict[str, Any] = {
            "ImageId": self._ami_id,
            "InstanceType": self._instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "SubnetId": self._subnet_id,
            "SecurityGroupIds": [self._security_group_id],
            "IamInstanceProfile": {"Name": self._iam_instance_profile},
            "InstanceMarketOptions": {
                "MarketType": "spot",
                "SpotOptions": {"SpotInstanceType": "one-time"},
            },
            # IMDSv2 with hop-limit 2. The transcriber-stream container
            # runs on the default Docker bridge network, which adds one
            # IP hop between the container and the IMDS endpoint at
            # 169.254.169.254. With the default hop-limit of 1, IMDSv2
            # rejects the request and boto3 inside the container cannot
            # obtain instance-role credentials. Result: PostToConnection
            # + SQS + DDB all silently fail and the container never
            # emits its `ready` message. Hop-limit 2 is the AWS-blessed
            # value for IMDSv2 inside Docker containers.
            "MetadataOptions": {
                "HttpTokens": "required",
                "HttpPutResponseHopLimit": 2,
                "HttpEndpoint": "enabled",
            },
            "UserData": _build_user_data(
                session_id=session_id,
                frame_queue_url=frame_queue_url,
                image_uri=self._stream_transcriber_image_uri,
                ws_mgmt_endpoint=self._streaming_ws_mgmt_endpoint,
                sessions_table=self._streaming_sessions_table,
                frame_pool_table=self._stream_frame_pool_table,
                transcripts_bucket=self._transcripts_bucket,
                aws_region=self._region_name,
            ),
            "TagSpecifications": tag_specifications,
        }

        try:
            response = self._client.run_instances(**kwargs)
        except self._client.exceptions.ClientError as exc:
            # Translate the raw botocore exception into the design's
            # stable error_code taxonomy (RUN_INSTANCES_ERROR_MAP) so
            # the route + observability layers do not need to import
            # botocore exception classes themselves.
            raise classify_run_instances_error(exc) from exc
        except Exception as exc:  # pragma: no cover - catch-all for non-botocore
            # Non-botocore exceptions (network errors, unexpected
            # exception types) still need the structured envelope so
            # the WS error message is consistent.
            if type(exc).__name__ == "ClientError":
                raise classify_run_instances_error(exc) from exc
            raise
        instances = response.get("Instances", [])
        if not instances:
            raise RuntimeError("ec2:RunInstances returned no instances")
        instance_id = instances[0].get("InstanceId")
        if not isinstance(instance_id, str):
            raise RuntimeError("ec2:RunInstances response missing InstanceId")
        return instance_id

    def describe_instance(self, instance_id: str) -> InstanceDetails | None:
        """Return state + tags for `instance_id` or `None` if not found."""
        try:
            response = self._client.describe_instances(InstanceIds=[instance_id])
        except self._client.exceptions.ClientError:
            return None

        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                if instance.get("InstanceId") != instance_id:
                    continue
                state_name: str | None = None
                state_block = instance.get("State")
                if isinstance(state_block, dict):
                    raw = state_block.get("Name")
                    if isinstance(raw, str):
                        state_name = raw
                tags: dict[str, str] = {}
                for tag in instance.get("Tags", []) or []:
                    key = tag.get("Key")
                    value = tag.get("Value")
                    if isinstance(key, str) and isinstance(value, str):
                        tags[key] = value
                return InstanceDetails(
                    instance_id=instance_id,
                    state=_coerce_state(state_name),
                    tags=tags,
                )
        return None

    def terminate_instance(self, instance_id: str) -> tuple[InstanceState, InstanceState]:
        """Terminate `instance_id` and return `(previous_state, current_state)`.

        Caller is expected to have already verified ownership via the
        instance's `Spawner` tag. We pass through whatever EC2 returns
        for the state pair.
        """
        response = self._client.terminate_instances(InstanceIds=[instance_id])
        terminating = response.get("TerminatingInstances", [])
        if not terminating:
            raise RuntimeError("ec2:TerminateInstances returned no instances")
        entry = terminating[0]
        previous = None
        current = None
        prev_block = entry.get("PreviousState")
        curr_block = entry.get("CurrentState")
        if isinstance(prev_block, dict):
            raw_prev = prev_block.get("Name")
            if isinstance(raw_prev, str):
                previous = raw_prev
        if isinstance(curr_block, dict):
            raw_curr = curr_block.get("Name")
            if isinstance(raw_curr, str):
                current = raw_curr
        return _coerce_state(previous), _coerce_state(current)
