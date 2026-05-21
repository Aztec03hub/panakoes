"""Settings module for the GPU Spawner service.

Environment-driven configuration via `pydantic-settings`. Every value
that varies between dev, staging, and prod (AMI id, subnet, security
group, IAM instance profile, JWT secret) is sourced from an env var so
the same image runs in all environments. There are NO hardcoded AMI
ids, subnet ids, or security group ids in this module: a misconfigured
deploy fails fast at first request, not after months of pointing at a
stale dev AMI.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the GPU Spawner.

    Override any field at runtime via the matching env var (e.g.
    `GPU_AMI_ID=ami-0123abcd`). The `model_config` block reads from a
    local `.env` file during development without leaking values into
    production deploys.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "gpu-spawner"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    # JWT signed by the Auth service (HS256, shared secret). The
    # session-manager mints service-actor JWTs to call this service.
    jwt_secret: str = "dev-only-secret-replace-in-production"  # noqa: S105
    jwt_issuer: str = "https://auth.panakoes.com"
    jwt_audience: str = "panakoes-api"

    # GPU launch configuration. All values come from Terraform-managed
    # infrastructure outputs and are injected via env vars at deploy
    # time. The empty defaults here exist so `Settings()` can construct
    # in tests; production deploys MUST override them.
    gpu_ami_id: str = ""
    gpu_security_group_id: str = ""
    gpu_subnet_id: str = ""
    gpu_instance_type: str = "g4dn.xlarge"
    gpu_iam_instance_profile: str = "panakoes-dev-gpu-instance"

    # Tag identity. `gpu_spawner_tag` MUST match the value the IAM
    # policy gates `RunInstances` and `TerminateInstances` on. Changing
    # it without updating IAM breaks both directions.
    project_tag: str = "panakoes"
    gpu_spawner_tag: str = "panakoes-dev-gpu-spawner"

    # Streaming endpoint the GPU instance connects back to over
    # WebSocket once it boots. The user-data script reads this from
    # instance metadata via the tag, no hardcoding in the AMI.
    session_manager_ws_endpoint: str = "wss://session-manager.panakoes.com"

    # SQS queue the EventBridge `streaming.session.connecting` events fan
    # into. Empty string disables the consumer (HTTP /spawn still works);
    # production deploys set this so auto-spawn fires on every $connect.
    spawn_queue_url: str = ""
    spawn_consumer_wait_seconds: int = 20

    # Concurrent-session ceiling. When the spawn callback finds this many
    # GPU EC2s already running (tagged with our Spawner tag), it evicts
    # the OLDEST one before launching the new one. Sized to fit comfortably
    # under the account's vCPU service quota: dev sits at 5 vCPU (G/VT
    # on-demand), each g4dn.xlarge eats 4, so the cap stays at 1 until the
    # quota is raised. The cap is read at every spawn so a `Settings()`
    # change (env var on the task def) takes effect on next deploy without
    # a code change. LRU-evict keeps the system self-healing in the face
    # of forgotten-tab sessions + browser closes that did not cleanly
    # tear down the WS.
    max_concurrent_sessions: int = 1

    # Streaming pipeline wiring. The spawn-callback claims a pool queue,
    # writes the frame_queue_url to the streaming-sessions row, and
    # generates UserData that pulls + runs the transcriber-stream
    # container with every env var that container needs at boot. Empty
    # defaults exist so `Settings()` constructs in tests; production
    # deploys MUST override them via env vars set on the ECS task.
    streaming_sessions_table: str = ""
    stream_frame_pool_table: str = ""
    transcripts_bucket: str = ""
    # PostToConnection management endpoint for the streaming WS API
    # Gateway (https://, NOT wss://). The transcriber container uses
    # this to push partial + final transcripts back to the SPA. Distinct
    # from `session_manager_ws_endpoint`, which is the legacy session
    # manager URL.
    streaming_ws_mgmt_endpoint: str = ""
    # ECR image URI for the transcriber-stream container the spawned
    # GPU instance pulls and runs (e.g.
    # `659225405128.dkr.ecr.us-east-1.amazonaws.com/panakoes-dev-transcriber-stream:main-04057c8`).
    stream_transcriber_image_uri: str = ""
