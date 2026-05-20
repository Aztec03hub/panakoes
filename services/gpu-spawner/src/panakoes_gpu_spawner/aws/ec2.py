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


def _build_user_data(*, session_id: str, ws_endpoint: str) -> str:
    """Build the cloud-init user-data script handed to the GPU instance.

    The script is the only place the streaming AMI learns about the
    session-manager WebSocket endpoint. We base64-encode the result the
    way the EC2 API expects.
    """
    script = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"echo 'PANAKOES_SESSION_ID={session_id}' >> /etc/panakoes.env\n"
        f"echo 'PANAKOES_WS_ENDPOINT={ws_endpoint}' >> /etc/panakoes.env\n"
        "systemctl enable --now panakoes-stream-transcriber.service\n"
    )
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
        region_name: str = "us-east-1",
        client: EC2Client | None = None,
    ) -> None:
        """Bind launch configuration and (optional) injected boto3 client."""
        self._ami_id = ami_id
        self._instance_type = instance_type
        self._security_group_id = security_group_id
        self._subnet_id = subnet_id
        self._iam_instance_profile = iam_instance_profile
        self._project_tag = project_tag
        self._spawner_tag = spawner_tag
        self._ws_endpoint = session_manager_ws_endpoint
        self._region_name = region_name
        self._client: EC2Client = (
            client if client is not None else boto3.client("ec2", region_name=region_name)
        )

    @property
    def spawner_tag(self) -> str:
        """The tag value we stamp on every instance we launch."""
        return self._spawner_tag

    def run_instance(self, *, session_id: str, user_id: str) -> str:
        """Launch one g4dn.xlarge Spot instance for `session_id`.

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
            "UserData": _build_user_data(
                session_id=session_id,
                ws_endpoint=self._ws_endpoint,
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
