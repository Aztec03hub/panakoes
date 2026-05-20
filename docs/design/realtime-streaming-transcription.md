# Real-time streaming transcription (design doc)

> **Status:** Proposed (v2 post-architect-review, pending adversarial review + Phil gate). v1 reviewed by architect-reviewer at 2026-05-20T05:48Z; this revision applies all 4 MUST items + the highest-leverage IMPs (vendoring WhisperLiveKit's inner loop, per-session SQS queue, binary WS frames, fixed faster-whisper API, Spot 2-min drain handler).
>
> **Why now:** the chunked-batch pseudo-realtime path (`/realtime`, shipped 2026-05-20 in PR #449) yields ~50-100 seconds per 8-second chunk because each chunk is a fresh AWS Batch job that pays a cold container start + a 3 GB Whisper-weights download. Phil's verdict (verbatim 2026-05-20): "even 50-second chunk processing time is ABSOLUTELY fucking criminally unacceptable." We are now wiring the data plane that the existing streaming control plane has been waiting for.

## Vendored components (per architect-review IMP-01 + MUST-04)

Rather than reinvent faster-whisper-large incremental streaming + LocalAgreement-2 stabilization from scratch, we vendor selected modules from [QuentinFuxa/WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) (Apache-2.0, 10.3k stars, active 2026-03). The architect-reviewer's Step 0 inventory confirmed this is the closest fit; vendoring its inner loop drops the container's bespoke surface from ~500 LOC to ~150 LOC of wrapper code.

**Vendored under `services/transcriber-stream/vendor/whisperlivekit/`:**

| File | LOC | What it does |
|---|---|---|
| `local_agreement/online_asr.py` | 425 | OnlineASRProcessor (LocalAgreement-2 incremental stabilization; the heart of the inner loop) |
| `local_agreement/backends.py` | 284 | FasterWhisperASR wrapper, normalized return shape across model variants |
| `local_agreement/whisper_online.py` | 201 | Backend factory + Whisper language tokenizer dispatch |
| `silero_vad_iterator.py` | ~100 | Silero VAD wrapper (boundary detection only; faster-whisper's bundled `vad_filter=True` handles in-segment silence) |
| `silero_vad_models/silero_vad_16k_op15.onnx` | binary | Silero VAD model weights (Apache-2.0 in upstream) |
| `warmup.py` | ~80 | First-call latency reducer (loads + decodes a 1 s test clip at startup) |

**Attribution (NOTICE file at `services/transcriber-stream/NOTICE`):** lists the upstream project, its license, the commit SHA we vendored from, and the modifications we made. New file. Required by Apache-2.0 section 4. Trivial.

**Modifications we make to the vendored code:**

1. Remove imports + branches for backends we don't ship (vLLM, MLX, Voxtral, Qwen).
2. Replace direct stdout logging with the project's structured logger.
3. No changes to the LocalAgreement algorithm or the inner faster-whisper call signature.

All modifications declared in `services/transcriber-stream/vendor/README.md` so a future bump from upstream is mechanical.

## NOT vendored (our own code)

- SQS frame consumer
- API Gateway `PostToConnection` emitter
- DDB session-row lifecycle watcher
- S3 final-transcript writer
- Spot interruption drain handler
- Container entrypoint + config + Dockerfile + tests

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

### Audio frame format and transport (revised per architect IMP-03 + IMP-07)

- **Capture:** browser `AudioWorklet` on the microphone stream. Downsample to 16 kHz mono signed 16-bit PCM. (`AudioContext({sampleRate: 16000})` works in modern Chromium + Firefox; Safari needs `AudioBuffer.resampleAsync` polyfill, accepted v1 limitation: Safari behind a "use Chrome/Firefox" notice.)
- **Frame size:** 200 ms ➜ 6400 bytes raw PCM. Why 200 ms: Silero VAD's natural frame size, balances WS overhead (~250 frames/min) with model emit cadence (every ~500 ms once speech detected).
- **Wire format:** **binary** WebSocket frames (not JSON+base64). 8-byte little-endian header (4 bytes seq + 4 bytes ms-timestamp delta from session start) followed by raw 16 kHz mono s16le PCM bytes. Total per frame: 8 + 6400 = 6408 bytes. At 5 Hz that's 32 KB/s upstream, well under the API Gateway WS frame size cap (32 KB single frame, 128 KB total for fragmented frames).
- **Why binary over JSON+base64:** ~33 % bandwidth + CPU savings (the architect's IMP-03). The browser side already produces PCM in a binary AudioWorklet; encoding it to base64 just to ship it is waste.
- **Server-side routing:** `streaming-router._route_audio_frame` currently forwards the body to a **shared** SQS queue tagged with `session_id` MessageAttribute. We change this to forward to a **per-session SQS queue** (URL stored on the DDB session row, written by gpu-spawner at spawn time). Per architect IMP-07, the shared-queue + client-filter pattern is an SQS anti-pattern; per-session queues are cheap (~$0.40 per million standard-queue requests, plus $0 for empty queues) and eliminate the cross-session message scan.

### transcriber-stream container (revised per architect MUST-01)

Location: `services/transcriber-stream/` (new). pyproject layout matches the existing `transcriber-batch` service skeleton.

```
services/transcriber-stream/
├── Dockerfile
├── pyproject.toml
├── README.md
├── NOTICE                                # vendor attribution (Apache-2.0 section 4)
├── src/panakoes_transcriber_stream/
│   ├── __init__.py
│   ├── main.py              # asyncio entrypoint
│   ├── config.py            # env vars: SESSION_ID, CONNECTION_ID, FRAME_QUEUE_URL, WS_ENDPOINT, ...
│   ├── sqs_consumer.py      # async SQS poller (per-session queue; no client-side filter)
│   ├── ws_publisher.py      # ApiGatewayManagementApi.PostToConnection client (+ 410 handling)
│   ├── persistence.py       # S3 upload + DDB UpdateItem for last_transcript and final
│   ├── lifecycle.py         # session-row watcher + Spot 2-min drain handler
│   └── transcribe.py        # thin wrapper around vendor.whisperlivekit.OnlineASRProcessor
└── vendor/
    ├── README.md            # what was vendored + commit SHA + our modifications
    └── whisperlivekit/      # Apache-2.0 modules from QuentinFuxa/WhisperLiveKit
        ├── local_agreement/
        │   ├── online_asr.py
        │   ├── backends.py
        │   └── whisper_online.py
        ├── silero_vad_iterator.py
        └── silero_vad_models/silero_vad_16k_op15.onnx
```

**Runtime contract (env vars):**
- `PANAKOES_SESSION_ID`: the DDB session row's primary key. Required.
- `PANAKOES_CONNECTION_ID`: the API GW WebSocket connection id (= session_id today). Required.
- `FRAME_QUEUE_URL`: **per-session** SQS audio-frame queue URL (written to the DDB session row by gpu-spawner; the container reads it from env at start). Required.
- `WS_ENDPOINT`: API GW management endpoint, e.g. `https://a75u8kj039.execute-api.us-east-1.amazonaws.com/dev`. Required.
- `STREAMING_SESSIONS_TABLE`: DDB table name. Required.
- `TRANSCRIPTS_BUCKET`: S3 bucket for final transcripts. Required.
- `MODEL_SIZE`: faster-whisper model variant. Default `large-v2` (architect MUST-02: large-v3 hallucinates on silence more than v2; LocalAgreement-2 mitigates but v2 is the safer default for live partials). Required at startup.
- `MODEL_CACHE_DIR`: pre-baked weights location. Default `/opt/whisper/models` (AMI-baked). Required.
- `LANGUAGE_HINT`: ISO 639-1 (default `en`). Forwarded to faster-whisper for both stability and latency.
- `MIN_CHUNK_SECONDS`: LocalAgreement minimum buffer before first emit. Default 1.0. Required.
- `MAX_CHUNK_SECONDS`: forced flush cap. Default 30. Required.
- `IDLE_SECONDS_BEFORE_EXIT`: tear down if no frames for N seconds AND session row says disconnected. Default 30. Required.

**Main loop (asyncio, corrected per architect MUST-01):**

The inner transcribe loop is now a thin wrapper around the vendored `OnlineASRProcessor` from WhisperLiveKit's `local_agreement` package. That class implements LocalAgreement-2 stabilization around `faster_whisper.WhisperModel.transcribe(audio_array, vad_filter=True, condition_on_previous_text=False, ...)` (the real API, fixing the MUST-01 bug where v1 of this design called `model.transcribe_stream(...)` which does not exist).

```python
# pseudocode
async def main():
    cfg = load_config_from_env()

    # Vendor: WhisperLiveKit's backend_factory builds a FasterWhisperASR +
    # OnlineASRProcessor pair configured per cfg. Singleton init takes
    # ~35 s for large-v2 fp16 on T4.
    asr, online = backend_factory(
        backend="faster-whisper",
        model_size=cfg.model_size,
        model_cache_dir=cfg.model_cache_dir,
        lan=cfg.language_hint,
        min_chunk_size=cfg.min_chunk_seconds,
    )
    warmup_asr(asr, warmup_file="/opt/whisper/warmup-1s.wav")

    sqs = SQSConsumer(cfg.frame_queue_url)           # per-session queue
    ws = WsPublisher(cfg.ws_endpoint, cfg.connection_id)
    persist = Persistence(cfg.transcripts_bucket, cfg.sessions_table, cfg.session_id)
    lifecycle = LifecycleWatcher(cfg.sessions_table, cfg.session_id)
    spot = SpotDrainHandler()                         # polls instance metadata

    await ws.send({"type": "ready"})

    async for pcm_chunk in sqs.frames():              # yields raw PCM bytes (200 ms each)
        online.insert_audio_chunk(pcm_to_float32(pcm_chunk))
        # LocalAgreement-2 inside online.process_iter() decides when to
        # emit a "confirmed" segment vs continue accumulating. Returns
        # (start, end, text) for each newly confirmed sentence; the
        # uncommitted prefix is available via online.to_flush.
        for start, end, text in online.process_iter():
            await ws.send({"type": "final", "text": text, "start": start, "end": end})
            await persist.update_last_transcript(text, segment_end=end)
        # In-progress (not-yet-confirmed) partial:
        partial_text = online.to_flush(online.transcript_buffer.complete())
        if partial_text:
            await ws.send({"type": "partial", "text": partial_text})

        if spot.is_interrupting():
            await drain_and_exit(asr, online, ws, persist)  # see Spot drain section
            return
        if await lifecycle.should_exit():
            break

    final_text = online.finish()
    await ws.send({"type": "final", "text": final_text, "is_session_end": True})
    await persist.write_final(final_text)
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
- Warmup pass (1 s test clip): 2 s
- **Total: 102-192 s before the first partial.** Phil's verdict 2026-05-20: acceptable for v1, Spot stays per cost discipline. The "warm pool" optimization (architect IMP-05/06) stays in FOLLOWUPS as a v1.5 lever if cold-start UX bites.

### Spot interruption handler (architect MUST-03)

Spot instances get a 2-minute warning before termination via instance-metadata service:

```bash
curl http://169.254.169.254/latest/meta-data/spot/instance-action
# returns 200 with {"action": "terminate", "time": "..."} when interruption is queued
```

`SpotDrainHandler` polls this endpoint every 5 seconds. When the warning fires:

1. **Stop accepting new SQS frames** (close the consumer's recv loop).
2. **Flush any in-buffer audio:** call `online.finish()` to force a final transcription of the remaining buffer.
3. **Emit a structured WS message** to the browser: `{"type": "error", "code": "spot-interrupted", "message": "GPU is being reclaimed; reconnect to continue."}`.
4. **Write the partial-final transcript to S3 + DDB** so the user does not lose what was captured.
5. **Update the session row** to status `interrupted`.
6. **Exit 0** so the host's reaper terminates the instance cleanly.

The browser-side `streaming-session.ts` reacts to `code: "spot-interrupted"` by surfacing a "Session interrupted; reconnect?" UI affordance with a fresh-start button. Reconnect = new session, new `connection_id`, new GPU spawn. The previous partial transcript is available via the legacy `/ingestion/[id]` view of the now-finalized partial session.

Budget for the drain: 2 minutes warning ➜ ~10 s for buffer flush + S3 write + DDB update + WS send leaves ~110 s of headroom. Generous.

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

**gpu-spawner's spawn-session-instance steps (post-update for per-session queue):**

1. `aws sqs create-queue --queue-name panakoes-dev-stream-frames-<session_id>` (per-session queue; standard, message-retention 60 s, visibility-timeout 5 s).
2. `aws dynamodb update-item` on the session row: write `frame_queue_url` attribute.
3. `aws ec2 run-instances` with the streaming-AMI, security group, IAM instance profile, tags (`panakoes:session-id=<id>`), and user-data that:
   - Pulls `transcriber-stream:latest` from ECR
   - Reads `connection_id` and `frame_queue_url` from the DDB session row at boot
   - Runs the container with env vars set per the runtime contract above
   - Configures Docker `--restart=on-failure` with backoff so a Whisper OOM gets one retry before instance tear-down

**`streaming-router._route_audio_frame` change:** instead of forwarding to a single shared queue, the router now reads `frame_queue_url` from the DDB session row and forwards there. The session row gets that URL written by gpu-spawner at spawn time, BEFORE the router gets its first frame for that session (the SPA waits for the `{"type":"ready"}` message before sending audio).

**Idle reaper cleanup:** when the reaper terminates an instance, it ALSO deletes the per-session SQS queue. Queue deletion is async (~60 s) but free. No leaked queues.

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

## Resolved questions (post-architect-review)

| Original v1 question | Resolution |
|---|---|
| Per-session SQS queue vs shared queue with filter | **Per-session queue.** Architect IMP-07 confirmed the shared-queue + client-filter pattern is an SQS anti-pattern; per-session queues are cheap (empty queues are free, ~$0.40 per million standard requests when active). gpu-spawner creates at spawn, reaper deletes at tear-down. |
| AudioWorklet vs MediaRecorder | **AudioWorklet.** Architect confirmed the latency improvement is real (~50-100 ms saved per frame) and the v1 limitation (no Safari, Phil's Pixel uses Chrome, demo audience uses laptop Chrome/Firefox) is acceptable. |
| Spot vs on-demand | **Spot v1 per Phil's call.** Architect's IMP-05/06 (on-demand + warm pool) is captured as a v1.5 lever in FOLLOWUPS for the cold-start UX issue if it bites. The Spot interruption drain handler (MUST-03) is mandatory regardless. |
| VAD cadence | **LocalAgreement-2 (via vendored `OnlineASRProcessor`) decides emit cadence dynamically.** Each `process_iter()` returns 0..N confirmed sentence segments + the running unconfirmed prefix. Browser sees a natural cadence that matches speech, not a fixed timer. |
| WS API stage management endpoint | **Hardcoded as an env var on the GPU instance**, computed by gpu-spawner from the WS API ID Terraform output. Simpler than runtime DescribeApi; the WS API stage rarely changes. |
| Idle reaper cadence | **5-minute sweep.** Cheap (DDB scan over <100 rows + EC2 describe-instances filtered by tag), short enough orphan window, matches the existing gpu-spawner cron in the codebase. |

## Open questions remaining for adversarial-reviewer + Phil gate

1. **Should the vendor lift cover SimulStreaming + AlignAtt (architect RES-02) as a flag-toggled v1.5 backend, or stay strictly on LocalAgreement-2?** Flag-toggled is cheap (a few hundred extra LOC vendored) but adds review surface.
2. **Spot interruption recovery UX:** "Reconnect to continue" loses the partial transcript context. Should v1 instead auto-reconnect transparently and stitch the partial into the new session's running text? Adds SPA complexity.
3. **What happens if the GPU container takes 90 s and the user clicks Stop at 60 s?** The container is still loading the model when the disconnect arrives; idle reaper terminates eventually but the user already abandoned. Spent compute. Acceptable in v1 or worth a "cancel-spawn" path?
4. **Should `streaming-router._route_audio_frame` short-circuit when the session row says `disconnected`?** Today it would still SQS-forward a stray frame from a closing connection. Cheap optimization.
5. **Vendor freshness policy:** the vendored WhisperLiveKit code is pinned to its commit SHA in `vendor/whisperlivekit/`. How often do we bump? Quarterly review? On security advisory only?

## Out of scope (handled elsewhere or deferred)

- The `Transcriber` abstraction (`docs/design/transcriber-abstraction.md`); this design plugs into it as one implementation.
- Diarization, language switching, vocabulary boost: feature requests, post-v1.
- Multi-session-per-GPU optimization: cost optimization, post-v1.
- Pre-warmed Spot pool: latency optimization, post-v1.
- iOS Safari support: browser caveat called out in v1 docs; full Safari path is post-v1.
