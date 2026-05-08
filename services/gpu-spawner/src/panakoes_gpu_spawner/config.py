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
