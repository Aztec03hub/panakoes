# Real-time streaming transcription (design doc)

> **Status:** Proposed (v4 post-second-adversarial-review, pending Phil gate). v1 reviewed by architect-reviewer at 2026-05-20T05:48Z; v2 applied all 4 architect MUST items. v3 was reviewed by adversarial-reviewer at 2026-05-20T06:08Z (3 new CRIT findings); v4 (this revision) addresses ALL 3 CRIT + ALL 6 HIGH + ALL 6 MED findings from that round 2 review. Headline v4 changes: JSON+base64 upstream (binary-on-the-wire reversed since API GW WS cannot route binary to a named route key; deferred to v1.5), drain-then-claim pool semantics (no PurgeQueue, ever), explicit vendor-code patch list including `condition_on_previous_text=False` + `use_vad()` + `beam_size=1`, one-row-per-pool-queue DDB model with random-walk claim, honest "preserved across a 2-minute gap" framing on 110-min reconnect with parent_session_id, large-v2 propagated consistently to AMI bake, SPA-side ping during cold-start, chunk-envelope metadata (seq + total + expected_chunks), explicit ping-echo protocol.
>
> **Why now:** the chunked-batch pseudo-realtime path (`/realtime`, shipped 2026-05-20 in PR #449) yields ~50-100 seconds per 8-second chunk because each chunk is a fresh AWS Batch job that pays a cold container start + a 3 GB Whisper-weights download. Phil's verdict (verbatim 2026-05-20): "even 50-second chunk processing time is ABSOLUTELY fucking criminally unacceptable." We are now wiring the data plane that the existing streaming control plane has been waiting for.

## Vendored components (per architect-review IMP-01 + MUST-04)

Rather than reinvent faster-whisper-large incremental streaming + LocalAgreement-2 stabilization from scratch, we vendor selected modules from [QuentinFuxa/WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) (Apache-2.0, 10.3k stars, active 2026-03). The architect-reviewer's Step 0 inventory confirmed this is the closest fit; vendoring its inner loop drops the container's bespoke surface from ~500 LOC to ~150 LOC of wrapper code.

**Vendored under `services/transcriber-stream/vendor/whisperlivekit/`:**

The vendored set is the closure of imports rooted at `local_agreement/online_asr.py` and `local_agreement/whisper_online.py`. Upstream import graph (verified against the live repo HEAD at 2026-05-20) requires:

| File (path under `vendor/whisperlivekit/`) | LOC | What it does |
|---|---|---|
| `local_agreement/online_asr.py` | 425 | OnlineASRProcessor (LocalAgreement-2 incremental stabilization; the heart of the inner loop) |
| `local_agreement/backends.py` | 284 | FasterWhisperASR + WhisperASR + OpenaiApiASR wrappers (we keep only FasterWhisperASR + base ASRBase) |
| `local_agreement/whisper_online.py` | 201 | Backend factory + Whisper language tokenizer dispatch |
| `timed_objects.py` | ~80 | `ASRToken`, `Sentence`, `Transcript` dataclasses (transitive dep of `online_asr.py`) |
| `model_paths.py` | ~120 | `resolve_model_path`, `detect_model_format` helpers (transitive dep of `backends.py` + `whisper_online.py`) |
| `backend_support.py` | ~60 | `faster_backend_available`, `mlx_backend_available` (transitive dep of `whisper_online.py`; we trim mlx branches) |
| `warmup.py` (top-level, NOT under `local_agreement/`) | ~80 | First-call latency reducer (loads + decodes a 1 s test clip at startup) |
| `silero_vad_iterator.py` | ~100 | Silero VAD wrapper (boundary detection only) |
| `silero_vad_models/silero_vad_16k_op15.onnx` | binary | Silero VAD model weights (Apache-2.0 in upstream) |

**Attribution (NOTICE file at `services/transcriber-stream/NOTICE`):** lists the upstream project, its license, the commit SHA we vendored from, and the modifications we made. New file. Required by Apache-2.0 section 4. Trivial.

**Modifications we make to the vendored code (binding contract for the implementing agent; the LICENSE + NOTICE files must reflect these):**

1. **Remove imports + branches for backends we don't ship.** Delete vLLM, MLX, Voxtral, Qwen branches from `whisper_online.py:_normalize_backend_choice` and the matching import lines. `backend_support.py` keeps only the `faster_backend_available` check.
2. **Replace direct stdout / logfile logging with the project's structured logger.** `logfile=sys.stderr` arg stays for upstream compat; the call sites switch to `logger.info(...)` against our `panakoes_transcriber_stream` logger.
3. **`backends.py:FasterWhisperASR.transcribe`: flip `condition_on_previous_text=True` to `False` (or read from a constructor arg defaulting to `False`).** Architect IMP-04 + adversarial CRIT-03 require this for streaming partial stability with LocalAgreement-2. Hardcoded patch in v1 (constructor-arg in v1.5 if needed).
4. **`backends.py:FasterWhisperASR.transcribe`: expose `beam_size` as a constructor arg defaulting to `1` (greedy).** Streaming-latency budget (50-150 ms per inference) assumes greedy. Default upstream `beam_size=5` is correct for batch but ~2-3x slower per call.
5. **`whisper_online.py:backend_factory`: call `asr.use_vad()` unconditionally after the `asr = asr_cls(...)` instantiation.** This sets `transcribe_kargs["vad_filter"] = True` so faster-whisper's bundled Silero VAD filter activates on the inference path. Without this, the architect's IMP-04 anti-hallucination claim is non-functional.
6. **No changes to the LocalAgreement algorithm in `online_asr.py`**, only the imports list (drop unused branches from removed backends).

The separate `silero_vad_iterator.py` provides speech-boundary detection (when to trigger `process_iter()`) which is a different role from `vad_filter=True` (which suppresses silence inside a buffer at inference time). Both are needed.

All modifications declared in `services/transcriber-stream/vendor/README.md` (as a diff-style "MODIFIED" markers list) so a future bump from upstream is mechanical.

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

### Audio frame format and transport (revised per adversarial CRIT-02 + HIGH-04)

- **Capture:** browser `AudioWorklet` on the microphone stream. Downsample to 16 kHz mono signed 16-bit PCM. (`AudioContext({sampleRate: 16000})` works in modern Chromium + Firefox; Safari needs `AudioBuffer.resampleAsync` polyfill, accepted v1 limitation: Safari behind a "use Chrome/Firefox" notice.)
- **Frame size:** 200 ms ➜ 6400 bytes raw PCM. Why 200 ms: Silero VAD's natural frame size, balances WS overhead (~5 frames/sec) with model emit cadence (every ~500 ms once speech detected).
- **Wire format (v1): JSON envelope with base64-encoded PCM.** Body shape:
  ```json
  {"action": "audio-frame", "v": 1, "seq": 142, "ts_ms_delta": 12345, "pcm_b64": "<base64 of 6400 bytes raw 16kHz mono s16le>"}
  ```
  Total ~8.7 KB per frame (envelope + base64). At 5 Hz that's ~43.5 KB/s upstream. Under the API Gateway WS frame size cap (32 KB single frame). Includes `v: 1` for forward-compat versioning (LOW-03 fix).
- **Why JSON+base64 and not binary** (reversed from v2/v3 binary proposal per adversarial CRIT-02): API Gateway WebSocket's `route.selection.expression` is global and text-evaluated. Binary frames cannot be routed to a named route key like `audio-frame`; they would fall through to `$default` and be silently dropped. The "all-the-way binary" claim was further weakened by HIGH-04: SQS message bodies are UTF-8 strings, so the streaming-router would have to base64-encode binary PCM into the SQS payload anyway. Net savings of binary would have been browser-CPU only, at ~32 KB/s with negligible browser CPU pressure. JSON+base64 is the correct v1 design. **Binary-on-the-wire is captured as a v1.5 lever in FOLLOWUPS** if a future iteration wants to invest in a separate-API redesign for routing.
- **Server-side routing:** `streaming-router._route_audio_frame` currently forwards the body to a **shared** SQS queue tagged with `session_id` MessageAttribute. We change this to forward to a **per-session SQS queue** (URL stored on the DDB session row, written by gpu-spawner at spawn time). Per architect IMP-07, the shared-queue + client-filter pattern is an SQS anti-pattern; per-session queues are cheap (~$0.40 per million standard-queue requests, plus $0 for empty queues) and eliminate the cross-session message scan. The SQS payload remains a UTF-8 string of the original JSON envelope; the GPU container's SQS consumer base64-decodes `pcm_b64` back to bytes at receive time.

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

**Runtime contract (env vars; MED-04 fix: each is either Required-no-default OR Optional-with-default, never both):**

Required (no default; container fails fast at startup if unset):
- `PANAKOES_SESSION_ID`: the DDB session row's primary key.
- `PANAKOES_CONNECTION_ID`: the API GW WebSocket connection id (= session_id today; HIGH-02 introduces `parent_session_id` for reconnects but `CONNECTION_ID` always names the active WS).
- `FRAME_QUEUE_URL`: per-session SQS audio-frame queue URL (written to the DDB session row by gpu-spawner; the container reads it from env at start).
- `WS_ENDPOINT`: API GW management endpoint, e.g. `https://a75u8kj039.execute-api.us-east-1.amazonaws.com/dev`.
- `STREAMING_SESSIONS_TABLE`: DDB session-row table name.
- `STREAMING_FRAME_POOL_TABLE`: DDB pool-state table name (HIGH-06 fix).
- `TRANSCRIPTS_BUCKET`: S3 bucket for final transcripts.

Optional (defaults documented; container reads but does not require):
- `MODEL_SIZE`: faster-whisper model variant. **Default `large-v2`** (architect MUST-02: large-v3 hallucinates on silence more than v2; LocalAgreement-2 mitigates but v2 is the safer default for live partials).
- `MODEL_CACHE_DIR`: pre-baked weights location. Default `/opt/whisper/models`. The container asserts at startup that `MODEL_CACHE_DIR/large-v2-ct2/` (or whatever `MODEL_SIZE` resolves to) exists; if absent, fails fast with a clear error rather than silently falling back to HuggingFace download (HIGH-03 fix).
- `LANGUAGE_HINT`: ISO 639-1 language code. Default `en`. Forwarded to faster-whisper for both stability and latency.
- `MIN_CHUNK_SECONDS`: LocalAgreement minimum buffer before first emit. Default 1.0.
- `MAX_CHUNK_SECONDS`: forced flush cap. Default 30.
- `IDLE_SECONDS_BEFORE_EXIT`: tear down if no frames for N seconds AND session row says disconnected. Default 30.
- `KEEPALIVE_PING_SECONDS`: GPU-side ping cadence. Default 540 (9 min).

**Main loop (asyncio, corrected per adversarial CRIT-01/02/03):**

The inner transcribe loop wraps the vendored `OnlineASRProcessor` (LocalAgreement-2) from WhisperLiveKit. The real upstream API (verified against `vendor/whisperlivekit/local_agreement/online_asr.py` and `whisper_online.py`) is:

- `backend_factory(backend, lan, model_size, model_cache_dir, model_dir, model_path, lora_path, direct_english_translation, buffer_trimming, buffer_trimming_sec, confidence_validation, warmup_file=None, min_chunk_size=None) -> asr` (single object; not a tuple).
- `OnlineASRProcessor(asr, logfile=sys.stderr)` (constructed separately).
- `online.insert_audio_chunk(audio: np.ndarray, audio_stream_end_time: Optional[float] = None) -> None`.
- `online.process_iter() -> Tuple[List[ASRToken], float]` returns (newly committed tokens, audio-processed-upto seconds).
- `online.finish() -> Tuple[List[ASRToken], float]` returns (uncommitted-remainder tokens, final audio-processed-upto seconds).
- `ASRToken` is a dataclass with `text: str`, `start: float`, `end: float`, `speaker: str | None`, `probability: float | None`.

**Both `process_iter()` and `finish()` call the synchronous GPU `transcribe()` inside.** They MUST be wrapped in `asyncio.run_in_executor` so the asyncio event loop (SQS consumer + Spot drain handler + WS publisher) stays responsive between inferences. The executor is a single-threaded `concurrent.futures.ThreadPoolExecutor(max_workers=1)` so we keep one GPU call in flight at a time.

```python
# pseudocode (corrected against actual upstream API)
import asyncio, sys, os
import numpy as np
from concurrent.futures import ThreadPoolExecutor

from .vendor.whisperlivekit.local_agreement.whisper_online import backend_factory
from .vendor.whisperlivekit.local_agreement.online_asr import OnlineASRProcessor

async def main():
    cfg = load_config_from_env()

    # Step 1: build the ASR backend (synchronous, ~35 s for large-v2 fp16 on T4).
    asr = backend_factory(
        backend="faster-whisper",
        lan=cfg.language_hint,
        model_size=cfg.model_size,
        model_cache_dir=cfg.model_cache_dir,
        model_dir=None,
        model_path=None,
        lora_path=None,
        direct_english_translation=False,
        buffer_trimming="segment",
        buffer_trimming_sec=15.0,
        confidence_validation=False,
        warmup_file="/opt/whisper/warmup-1s.wav",
        min_chunk_size=cfg.min_chunk_seconds,
    )
    # Step 2: construct OnlineASRProcessor separately (NOT returned by factory).
    online = OnlineASRProcessor(asr, logfile=sys.stderr)

    sqs = SQSConsumer(cfg.frame_queue_url)
    ws = WsPublisher(cfg.ws_endpoint, cfg.connection_id)
    persist = Persistence(cfg.transcripts_bucket, cfg.sessions_table, cfg.session_id)
    lifecycle = LifecycleWatcher(cfg.sessions_table, cfg.session_id)
    spot = SpotDrainHandler()

    # Single-worker executor: keeps GPU call off the event loop without
    # multi-threading the GPU itself.
    gpu_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper-gpu")
    loop = asyncio.get_running_loop()

    # Background tasks: lifecycle + spot watchers run independent of the
    # main consume loop so a slow inference does not block them.
    lifecycle_task = asyncio.create_task(lifecycle.watch())     # sets event on disconnect
    spot_task = asyncio.create_task(spot.watch())               # sets event on warning
    keepalive_task = asyncio.create_task(ws.keepalive_pings(interval_seconds=540))  # 9-min ping (CRIT-05)

    await ws.send({"type": "ready"})

    try:
        async for pcm_chunk in sqs.frames():
            # PCM frame: 200 ms @ 16 kHz s16le ➜ 3200 int16 samples.
            samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32) / 32768.0
            # insert_audio_chunk is cheap (numpy append); not run on executor.
            online.insert_audio_chunk(samples)

            # GPU inference happens here. Off the event loop.
            committed_tokens, processed_upto = await loop.run_in_executor(
                gpu_pool, online.process_iter
            )
            for token in committed_tokens:
                await ws.send({
                    "type": "final",
                    "text": token.text,
                    "start": token.start,
                    "end": token.end,
                    "probability": token.probability,
                    "audio_upto": processed_upto,  # for client-side audio-time sync (LOW-01)
                })
            await persist.update_last_transcript_tokens(committed_tokens, audio_upto=processed_upto)

            if spot.event.is_set():
                await drain_and_exit(online, ws, persist, loop, gpu_pool, reason="spot-interrupted")
                return
            if lifecycle.event.is_set():
                break

        # Normal end-of-session path: drain remaining buffer.
        remaining_tokens, final_processed_upto = await loop.run_in_executor(gpu_pool, online.finish)
        await persist.write_final_tokens(remaining_tokens, audio_upto=final_processed_upto, committed=True)
        # HIGH-11 + MED-05: final transcript can be large. Emit in chunks
        # of <=24 KB JSON (margin under the 32 KB WS frame cap). Each
        # chunk carries seq + total so the SPA can detect mid-emit loss
        # and request a re-emit via transcript-request (which reads from
        # DDB).
        chunks = chunk_tokens_for_ws(remaining_tokens, max_bytes=24_000)
        total = len(chunks)
        for seq, chunk in enumerate(chunks):
            await ws.send({"type": "final-chunk", "seq": seq, "total": total, "tokens": chunk})
        await ws.send({"type": "ended", "expected_chunks": total, "audio_upto": final_processed_upto})
    finally:
        for task in (lifecycle_task, spot_task, keepalive_task):
            task.cancel()
        gpu_pool.shutdown(wait=False)
```

**On `PostToConnection` 410 mid-emit (HIGH-05):** `WsPublisher.send()` catches the 410 (client disconnected), sets `lifecycle.event` so the main loop exits the consume `async for`, then runs the same drain-and-finalize path as a normal `$disconnect`. The container writes a final transcript to S3 + DDB BEFORE exiting; no work is lost. The 410 catch path is identical to the lifecycle-watcher disconnect path, with status set to `"disconnected_by_410"` so the operator can distinguish the two.

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

### WebSocket connection lifecycle (adversarial CRIT-05)

**API Gateway WebSocket has TWO hard limits the design must respect:**

1. **10-minute idle timeout.** Any 10-minute gap between client-server messages causes API GW to close the connection. Mitigation: explicit ping/echo protocol (MED-06 fix). GPU side: `WsPublisher.keepalive_pings(interval_seconds=540)` sends `{"type":"ping","seq":N}` every 9 minutes (downstream). SPA side: on receipt of `ping`, replies with `{"action":"ping-echo","seq":N}` (upstream); also sends its OWN `{"action":"ping"}` every 60 seconds during the `spawning-gpu` state per HIGH-05 since the GPU isn't sending yet. The streaming-router's `$default` route accepts `ping` and `ping-echo` (no action; returns 200). Net result: both sides' idle timers refresh every ~9 minutes during steady-state, every 60 s during cold-start.
2. **2-hour maximum connection duration.** Hard cap; cannot be raised. Any single WS connection terminates at the 2-hour mark.

**Session-length policy (v1, revised honest framing per adversarial HIGH-02):**

- Sessions longer than ~110 minutes auto-finalize at the 110-minute mark: container writes the final transcript, sends `{"type":"session-ending-soon", "reason":"api-gw-2h-limit", "warn_at_ms": 5000}` to the browser 5 s before close, then closes the WS cleanly. The browser-side `streaming-session.ts` reacts by opening a NEW WS (fresh `connection_id`, new DDB session row, new GPU spawn), passing the prior session's `committed_transcript_tail` (last 200 characters) as a connect-time query-string param. The new container reads `parent_session_id` + `prompt_seed` from the new DDB session row at boot and primes `OnlineASRProcessor.committed` with the prompt-seed text so prompt context survives.
- **Honest UX framing:** the cutover incurs a fresh cold start (~100-190 s on Spot) during which the SPA shows "Switching to a fresh GPU; this takes about 2 minutes. Your transcript so far is preserved." The previously-displayed transcript is not lost; the gap is in NEW frames being transcribed. This is NOT "seamless" - it is "preserved across a 2-minute gap." The runbook + SPA help copy say so explicitly.
- **DDB session row gains `parent_session_id`** (nullable string). A reconnected session writes its predecessor's `session_id` here; downstream display (`/ingestion/[id]` view, S3 transcript path) joins parent + child rows so the user sees a single transcript across the cap boundary. Older rows have `parent_session_id` absent (no migration needed).
- The 2-hour cap is documented in the SPA help copy AND `docs/runbooks/streaming-session-end-to-end.md`.
- v1.5 lever (FOLLOWUPS): the warm-pool pattern (architect IMP-06) makes the cutover ~30-45 s instead of 100-190 s, which would justify a "soft cutover" framing. Not v1.

**On any unexpected `socket.close`:**
- Container: 410 from PostToConnection ➜ flush-and-exit path (HIGH-05).
- SPA: shows "Reconnect to continue" affordance with the partial transcript preserved client-side. Reconnect = new session; no automatic stitch unless inside the 110-minute window.

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

Budget for the drain (MED-03 fix, honest numbers): 2 minutes warning. Realistic worst case is `online.finish()` on a 30 s buffer (up to ~3 s on T4 fp16 for large-v2), plus chunked final-transcript WS emission (1-3 s for a long session), plus S3 write (1-2 s), plus DDB update (50 ms), plus final WS notify (50 ms). Realistic drain total: 5-10 s typical, 15-20 s worst case. Even worst-case leaves ~100 s of headroom under the 2-min warning. Comfortable, not "generous" - the prior "10 s flat" framing was optimistic.

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

**Frame-queue strategy (adversarial CRIT-01 + HIGH-06 fix): pre-allocated pool with drain-then-claim, one-row-per-queue DDB model.**

`CreateQueue` and `DeleteQueue` are heavyweight control-plane API calls (~30 TPS account ceiling) with a 60-second tombstone on name reuse, and `PurgeQueue` takes up to 60 s with a documented window during which messages sent post-purge may be silently deleted. None of those latencies are acceptable for per-session use. The v4 design uses a fixed pool of pre-allocated queues, claimed via DDB with **drain-then-claim** semantics (no PurgeQueue, ever).

**Pool sizing:** 32 standard SQS queues named `panakoes-dev-stream-frames-pool-{0..31}`, terraform-managed in `infra/dev/streaming-frame-queues/`, created once at infra apply. Sizing rationale (LOW-02 fix): peak target concurrency per the cost model is ~10 sessions; 32-queue pool provides 3x headroom for surge.

**DDB pool data model (HIGH-06 fix): ONE row per queue.** Table `panakoes-dev-stream-frame-pool`, primary key `pool_queue_id` (int 0..31). Attributes: `queue_url` (string), `claimed_by` (string, optional), `claimed_at` (ISO timestamp, optional). Initial state: all 32 rows present with `claimed_by` absent.

**Claim path (random-walk):**

```python
# pseudocode in gpu-spawner
def claim_pool_queue(session_id: str) -> str | None:
    """Returns the queue URL or None if pool is exhausted."""
    candidates = random.sample(range(32), 32)  # try all 32 in random order
    for pool_id in candidates:
        try:
            ddb.update_item(
                Key={"pool_queue_id": pool_id},
                UpdateExpression="SET claimed_by = :sid, claimed_at = :now",
                ConditionExpression="attribute_not_exists(claimed_by)",
                ExpressionAttributeValues={":sid": session_id, ":now": now_iso()},
            )
            queue_url = ddb.get_item(Key={"pool_queue_id": pool_id})["Item"]["queue_url"]
            # Drain-then-claim: discard any stale frames from prior session.
            drain_queue(queue_url, max_seconds=1.0)
            return queue_url
        except ConditionalCheckFailedException:
            continue  # racer beat us to this slot; try next
    return None  # pool exhausted
```

Median claim cost under low contention: 1 DDB UpdateItem + 1 DDB GetItem + 1 brief drain loop = ~30-60 ms. Under contention: still bounded by 32 attempts × ~10 ms per failed conditional update.

**Drain-then-claim implementation:**

```python
def drain_queue(queue_url: str, max_seconds: float) -> int:
    """Pulls and discards stale messages until empty or max_seconds elapsed."""
    deadline = time.monotonic() + max_seconds
    discarded = 0
    while time.monotonic() < deadline:
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0)
        msgs = resp.get("Messages", [])
        if not msgs:
            return discarded  # queue is empty
        entries = [{"Id": m["MessageId"], "ReceiptHandle": m["ReceiptHandle"]} for m in msgs]
        sqs.delete_message_batch(QueueUrl=queue_url, Entries=entries)
        discarded += len(msgs)
    return discarded
```

Typical wall-clock cost: 50-500 ms. If a prior session left a backlog, the cap of 1.0 s ensures we don't block claim indefinitely.

**Release path:** at session end (lifecycle reaper, normal disconnect, or Spot drain), gpu-spawner clears `claimed_by` from the DDB row. **No PurgeQueue.** The next claimant's drain-then-claim handles any residue. Release is a single DDB UpdateItem with `ConditionExpression="claimed_by = :sid"` so a stale release request from a previous claim cannot accidentally release a queue claimed by a different session.

**If all 32 are claimed**, gpu-spawner returns HTTP 503 (or surfaces `{"type":"error","code":"capacity-exhausted","retry_after_seconds":60}` over the established WS). 32-cap can be tuned upward by changing Terraform.

**gpu-spawner's spawn-session-instance steps (revised):**

1. Claim a pool queue (DDB conditional update). If pool exhausted, return 503.
2. Write `frame_queue_url` to the session row (DDB UpdateItem).
3. Call `aws ec2 run-instances` with the streaming-AMI, security group, IAM instance profile, tags (`panakoes:session-id=<id>`, `panakoes:pool-queue=<n>`), and user-data that pulls `transcriber-stream:latest` from ECR, reads `connection_id` and `frame_queue_url` from the DDB session row at boot, runs the container, sets `--restart=on-failure` with 1-retry cap.

**`gpu-spawner` RunInstances failure paths (adversarial HIGH-03):**

| Failure | Detection | Response |
|---|---|---|
| Spot capacity exhausted (`InsufficientInstanceCapacity`, `MaxSpotInstanceCountExceeded`) | botocore exception class | Update session row `status=spawn-failed`, `error_code=spot-no-capacity`. PostToConnection `{"type":"error", "code":"capacity-exhausted", "retry_after_seconds":60}`. Release pool queue. Increment CloudWatch metric `panakoes.streaming.spawn.spot-no-capacity`. |
| AMI missing or AMI permission denied | `InvalidAMIID` | Same template, `code:"ami-missing"`. Operator-grade error; CloudWatch alarm fires. |
| Quota cap (`VcpuLimitExceeded`) | `RequestLimitExceeded` w/ specific code | Same template, `code:"quota-exceeded"`. CloudWatch alarm fires. |
| IAM instance profile not propagated | `InvalidIamInstanceProfile.NotFound` | Same template, `code:"iam-not-ready"`. **Retry once after 5 s** before failing. (IAM profile creation has eventual-consistency for ~10 s after Terraform apply.) |
| Any other unexpected | catch-all | `code:"unknown-spawn-failure"`; original exception class + message logged. |

**SPA's ready-message race (adversarial HIGH-04):** the SPA MUST wait for the GPU container's `{"type":"ready"}` message before sending the first `audio-frame`. If the SPA pushes a frame before the row has `frame_queue_url`, `streaming-router._route_audio_frame` errors out (no queue URL to forward to). To enforce this strictly:

1. `streaming-router` checks for `frame_queue_url` on the session row before SQS forwarding; if absent, logs `WARN` and returns 200 (drops the frame silently rather than crashing). Once the row has the URL, frames flow.
2. SPA's `streaming-session.ts` keeps a `state = "connecting" | "spawning-gpu" | "ready" | ...` machine. Frames are queued client-side until `state === "ready"`. Once `ready` arrives, queued frames flush in order. The `spawning-gpu` UI shows "Bringing up the GPU; this takes about 90 seconds on cold start."

**Pool-queue cleanup:** when the lifecycle reaper terminates an instance, it clears `claimed_by` from the DDB pool row (single conditional UpdateItem, fast). **PurgeQueue is never called**; any residual frames from the freshly-ended session are discarded by the next claimant's drain-then-claim. No leaked queue claims, no 60-second tombstone, no message-loss race.

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
- Each frame ➜ `socket.send(JSON.stringify({action: "audio-frame", v: 1, seq, ts_ms_delta, pcm_b64}))` where `pcm_b64 = btoa(String.fromCharCode(...pcmBytes))` for the 6400-byte PCM payload. Downstream messages from the GPU are also JSON-text frames; `MessageEvent.data` is always a string.
- API Gateway WebSocket route-selection-expression stays at the existing `$request.body.action` (no Terraform change needed). The browser's JSON envelope satisfies the expression at the `action` field and lands on the appropriate route.
- **During the `spawning-gpu` state (HIGH-05 fix):** the SPA sends `{"action": "ping"}` every 60 seconds. The streaming-router's `$default` route accepts it, returns 200, no work. This keeps API Gateway's 10-minute idle timer fresh through any extended cold-start path (Spot retry, IAM-not-ready retry, first-time AMI fetch). Costs $0.
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
- Faster-whisper-large-**v2** weights (~1.5 GB in CTranslate2 format) should be **pre-baked into the AMI** at `/opt/whisper/models/large-v2-ct2/`. **The path MUST match the `MODEL_SIZE` env-var default** (`large-v2`), otherwise the container's startup assertion fails fast with "AMI is missing expected weights" instead of silently falling back to a slow HuggingFace download (HIGH-03 fix). If a future revision pins large-v3, both the AMI bake directory AND the env-var default must be updated together (single Terraform variable governs both).
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
