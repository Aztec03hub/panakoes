# transcriber-stream

GPU-side streaming transcription container. One container per session,
running on a session-spawned ``g4dn.xlarge`` Spot instance. Polls a
per-session SQS audio-frame queue, feeds 200 ms PCM frames into a
vendored LocalAgreement-2 ``OnlineASRProcessor`` wrapped around
``faster-whisper-large``, and emits partial and final transcripts over
the API Gateway WebSocket management API.

The full design is at
``docs/design/realtime-streaming-transcription.md`` (v7). This README
covers what this directory contains; do not duplicate the design here.

## Layout

```
services/transcriber-stream/
├── Dockerfile
├── NOTICE                                # Apache-2.0 attribution for vendored code
├── README.md                             # this file
├── pyproject.toml
├── src/panakoes_transcriber_stream/
│   ├── __init__.py
│   ├── asr_proxy.py                      # SeededOnlineASRProcessor (DEG-01 fix)
│   ├── config.py                         # env-driven Config dataclass
│   ├── lifecycle.py                      # LifecycleWatcher + SpotDrainHandler
│   ├── main.py                           # asyncio entrypoint (CMD)
│   ├── persistence.py                    # S3 + DDB session-row writer
│   ├── sqs_consumer.py                   # per-session SQS audio-frame poller
│   ├── transcribe.py                     # backend_factory adapter
│   ├── ws_publisher.py                   # PostToConnection + keepalive pings
│   └── vendor/
│       ├── README.md                     # vendor inventory + mod list
│       └── whisperlivekit/               # vendored Apache-2.0 subset
└── tests/
    ├── unit/
    └── integration/
```

The vendored subtree lives INSIDE the package so the relative imports
the wrapper code uses (``from .vendor.whisperlivekit...``) resolve at
runtime. The design v7 directory diagram shows ``vendor/`` at the
service-root level; we keep it inside the package for Python
importability and document the choice in the run report.

## Required env vars

| Var | Required | Default | Purpose |
|---|---|---|---|
| ``PANAKOES_SESSION_ID`` | yes | n/a | DDB session-row primary key |
| ``PANAKOES_CONNECTION_ID`` | yes | n/a | API GW WS connection id |
| ``FRAME_QUEUE_URL`` | yes | n/a | per-session SQS queue URL |
| ``WS_ENDPOINT`` | yes | n/a | API GW management endpoint URL |
| ``STREAMING_SESSIONS_TABLE`` | yes | n/a | DDB session-row table name |
| ``STREAMING_FRAME_POOL_TABLE`` | yes | n/a | DDB pool-state table name |
| ``TRANSCRIPTS_BUCKET`` | yes | n/a | S3 bucket for final transcripts |
| ``MODEL_SIZE`` | no | ``large-v2`` | faster-whisper variant |
| ``MODEL_CACHE_DIR`` | no | ``/opt/whisper/models`` | AMI-baked weights root |
| ``LANGUAGE_HINT`` | no | ``en`` | ISO 639-1 language code |
| ``MIN_CHUNK_SECONDS`` | no | ``1.0`` | optional cold-start short-circuit gate |
| ``MAX_CHUNK_SECONDS`` | no | ``30`` | forced flush cap |
| ``IDLE_SECONDS_BEFORE_EXIT`` | no | ``30`` | fallback exit if frames stop |
| ``KEEPALIVE_PING_SECONDS`` | no | ``540`` | GPU-side ping cadence (9 min) |
| ``BUFFER_TRIMMING`` | no | ``segment`` | LocalAgreement-2 trimming mode |
| ``BUFFER_TRIMMING_SEC`` | no | ``15.0`` | trimming threshold |
| ``AWS_REGION`` | no | ``us-east-1`` | boto3 region |
| ``LOG_LEVEL`` | no | ``INFO`` | stdlib log level |

## AMI contract

The container expects the host AMI to bake two assets:

* ``${MODEL_CACHE_DIR}/${MODEL_SIZE}-ct2/`` (default
  ``/opt/whisper/models/large-v2-ct2/``) containing the CTranslate2
  weights for ``faster-whisper-large-v2``.
* ``/opt/whisper/warmup-1s.wav`` (a 1-second 16 kHz mono PCM clip).

``main`` asserts both paths exist BEFORE invoking ``backend_factory``
so a missing bake fails fast with a clear ``ami-asset-missing`` error
instead of silently falling back to an 8-12 minute HuggingFace
download.

## Local dev

```bash
cd services/transcriber-stream
uv sync --no-dev=false
uv run pytest tests/
```

The integration tests use ``moto`` to fake S3 + DynamoDB + SQS; no real
AWS calls happen during the test run. Coverage is gated at 80 percent
on the wrapper code (the vendored subtree is excluded from the
coverage source).

## Container build

```bash
docker build -t panakoes-dev-transcriber-stream:test \
  -f services/transcriber-stream/Dockerfile \
  services/transcriber-stream/
```

The image is built with the service directory as its build context
because the container is self-contained; unlike ``transcriber-batch``,
there are no path-dep siblings to mount in.
