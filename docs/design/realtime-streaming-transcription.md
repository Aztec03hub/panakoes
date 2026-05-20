# Real-time streaming transcription (design doc)

> **Status:** Proposed. This is a design doc, not an implementation. No code lands until the design passes architect-review + Phil gate + adversarial-review + Phil gate per `WORKFLOW.md` section 5.6.
>
> **Why now:** the chunked-batch pseudo-realtime path (`/realtime`, shipped 2026-05-20 in PR #449) yields ~50-100 seconds per 8-second chunk because each chunk is a fresh AWS Batch job that pays a cold container start + a 3 GB Whisper-weights download. Phil's verdict (verbatim 2026-05-20): "even 50-second chunk processing time is ABSOLUTELY fucking criminally unacceptable." We are now wiring the data plane that the existing streaming control plane has been waiting for.

## TL;DR

A long-running `transcriber-stream` container runs on a session-spawned `g4dn.xlarge` Spot instance. faster-whisper-large + Silero VAD process 200 ms PCM frames as they arrive, emit partial transcripts every ~500 ms via the API Gateway WebSocket management API, and tear down on `$disconnect`. The existing `streaming-router`, `gpu-spawner`, `session-manager`, `ws-authorizer`, IAM roles, and ECR repo are already wired and tested; **the only vaporware is the GPU container itself**, the gpu-spawner ➜ EventBridge auto-spawn trigger, and the SPA WebSocket client.

## Existing state (already wired, do NOT rebuild)

| Component | Location | What it does |
|---|---|---|
| WebSocket API GW | `infra/dev/api-gateway-ws/` | `wss://a75u8kj039.execute-api.us-east-1.amazonaws.com/dev` with `$connect`, `audio-frame`, `transcript-request`, `$default`, `$disconnect` routes |
| `ws-authorizer` Lambda | `services/ws-authorizer/` | HS256 JWT validate on `$connect`; rejects unauthenticated handshakes |
| `streaming-router` Lambda | `services/streaming-router/router.py` (246 LOC) | `$connect` ➜ DDB session row + EventBridge `streaming.session.connecting`. `audio-frame` ➜ SQS `audio-frame-queue` with `session_id` MessageAttribute. `transcript-request` ➜ reads `last_transcript` from DDB (stub today). `$disconnect` ➜ DDB row status=disconnected |
| `session-manager` ECS service | `services/session-manager/` | HTTP API for session lifecycle queries (read-mostly today) |
| `gpu-spawner` ECS service | `services/gpu-spawner/` | HTTP endpoint that calls `ec2:RunInstances` to spawn a `g4dn.xlarge`. EventBridge subscription wiring is NOT yet present |
| ECR `panakoes-dev-transcriber-stream` | `infra/dev/ecr/` | Empty repo; no images |
| IAM `panakoes-dev-transcriber-stream-task` | `infra/dev/iam/main.tf:861` | Role with S3 PutObject (transcripts), DDB UpdateItem on streaming-sessions, CloudWatch PutMetricData, KMS Encrypt. **Missing: `execute-api:ManageConnections` on the WS API stage**; required for `PostToConnection`. |
| Observability log group | `infra/dev/observability/` | `/aws/transcriber-stream/<env>` already provisioned |

## Goals

1. **Sub-second perceived latency.** From the moment a user finishes speaking a word, a partial transcript appears in the browser within ~500-1000 ms.
2. **One persistent GPU per active session.** No fresh container per chunk; the container starts once, loads the model once, processes continuously, tears down on `$disconnect`.
3. **Reuse the existing control plane.** `streaming-router`, `ws-authorizer`, `session-manager`, IAM, ECR are already in place. The work is the data plane and one EventBridge wire-up.
4. **No regression on the async path.** The Whisper-on-Batch async ingestion path stays unchanged. `/upload` and the legacy `/ingestion/[id]` view continue to work.
5. **Cost-bounded.** Each session is one Spot `g4dn.xlarge` at ~$0.15/hr. Sessions terminate within ~30 s of `$disconnect`. Idle reaper kills orphans at 60 min.

## Non-goals

- Diarization, speaker-attribution, language auto-detect-with-switch. Single-language, single-speaker is the v1 target. (Faster-whisper-large supports diarization via pyannote but adds latency; deferred.)
- Multi-session per GPU. Each session gets its own `g4dn.xlarge`. Concurrency capacity is governed by EC2 Spot quota, not by stuffing N sessions onto one instance.
- Re-implementing the `Transcriber` abstraction. The streaming backend slots into the existing `transcriber-abstraction.md` interface as an additional implementation, but this design doc does not change the interface.
- Replacing the chunked-batch `/realtime` page. The two paths can coexist while streaming proves out. The SPA WebSocket client lands at `/realtime` and superseeds the chunked-batch implementation in the same route.

## Architecture

```
┌────────────────┐                                              ┌─────────────────────────────┐
│ Browser        │                                              │ AWS                         │
│ admin SPA      │                                              │                             │
│ /realtime      │                                              │                             │
├────────────────┤                                              │                             │
│ AudioWorklet   │ ──────── 200ms PCM frames ───────────►       │                             │
│ 16 kHz mono    │  WSS / wss://...execute-api.../dev           │                             │
│                │                                              │                             │
│ WebSocket cli  │ ◄─── partial transcripts (JSON) ────────     │                             │
└────────────────┘                                              │   ┌─────────────────────┐   │
       ▲                                                        │   │ API GW v2 WebSocket │   │
       │                                                        │   ├─────────────────────┤   │
       │                                                        │   │ ws-authorizer (JWT) │   │
       │                                                        │   │ streaming-router    │───┼──┐
       │                                                        │   │   $connect ─► DDB   │   │  │
       │ PostToConnection                                       │   │   audio-frame ─►SQS │   │  │ EventBridge
       │ (execute-api:ManageConnections)                        │   │   $disconnect ─►DDB │   │  │ streaming.session.connecting
       │                                                        │   └─────────────────────┘   │  │
       │                                                        │            │                │  │
       │                                                        │            ▼                │  ▼
       │                                                        │   ┌─────────────────────┐   │  ┌──────────────────┐
       │                                                        │   │ SQS audio-frame-q   │   │  │ gpu-spawner ECS  │
       │                                                        │   │ (MessageAttr:       │   │  │ EventBridge      │
       │                                                        │   │  session_id)        │   │  │ subscription     │
       │                                                        │   └─────────────────────┘   │  │  + RunInstances  │
       │                                                        │            │                │  └──────────────────┘
       │                                                        │            │ polled by               │
       │                                                        │            │ matching                │ spawn
       │                                                        │            ▼ session                 ▼
       │                                                        │   ┌──────────────────────────────────────────┐
       │                                                        │   │ g4dn.xlarge Spot (per session)           │
       │                                                        │   ├──────────────────────────────────────────┤
       │                                                        │   │ transcriber-stream container             │
       │                                                        │   │   - polls SQS filtered by session_id     │
       │                                                        │   │   - buffers PCM frames                   │
       │                                                        │   │   - Silero VAD ➜ faster-whisper-large    │
       │                                                        │   │   - emits partials via PostToConnection  │
       └────────────────────────────────────────────────────────┼───│   - writes final transcript ➜ S3 + DDB   │
                                                                │   │   - exits on session row disconnected    │
                                                                │   └──────────────────────────────────────────┘
                                                                └─────────────────────────────┘
```

## Detailed design

### Audio frame format and transport

- **Capture:** browser `AudioWorklet` on the microphone stream. Downsample to 16 kHz mono signed 16-bit PCM. (`AudioContext({sampleRate: 16000})` works in modern Chromium + Firefox; Safari needs `AudioBuffer.resampleAsync` polyfill, accepted v1 limitation: Safari behind a "use Chrome/Firefox" notice.)
- **Frame size:** 200 ms ➜ 6400 bytes raw PCM ➜ ~8.5 KB base64. Why 200 ms: Silero VAD's natural frame size, balances WS overhead (~250 frames/min) with model emit cadence (every ~500 ms once speech detected).
- **Wire format:** JSON over WS `audio-frame` route. Body shape:
  ```json
  {
    "seq": 142,
    "ts_ms": 1779255300000,
    "pcm_b64": "<base64-encoded raw 16kHz mono s16le>"
  }
  ```
  Total ~8.6 KB per frame at 5 Hz = ~43 KB/s upstream. Well under API Gateway WS frame size limits (32 KB).
- **Server-side:** `streaming-router._route_audio_frame` already forwards the body to SQS with `session_id` MessageAttribute (no change needed).

### transcriber-stream container

Location: `services/transcriber-stream/` (new). pyproject layout matches the existing `transcriber-batch` service skeleton.

```
services/transcriber-stream/
├── Dockerfile
├── pyproject.toml
├── README.md
├── src/panakoes_transcriber_stream/
│   ├── __init__.py
│   ├── main.py              # asyncio entrypoint
│   ├── config.py            # env vars: SESSION_ID, CONNECTION_ID, FRAME_QUEUE_URL, WS_ENDPOINT, etc.
│   ├── sqs_consumer.py      # async SQS poller, filters by session_id
│   ├── vad.py               # Silero VAD wrapper
│   ├── transcribe.py        # faster-whisper-large wrapper, emits partial + final segments
│   ├── ws_publisher.py      # ApiGatewayManagementApi.PostToConnection client
│   ├── persistence.py       # S3 upload + DDB UpdateItem for last_transcript and final
│   └── lifecycle.py         # session-row watcher; exits on status=disconnected
└── tests/
    ├── test_sqs_consumer.py
    ├── test_vad.py
    ├── test_ws_publisher.py
    └── test_lifecycle.py
```

**Runtime contract (env vars):**
- `PANAKOES_SESSION_ID`: the DDB session row's primary key. Required.
- `PANAKOES_CONNECTION_ID`: the API GW WebSocket connection id (= session_id today). Required.
- `FRAME_QUEUE_URL`: SQS audio-frame queue URL. Required.
- `WS_ENDPOINT`: API GW management endpoint, e.g. `https://a75u8kj039.execute-api.us-east-1.amazonaws.com/dev`. Required.
- `STREAMING_SESSIONS_TABLE`: DDB table name. Required.
- `TRANSCRIPTS_BUCKET`: S3 bucket for final transcripts. Required.
- `MODEL_PATH`: path to faster-whisper-large weights. Default `/opt/whisper/models/large-v3-ct2` (CTranslate2 format). Required at startup.
- `MAX_BUFFER_SECONDS`: rolling buffer cap before forced flush. Default 30. Required.
- `IDLE_SECONDS_BEFORE_EXIT`: tear down if no frames for N seconds AND session row says disconnected. Default 30. Required.

**Main loop (asyncio):**
```python
# pseudocode
async def main():
    cfg = load_config_from_env()
    model = load_faster_whisper(cfg.model_path)   # ~35s warm-up
    vad = SileroVAD()
    sqs = SQSConsumer(cfg.frame_queue_url, session_id=cfg.session_id)
    ws = WsPublisher(cfg.ws_endpoint, cfg.connection_id)
    persist = Persistence(cfg.transcripts_bucket, cfg.sessions_table, cfg.session_id)
    lifecycle = LifecycleWatcher(cfg.sessions_table, cfg.session_id)

    await ws.send({"type": "ready"})
    audio_buffer = bytearray()
    async for frame in sqs.frames():       # yields decoded PCM bytes
        audio_buffer.extend(frame.pcm)
        # VAD-driven flush: when Silero detects a speech-end boundary OR
        # buffer length exceeds MAX_BUFFER_SECONDS, run faster-whisper on
        # the buffered audio and emit the partial.
        if vad.is_speech_boundary(audio_buffer) or duration(audio_buffer) > cfg.max_buffer_seconds:
            text, is_final, words = model.transcribe_stream(audio_buffer)
            await ws.send({"type": "partial" if not is_final else "final", "text": text, "words": words})
            await persist.update_last_transcript(text)
            if is_final:
                audio_buffer.clear()  # only on a confirmed sentence end
        if await lifecycle.should_exit():
            break

    await persist.write_final()
    await ws.send({"type": "ended"})
```

**Latency budget (warm):**
- WebSocket round-trip browser ➜ API GW: 30-50 ms
- streaming-router Lambda: 10-30 ms (no cold start; provisioned-concurrency=1 is cheap)
- SQS send + receive: 50-150 ms
- VAD + buffer step: < 10 ms
- faster-whisper-large incremental transcribe (200 ms window): 50-150 ms on T4 GPU (CTranslate2 fp16)
- WS PostToConnection back: 30-50 ms
- **Total perceived: 200-500 ms** for partials, ~500-1000 ms for finals.

**Cold-start budget (first frame after spawn):**
- EC2 RunInstances + Spot fulfillment: 30-90 s
- Instance boot + cloud-init: 30-60 s
- Container start: 5 s
- Model load (CTranslate2 fp16, weights pre-baked into AMI): 35 s
- **Total: 100-190 s before the first partial.** Acceptable for v1; pre-warmed Spot pool is a v2 optimization.

### gpu-spawner enhancement

Today `gpu-spawner` exposes an HTTP `/spawn` endpoint. It needs an EventBridge subscription so it auto-spawns on `streaming.session.connecting`.

**New file: `services/gpu-spawner/src/panakoes_gpu_spawner/eventbridge_consumer.py`**

```python
# Reads SQS-EventBridge subscription (EventBridge ➜ SQS pattern) so the
# spawner is event-driven without coupling to a Lambda. The ECS service
# polls one SQS queue; each message = one session to spawn.
async def consume_loop(spawn_queue_url: str):
    while True:
        msgs = sqs.receive_message(QueueUrl=spawn_queue_url, ...)
        for m in msgs.get("Messages", []):
            evt = json.loads(m["Body"])
            session_id = evt["detail"]["session_id"]
            await spawn_session_instance(session_id)
            sqs.delete_message(...)
```

**EC2 user-data on spawned instance:**
- Pulls the `transcriber-stream:latest` image from ECR.
- Looks up `connection_id` from the DDB session row (= `session_id` in current schema).
- Runs the container with env vars set per above.
- Configures Docker `--restart=on-failure` with backoff so a Whisper OOM gets one retry before instance tear-down.

**Instance tag:** `panakoes:session-id=<id>`. Lets the gpu-spawner idle-reaper find orphaned instances.

**Idle reaper:** existing `gpu-spawner` cron OR a new EventBridge schedule that runs every 5 min, lists instances tagged with `panakoes:session-id`, joins against DDB session rows, terminates any instance whose session row is `disconnected` and has been so for ≥ 5 min OR has been `connecting` without a `connected` flip for ≥ 10 min (stuck spawn).

### SPA WebSocket client (replacing chunked-batch on `/realtime`)

`services/admin/src/routes/realtime/+page.svelte` gets rewritten. The existing `lib/realtime-session.ts` (chunked-batch) is replaced with `lib/streaming-session.ts`.

**Public API of `lib/streaming-session.ts`:**

```typescript
export type StreamStatus = "idle" | "connecting" | "spawning-gpu" | "ready" | "transcribing" | "ended" | "failed";

export interface StreamingSession {
  start(): Promise<void>;
  stop(): Promise<void>;
  readonly status: StreamStatus;
  readonly partialText: string;     // running concat of all partials
  readonly finalSegments: string[]; // sentence-final transcripts
  onStatusChange?: (s: StreamStatus) => void;
  onTranscript?: (msg: { type: "partial" | "final"; text: string }) => void;
  onError?: (err: Error) => void;
}
```

**Wire-up:**
- Opens `wss://...execute-api.../dev?token=<JWT>` (token in query string, picked up by `ws-authorizer`).
- Acquires mic via `getUserMedia({ audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true } })`.
- Pipes through an `AudioWorklet` that emits 200 ms PCM frames.
- Each frame ➜ `socket.send(JSON.stringify({action: "audio-frame", seq, ts_ms, pcm_b64}))`.
- Receives `{type: "ready" | "partial" | "final" | "ended" | "error", ...}` messages and updates state.
- On user-clicked Stop OR `socket.close()`, emits the `$disconnect` route.

**UI:**
- Active mic button (pulsing).
- Live partial text in a big card, sentence-final segments appended below it.
- Status badge (idle ➜ connecting ➜ spawning-gpu ➜ ready ➜ transcribing ➜ ended).
- "Copy transcript" button shown after stop.

### IAM additions

1. `panakoes-dev-transcriber-stream-task` role gains `execute-api:ManageConnections` on `arn:aws:execute-api:us-east-1:<acct>:<ws-api-id>/dev/POST/@connections/*`. Required for `PostToConnection`.
2. `panakoes-dev-gpu-spawner-task` role gains EventBridge ➜ SQS consumer pattern: SQS `ReceiveMessage` + `DeleteMessage` on a new `panakoes-dev-spawn-queue`.
3. New EventBridge rule: `streaming.session.connecting` ➜ `panakoes-dev-spawn-queue`. Trivial Terraform addition in `infra/dev/events/` or `infra/dev/api-gateway-ws/` (TBD: locate the right module home).

### AMI choice and weight pre-baking

- The earlier `gpu-transcribe` AMI (`ami-0dee04ee5042c94cf`) was deemed unusable for AWS Batch because it lacks an ECS agent. For direct EC2 spawning via `gpu-spawner`, that AMI works fine (it has CUDA + NVIDIA driver + Python + faster-whisper Python wheel optionally pre-installed; verify or rebuild).
- Faster-whisper-large weights (~1.5 GB in CTranslate2 format) should be **pre-baked into the AMI** at `/opt/whisper/models/large-v3-ct2`. This collapses cold-start by ~60 s.
- AMI lineage tracking: `infra/dev/batch/variables.tf` documents the GPU AMI ID pattern (the ECS-Optimized GPU AMI for Batch); the streaming path needs its own variable, e.g., `var.streaming_gpu_ami_id` in `infra/dev/ecs/` or wherever `gpu-spawner` lives.

## Failure modes

| Failure | Detection | Recovery |
|---|---|---|
| Spot interruption during session | Instance metadata `spot/instance-action` reads with 2-min warning | Container `PostToConnection({type:"error",code:"spot-interrupted"})`, writes final partial transcript to DDB + S3, exits. Browser shows "Reconnect" button. Operator-visible CloudWatch metric. |
| Container OOM / Whisper crash | Docker `--restart=on-failure` retries once | If second crash, gpu-spawner reaper terminates instance after 5 min. Browser sees `PostToConnection` failure (the connection times out) and reconnects. |
| WS disconnect mid-session (browser closed, network drop) | `streaming-router._route_disconnect` updates DDB row | Container's lifecycle watcher detects disconnected row within 10 s, writes final transcript, exits. gpu-spawner reaper terminates instance. |
| GPU container can't find SQS queue | Startup config check fails | Container exits non-zero. EC2 user-data marks instance as unhealthy. gpu-spawner reaper terminates. Operator alarmed via CloudWatch. |
| EventBridge rule misconfigured (no auto-spawn) | gpu-spawner doesn't fire | DDB session row stuck at `connecting` ≥ 10 min. Reaper fires anyway; user sees "session never came up." Operator-visible via existing CloudWatch alarm pattern. |
| `PostToConnection` fails (410 Gone = client disconnected) | Container catches 410 | Container treats as $disconnect signal, writes final, exits. Idempotent with the real disconnect path. |
| KMS decrypt fails (transcripts bucket) | S3 write returns 403 | Container surfaces as `error` over WS, writes audit log. Operator pages on the audit-log spike. (This is the same trap that hit admin.panakoes.com today; see `feedback_admin_spa_deploy_kms_trap.md`.) |

## Cost model

- **Active session:** 1 × g4dn.xlarge Spot @ ~$0.158/hr ➜ ~$0.013 per 5-min conversation.
- **Idle:** zero by design (no warm pool in v1).
- **Per-message:** SQS standard (~$0.40 per million); WS frames cost negligibly per session.
- **Daily projected** at 100 sessions × 5 min: ~$1.30/day = ~$40/month. Acceptable in the demo period.

## Migration / rollout

1. **Stage 1 (this design):** writeup, architect-review, Phil gate, adversarial-review, Phil gate.
2. **Stage 2 (parallel agents):**
   - Agent A: `services/transcriber-stream` container + tests + Dockerfile.
   - Agent B: `gpu-spawner` EventBridge subscriber + Terraform EventBridge rule + AMI variable + IAM additions.
   - Agent C: SPA `/realtime` rewrite (replaces `realtime-session.ts` with `streaming-session.ts`).
3. **Stage 3 (E2E):** orchestrator drives a smoke test against the gold-standard fixture via the live API; verify partial cadence + final transcript matches.
4. **Stage 4 (cleanup):** delete `services/admin/src/lib/realtime-session.ts` (the chunked-batch implementation) and its tests. Drop the chunked-batch FOLLOWUPS note since this work realizes it.

## Open questions for architect-reviewer + adversarial-reviewer

1. **Per-session SQS queue vs shared queue with session_id filter:** the shared-queue + filter approach reuses existing routing but means the GPU container's SQS poll grabs messages tagged for OTHER sessions (it must filter and re-queue them via `ChangeMessageVisibility(0)`). Is that an acceptable design vs creating per-session queues at spawn time?
2. **AudioWorklet vs MediaRecorder:** AudioWorklet gives raw PCM at the cost of more browser-side code. Is the latency improvement worth the complexity, or should v1 stick with MediaRecorder + serverside re-decode?
3. **Spot vs On-Demand for first-session demo:** Spot is cheap but interruption mid-demo is bad UX. Is on-demand the right v1 default (~3-4x cost but no interruptions)?
4. **VAD-driven partial cadence vs fixed-window partial cadence:** Silero VAD's speech-boundary detection gives natural sentence breaks but variable latency. A fixed 500 ms partial cadence gives smoother UX. Should the partial emit cadence be tunable per session?
5. **Where does `gpu-spawner` learn the WS API stage's management endpoint?** Hardcoded ENV var, Terraform output, or runtime API GW DescribeApi? The third is cleanest but adds a startup call.
6. **What's the right idle-reaper cadence?** 5 min sweep is cheap; lower (1 min) reduces orphan window; higher (15 min) costs less in DDB scan + CloudWatch metric tags. v1 default 5 min ✓?

## Out of scope (handled elsewhere or deferred)

- The `Transcriber` abstraction (`docs/design/transcriber-abstraction.md`); this design plugs into it as one implementation.
- Diarization, language switching, vocabulary boost: feature requests, post-v1.
- Multi-session-per-GPU optimization: cost optimization, post-v1.
- Pre-warmed Spot pool: latency optimization, post-v1.
- iOS Safari support: browser caveat called out in v1 docs; full Safari path is post-v1.
