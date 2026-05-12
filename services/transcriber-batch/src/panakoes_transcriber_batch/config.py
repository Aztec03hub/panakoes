"""Environment-driven configuration for the transcriber-batch worker.

AWS Batch injects the per-job dynamic values (``S3_INPUT_URI``,
``S3_OUTPUT_PREFIX``, ``JOB_ID``, ``SESSION_ID``) via the job
definition's ``parameters`` block. The static configuration
(``MODEL_PATH``, the DDB table name, the AWS region) comes from the
container's ``environment`` block in the job definition (see
``infra/dev/batch/main.tf``).

A misconfigured deploy fails at startup rather than after a multi-
minute model load, so every required field has no default and the
``load_settings`` call raises at process start.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Resolved environment configuration for a single Batch job invocation.

    Field naming matches the env-var contract documented in
    ``infra/dev/batch/main.tf``: per-job parameters in upper-snake
    case at the top of the block, container-wide config below. The
    pydantic-settings reader is case-insensitive so the Python field
    names (lower-snake) match the AWS-side env-var names (upper-snake)
    without an explicit alias map.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Per-job Batch parameters. Required; no defaults.
    s3_input_uri: str = Field(..., description="s3://<bucket>/<key> of the source audio")
    s3_output_prefix: str = Field(
        ...,
        description="s3://<bucket>/<prefix> where transcript artifacts are written",
    )
    job_id: str = Field(..., description="Batch job id, propagated for log correlation")
    session_id: str = Field(..., description="Owning streaming-sessions row id")

    # Container-static configuration. Defaults are safe for dev.
    model_path: str = Field(
        default="/opt/whisper/models/large-v3.pt",
        description="On-disk path to the Whisper-large-v3 fp16 weights baked into the AMI",
    )
    sessions_table: str = Field(
        default="panakoes-dev-streaming-sessions",
        description="DynamoDB table name for the streaming-sessions row updates",
    )
    aws_region: str = Field(default="us-east-1")
    log_level: str = Field(default="INFO")
    # Compute device the whisper.load_model call uses. The GPU AMI exposes a
    # CUDA-capable T4; CPU is the fallback for local debugging on a non-GPU
    # box.
    device: str = Field(default="cuda")


def load_settings() -> Settings:
    """Build a Settings instance from process environment variables.

    Wraps the pydantic-settings constructor so the call site stays a
    one-liner and tests can monkeypatch this function as a single seam.
    """
    return Settings()  # type: ignore[call-arg]
