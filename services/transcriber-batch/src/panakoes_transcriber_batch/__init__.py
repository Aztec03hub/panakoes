"""AWS Batch GPU transcription worker.

The transcriber-batch container runs as a Batch job on a g4dn.xlarge
Spot instance launched from the gpu-transcribe AMI (per
infra/dev/batch/ and infra/ami/gpu-transcribe/). It reads a Batch-
supplied environment payload, downloads the source audio from S3,
invokes Whisper-large-v3 fp16 (model weights baked into the AMI at
``/opt/whisper/models/large-v3.pt``), writes the canonical Panakoes
transcript shape back to S3, and updates the streaming-sessions
DynamoDB row with the final status, duration, and word count.

Module map:

- ``config``: pydantic-settings env-driven configuration.
- ``s3``: S3 download / upload helpers (no business logic).
- ``sessions``: DDB streaming-sessions row update helpers.
- ``transcribe``: thin wrapper around the system-Python ``whisper``
  module with retry + structlog instrumentation. Lazy import so unit
  tests mock the seam without the AMI being present.
- ``main``: the Batch entrypoint that wires the four together.
"""

from panakoes_transcriber_batch.config import Settings, load_settings
from panakoes_transcriber_batch.main import main, run

__all__ = ["Settings", "load_settings", "main", "run"]
