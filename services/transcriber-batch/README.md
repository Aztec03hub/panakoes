# services/transcriber-batch

AWS Batch GPU worker that transcribes audio with Whisper-large-v3 fp16 (model
weights baked into the `panakoes-gpu-transcribe` AMI, per
`infra/ami/gpu-transcribe/`). Runs as a single-shot container job launched by
the dev Batch queue defined in `infra/dev/batch/`.

## Contract

The container is invoked by AWS Batch with the following environment payload:

| Env var | Source | Description |
|---|---|---|
| `S3_INPUT_URI` | Batch job parameter | `s3://<bucket>/<key>` of the source audio file |
| `S3_OUTPUT_PREFIX` | Batch job parameter | `s3://<bucket>/<prefix>` where transcript artifacts are written |
| `JOB_ID` | Batch job parameter | Batch job id for log correlation |
| `SESSION_ID` | Batch job parameter | Owning `panakoes-dev-streaming-sessions` row id |
| `MODEL_PATH` | Job definition env | On-disk path to the baked Whisper weights (default `/opt/whisper/models/large-v3.pt`) |
| `SESSIONS_TABLE` | Job definition env | DynamoDB table name for the streaming-sessions row |
| `AWS_REGION` | Job definition env | AWS region (default `us-east-1`) |
| `DEVICE` | Job definition env | Whisper compute device (default `cuda`; set `cpu` for local debugging) |
| `LOG_LEVEL` | Job definition env | structlog filter level (default `INFO`) |

On success the worker:

1. Marks the session row `status=active` (with `updated_at`).
2. Downloads `S3_INPUT_URI` to a tmpfs temp dir.
3. Loads Whisper from `MODEL_PATH`, transcribes with `fp16=True, word_timestamps=True`.
4. Writes a canonical transcript JSON to `S3_OUTPUT_PREFIX/transcript.json`.
5. Marks the session row `status=completed` with `transcript_uri`, `duration_seconds`, `word_count`.
6. Exits 0.

On failure the worker marks the session row `status=errored` with a short
`error_message` and exits 1; the Batch service surfaces the non-zero exit
as a job failure which the `FailedJobs` CloudWatch alarm picks up.

## Canonical transcript shape

```json
{
  "text": "hello world",
  "language": "en",
  "duration_seconds": 3.2,
  "word_count": 6,
  "segments": [
    {
      "text": "hello world",
      "start": 0.0,
      "end": 1.5,
      "words": [
        {"text": "hello", "start": 0.0, "end": 0.5},
        {"text": "world", "start": 0.6, "end": 1.5}
      ]
    }
  ]
}
```

This is a deliberate superset of the `panakoes_transcriber.TranscriptionResult`
dataclass: it adds `duration_seconds` and `word_count` for downstream
consumers that need them without re-deriving from the segments array.

## Why Whisper is not in `pyproject.toml`

The `openai-whisper` wheel is baked into the GPU AMI (`ami-0dee04ee5042c94cf`
and successors) alongside the CUDA-tuned PyTorch build and the Whisper model
weights. Pinning whisper in this service's deps would either:

- Duplicate that install (size + drift between the AMI bake and the container
  rebuild), or
- Shadow the AMI's CUDA-tuned PyTorch with a generic CPU build.

The `transcribe` module imports whisper lazily so unit tests can monkeypatch
the seam without the wheel being present. Production code paths exercise the
real wheel from the AMI's system Python at runtime.

## Local testing

```bash
cd services/transcriber-batch
uv sync --group dev
uv run pytest
```

The test suite uses `moto[s3,dynamodb]` for AWS mocking and stubs out the
whisper module entirely. No real AWS calls, no GPU required, full suite runs
in a few seconds. Coverage gate is 80%, enforced by `--cov-fail-under=80` in
`pyproject.toml`.

## How AWS Batch invokes the container

The job definition (see `infra/dev/batch/main.tf`) launches this container
with `vcpus=4`, `memory=15000`, and a `GPU=1` resource requirement on a
g4dn.xlarge Spot host running the `panakoes-gpu-transcribe` AMI. The
container's CMD is `python -m panakoes_transcriber_batch.main`; the AWS Batch
agent injects the env payload listed above and streams stdout / stderr to the
`/aws/batch/panakoes-dev-transcribe` CloudWatch log group.

To dispatch a job manually for verification (once the ECR image is published
and the Batch infra is fully applied):

```bash
aws batch submit-job \
  --job-name smoke-$(date -u +%s) \
  --job-queue panakoes-dev-transcribe-queue \
  --job-definition panakoes-dev-transcribe-batch \
  --container-overrides '{
    "environment": [
      {"name": "S3_INPUT_URI", "value": "s3://panakoes-dev-audio-uploads/audio/usr_xxx/ing_xxx/file.wav"},
      {"name": "S3_OUTPUT_PREFIX", "value": "s3://panakoes-dev-transcripts/sess_xxx"},
      {"name": "JOB_ID", "value": "smoke"},
      {"name": "SESSION_ID", "value": "sess_xxx"}
    ]
  }'
```

## References

- `infra/dev/batch/`: the Batch compute environment, job queue, and job definition that consume this image.
- `infra/ami/gpu-transcribe/`: the Packer build for the GPU AMI that bakes Whisper + CUDA + the NVIDIA driver.
- `docs/runbooks/gpu-ami-bake.md`: AMI rotation runbook.
- `services/transcriber-lib/`: the pluggable `Transcriber` Protocol this worker conforms to in spirit (it does not register an async backend because Batch jobs are inherently single-shot).
- `services/transcriber-groq/`: the hosted-API peer backend; useful shape reference for any future synchronous `Transcriber` implementation that lives in-process.
