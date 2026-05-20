# Real-time streaming transcription (design doc)

> **Status:** Approved for Stage 2 dispatch (v7 post-fifth-adversarial-review + Phil gate). Review trail: v1 → architect (4 MUST) → v2 → adversarial r1 (5 CRIT) → v3 → adversarial r2 (3 CRIT) → v4 → adversarial r3 (2 CRIT) → v5 → adversarial r4 (2 BLOCK + 3 DEGRADE + 4 NIT) → v6 → adversarial r5 (**0 BLOCK** + 1 DEGRADE + 7 NIT total). Phil's rinse-repeat gate ("zero CRITs reported") cleared at round 5. v7 (this revision) folds the round-5 DEGRADE + all 7 NITs (3 new + 4 carry-over) into the doc so the Stage 2 implementing agents have a fully-precise contract: pre-factory AMI-asset assertion (NIT-02 + NIT-03), `try/finally` around factory phase to prevent task-leak (NIT-01), MIN_CHUNK_SECONDS documented as optional-gate (DEGRADE-01), FIFO label correction + disconnect cache eviction (round-4 NIT carry-over), DDB TTL on stuck-`connecting` rows (round-4 NIT carry-over), split `frame.capture_jitter_ms` vs `frame.gpu_processing_ms` metrics (round-4 NIT carry-over).

**Trend across rounds: 5 → 3 → 2 → 2 → 0 BLOCK/CRITs. Strict monotonic decrease. Ship.**
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
6. **`backends.py:FasterWhisperASR.__init__`: route the AMI-baked directory through the `model_size_or_path` argument as a full path, NOT through `cache_dir`** (adversarial round-4 BLOCK-02; v5's `local_files_only=True` fix was insufficient). Verified against faster-whisper source: `WhisperModel(model_size_or_path, ...)` accepts either a model name (like `"large-v2"`, which triggers HF download into the canonical layout `<cache_dir>/models--Systran--faster-whisper-large-v2/snapshots/<hash>/`) OR a full directory path (like `/opt/whisper/models/large-v2-ct2/`, which short-circuits the HF lookup entirely). The AMI bake stores weights under the flat directory, NOT the HF canonical layout, so `local_files_only=True` alone would not find them. The patch: when `MODEL_CACHE_DIR/${MODEL_SIZE}-ct2/` exists, the wrapper constructs `WhisperModel(model_size_or_path="/opt/whisper/models/large-v2-ct2", local_files_only=True)`. Both flags together make the container fully HF-independent at runtime. The vendored backend's constructor signature must be patched to accept and forward `model_size_or_path` as the path-or-name string. Combined with the HIGH-03 startup assertion (above) on the directory's presence, container start is fully offline.
7. **No changes to the LocalAgreement algorithm in `online_asr.py`**, only the imports list (drop unused branches from removed backends).

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
  Total ~8.7 KB per frame (envelope + base64). At 5 Hz that's ~43.5 KB/s upstream. Under the API Gateway WS frame size cap (32 KB single frame). Includes `v: 1` for forward-compat versioning.

  **`ts_ms_delta` semantics (adversarial round-3 HIGH-05 fix):** milliseconds since the SPA's `streaming-session.start()` invocation (NOT epoch time, NOT wall-clock). Monotonic; sourced from `performance.now()`, which the WebPerformance spec guarantees monotonic. If the SPA detects a backward jump (clock change, dev-tools time mock), it resets the counter to the next monotonic value and increments `seq` normally. The GPU consumer uses `ts_ms_delta` ONLY for jitter / cadence metrics (CloudWatch dimension `panakoes.streaming.frame.jitter_ms`); audio time is computed from the GPU's own frame counter, not from `ts_ms_delta`.
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
- `MIN_CHUNK_SECONDS`: documented for forward-compat with upstream's `backend_factory(min_chunk_size=...)` parameter. Default 1.0. **Currently load-bearing via wrapper, NOT upstream.** Upstream declares this kwarg but does not consume it; emit cadence is governed by LocalAgreement-2 + `OnlineASRProcessor.chunk_completed_segment` (which fires on the `buffer_trimming_sec` boundary). Our `SeededOnlineASRProcessor.process_iter()` MAY short-circuit `process_iter()` when buffer duration is under `MIN_CHUNK_SECONDS` (skip the GPU call). If we keep this as a no-op for v1 (relying on the upstream gating), the env var is documentation-only; if we add the gate, it's load-bearing. v1: documentation-only; the implementing agent decides whether to add the short-circuit gate based on observed cold-start partial behavior.
- `MAX_CHUNK_SECONDS`: forced flush cap. Default 30.
- `IDLE_SECONDS_BEFORE_EXIT`: tear down if no frames for N seconds AND session row says disconnected. Default 30. **Fallback only** (adversarial round-3 MED-06); the primary exit path is the lifecycle-event watcher triggering the consume-loop's `break`. The 30 s timer guards against the busy-loop case where `lifecycle.event` was set but the consume loop processed a few more in-flight frames before checking.
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
    loop = asyncio.get_running_loop()

    # WsPublisher must exist before any error path can ws.send; it does
    # NOT need the factory to have completed (its underlying boto3 client
    # is lazy-initialized).
    ws = WsPublisher(cfg.ws_endpoint, cfg.connection_id)

    # Adversarial round-5 NIT-02: assert pre-baked AMI weights exist BEFORE
    # constructing the (slow) backend factory. Failure mode: AMI bake silently
    # missed the model directory, so we'd fall back to HF download (8-12 min).
    # Hard-fail here saves the long wait + surfaces the bake failure.
    asr_dir = f"{cfg.model_cache_dir}/{cfg.model_size}-ct2"
    warmup_clip = "/opt/whisper/warmup-1s.wav"  # adversarial round-5 NIT-03: AMI bake spec
    for path in (asr_dir, warmup_clip):
        if not os.path.exists(path):
            await ws.send({"type": "error", "code": "ami-asset-missing", "path": path})
            raise RuntimeError(f"AMI missing expected asset at {path}")

    # Adversarial round-4 DEG-02 fix: backend_factory blocks for ~35 s on
    # model load; lifecycle + Spot watchers must be created BEFORE the
    # factory call so a $disconnect or Spot-interruption during cold start
    # is observable (the event flags get set even though the consume loop
    # has not started). They idle-wait on their respective polling streams.
    lifecycle = LifecycleWatcher(cfg.sessions_table, cfg.session_id)
    spot = SpotDrainHandler()
    lifecycle_task = asyncio.create_task(lifecycle.watch())
    spot_task = asyncio.create_task(spot.watch())

    # Step 1: build the ASR backend (synchronous, ~35 s for large-v2 fp16 on T4).
    # Wrapped in run_in_executor so it doesn't starve the lifecycle/spot watchers
    # during the model-load window. The try/finally guards against task leaks if
    # the factory raises before we reach the consume loop (adversarial round-5 NIT-01).
    factory_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="factory")
    try:
        asr = await loop.run_in_executor(
            factory_pool,
            lambda: backend_factory(
                backend="faster-whisper",
                lan=cfg.language_hint,
                model_size=cfg.model_size,
                model_cache_dir=cfg.model_cache_dir,
                model_dir=asr_dir,                  # adversarial round-4 BLOCK-02:
                model_path=None,                    # full path to AMI-baked weights.
                lora_path=None,
                direct_english_translation=False,
                buffer_trimming="segment",
                buffer_trimming_sec=15.0,
                confidence_validation=False,
                warmup_file=warmup_clip,
                min_chunk_size=cfg.min_chunk_seconds,
            ),
        )
    except Exception:
        # Cancel the pre-spawned lifecycle/spot tasks so they don't leak.
        for task in (lifecycle_task, spot_task):
            task.cancel()
        factory_pool.shutdown(wait=False)
        raise
    factory_pool.shutdown(wait=False)

    # Step 2: construct OnlineASRProcessor variant separately. The
    # SeededOnlineASRProcessor (see asr_proxy.py) injects prompt_seed_text
    # into prompt() without mutating committed[] (DEG-01 fix).
    prompt_seed = read_prompt_seed_from_ddb(cfg.session_id)
    online = SeededOnlineASRProcessor(asr, prompt_seed_text=prompt_seed, logfile=sys.stderr)

    # If $disconnect or Spot warning fired during the cold start, bail
    # before opening the consume loop.
    if lifecycle.event.is_set() or spot.event.is_set():
        await ws.send({"type": "error", "code": "session-cancelled-during-spawn"})
        return

    sqs = SQSConsumer(cfg.frame_queue_url)
    persist = Persistence(cfg.transcripts_bucket, cfg.sessions_table, cfg.session_id)

    # Single-worker executor for inference: keeps GPU call off the event
    # loop without multi-threading the GPU itself.
    gpu_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper-gpu")

    # 9-min ping (CRIT-05). lifecycle_task + spot_task were created
    # earlier (before the factory cold-start) per DEG-02; only the
    # keepalive starts here since it needs an active WS.
    keepalive_task = asyncio.create_task(ws.keepalive_pings(interval_seconds=540))

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

**Re-emit on chunk loss (adversarial round-3 MED-01):** if the SPA detects a missing `seq` in the final-chunk emission (it knows `expected_chunks = total`), it issues a `transcript-request` over the WS. The router's `_route_transcript_request` returns the whole transcript from the DDB row's `last_transcript_text` field (single string, NOT per-chunk-sliceable). The SPA replaces its local buffer with the returned full transcript. Bandwidth waste on recovery is acceptable for v1: a 100 KB transcript that loses 1 of 5 chunks requires a 100 KB re-fetch, but the loss event is rare. v1.5 lever: extend `transcript-request` with `?from_seq=N` for delta recovery.

**Latency budget (warm):**
- WebSocket round-trip browser ➜ API GW: 30-50 ms
- streaming-router Lambda: 10-30 ms steady-state; 15-45 ms on the first frame per warm-pool member per session due to the queue_url DDB read (per CRIT-02 cache fix); cold-start adds ~500 ms init time for boto3 + DDB resource. **Provisioned-concurrency raised from 1 to 5** (adversarial round-3 MED-03) so concurrent session-start does not stack cold-starts on top of the GPU-spawn UX. Cost: ~$10/month for 5 warm Lambdas vs. ~$2/month for 1.
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

1. **10-minute idle timeout.** Any 10-minute gap between client-server messages causes API GW to close the connection. Mitigation: explicit ping/echo protocol. GPU side: `WsPublisher.keepalive_pings(interval_seconds=540)` sends `{"type":"ping","seq":N}` every 9 minutes (downstream). SPA side: on receipt of `ping`, replies with `{"action":"ping-echo","seq":N}` (upstream); also sends its OWN `{"action":"ping"}` every 60 seconds during the `spawning-gpu` state per HIGH-05 since the GPU isn't sending yet.

   **Router + Terraform change (adversarial round-4 BLOCK-01 fix; round-3 HIGH-01 + Terraform registration):** for API Gateway WebSocket to deliver a route to the Lambda's `requestContext.routeKey`, the route MUST be registered as an `aws_apigatewayv2_route` resource. Today `infra/dev/api-gateway-ws/main.tf` line 32-35 defines `local.app_routes = toset(["audio-frame", "transcript-request"])` and creates one `aws_apigatewayv2_route.app` per entry (line 622-...). Without registering `ping` and `ping-echo`, the API GW route-selection-expression resolves them to `$default`, and the router's new route arm is dead code.

   Two-part fix:

   (a) **Terraform (`infra/dev/api-gateway-ws/main.tf` line 32-35):**

   ```hcl
   app_routes = toset([
     "audio-frame",
     "transcript-request",
     "ping",
     "ping-echo",
   ])
   ```

   (b) **`streaming-router.Router.handle()` adds explicit arms BEFORE the `$default` catch-all,** returning 200 without logging:

   ```python
   if route in ("ping", "ping-echo"):
       return _ok({"route": route, "handled": "keepalive"})
   # $default + any other unknown action: log + accept (existing behavior)
   ```

   Net result: both sides' idle timers refresh every ~9 minutes during steady-state, every 60 s during cold-start, with zero spurious WARN entries. Terraform plan-apply lands the route registration before the SPA + GPU containers ship.
2. **2-hour maximum connection duration.** Hard cap; cannot be raised. Any single WS connection terminates at the 2-hour mark.

**Session-length policy (v1, revised honest framing per adversarial HIGH-02):**

- Sessions longer than ~110 minutes auto-finalize at the 110-minute mark: container writes the final transcript, sends `{"type":"session-ending-soon", "reason":"api-gw-2h-limit", "warn_at_ms": 5000}` to the browser 5 s before close, then closes the WS cleanly. The browser-side `streaming-session.ts` reacts by opening a NEW WS (fresh `connection_id`, new DDB session row, new GPU spawn), passing the prior session's `committed_transcript_tail` (last 200 characters) as a connect-time query-string param. The new container reads `parent_session_id` + `prompt_seed` from the new DDB session row at boot and primes `OnlineASRProcessor.committed` with the prompt-seed text so prompt context survives.
- **Honest UX framing:** the cutover incurs a fresh cold start (~100-190 s on Spot) during which the SPA shows "Switching to a fresh GPU; this takes about 2 minutes. Your transcript so far is preserved." The previously-displayed transcript is not lost; the gap is in NEW frames being transcribed. This is NOT "seamless" - it is "preserved across a 2-minute gap." The runbook + SPA help copy say so explicitly.
- **DDB session row gains `parent_session_id`** (nullable string) AND `prompt_seed_text` (nullable string, ≤ 200 chars). A reconnected session writes its predecessor's `session_id` here.

- **Router change (adversarial CRIT-01 fix; load-bearing):** `streaming-router._route_connect` must read `event.get("queryStringParameters", {}) or {}` and, when present, persist `parent_session_id` + `prompt_seed_text` into the new DDB session row alongside the fixed-shape columns. New file diff for `services/streaming-router/src/panakoes_streaming_router/router.py`:

  ```python
  # Inside _route_connect, after auth = AuthorizerContext.from_event(event):
  qs = event.get("queryStringParameters") or {}
  parent_session_id = qs.get("parent_session_id") or None
  prompt_seed_text = qs.get("prompt_seed_text") or None
  # Then include them in the Item dict written by self._sessions.put_item(...).
  ```

- **Container change (adversarial round-4 DEG-01 fix; subclass approach replaces the v5 token-mutation):** the v5 design synthesized fake tokens with `start=-1.0, end=-0.5` and extended `online.committed`. Verified against upstream: that approach has two fragile corner cases, (i) `process_iter()`'s freeze-prevention reset (`self.init(offset=...)`) wipes `self.committed = []`, and (ii) `chunk_completed_segment()` computes `last_committed_time = -0.5` from the synthetic-only committed list and calls `chunk_at(-0.5)` which truncates `audio_buffer` to its last 0.5 sec and sets `buffer_time_offset = -0.5`. Both can fire on the cold-start short-pause path. Cleaner fix: subclass `OnlineASRProcessor` and override `prompt()` to inject the seed text into the initial-prompt without mutating `committed`.

  ```python
  # In services/transcriber-stream/src/panakoes_transcriber_stream/asr_proxy.py (new):
  from .vendor.whisperlivekit.local_agreement.online_asr import OnlineASRProcessor

  class SeededOnlineASRProcessor(OnlineASRProcessor):
      """OnlineASRProcessor with a one-shot prompt seed.

      The seed string is prepended to the prompt returned by upstream's
      prompt() helper. It does NOT enter committed[]/audio_buffer/anything
      else, so it survives reset paths and segment-trimming.
      """
      def __init__(self, asr, prompt_seed_text: str | None = None, **kwargs):
          super().__init__(asr, **kwargs)
          self._prompt_seed = prompt_seed_text or ""

      def prompt(self):
          base_prompt, context = super().prompt()
          if self._prompt_seed and not self.committed:
              # Only seed when no real committed tokens yet. Once the new
              # session has its own committed history, drop the seed.
              return (self._prompt_seed + " " + base_prompt).strip(), context
          return base_prompt, context

  # In main():
  prompt_seed = read_prompt_seed_from_ddb(cfg.session_id)  # None on fresh start
  online = SeededOnlineASRProcessor(asr, prompt_seed_text=prompt_seed, logfile=sys.stderr)
  ```

  This delivers the same "prompt bias only, never in output" semantics while surviving every reset path the upstream class can take.

- **Parent-child resolution for downstream display (MED-02):** on `/ingestion/[id]` load, if the DDB row has `parent_session_id` set, recursively walk the parent chain (1-3 hops typical) and concatenate transcripts in chronological order based on `connected_at`. Cache the resolved root_session_id back into the leaf row after first walk to avoid re-walking on every view. New `query-api` endpoint: `GET /v1/streaming-sessions/<id>/full` returns the concatenated transcript across the chain.

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

**Claim path (Query-first then conditional-claim, adversarial round-3 HIGH-04 fix):**

```python
# pseudocode in gpu-spawner
def claim_pool_queue(session_id: str) -> str | None:
    """Returns the queue URL or None if pool is exhausted."""
    # Pre-filter: Scan with FilterExpression to find unclaimed slots. DDB Scan
    # is eventually-consistent; the conditional UpdateItem below is the
    # authoritative check. Net cost: ~10 ms for the Scan (32-row table is
    # tiny), then 1 conditional update.
    resp = ddb.scan(
        TableName="panakoes-dev-stream-frame-pool",
        FilterExpression="attribute_not_exists(claimed_by)",
        ProjectionExpression="pool_queue_id",
    )
    candidates = [item["pool_queue_id"] for item in resp.get("Items", [])]
    if not candidates:
        return None  # pool exhausted
    # Randomize candidate order so concurrent claimants don't pile on
    # the same slot.
    random.shuffle(candidates)
    for pool_id in candidates:
        try:
            ddb.update_item(
                Key={"pool_queue_id": pool_id},
                UpdateExpression="SET claimed_by = :sid, claimed_at = :now",
                ConditionExpression="attribute_not_exists(claimed_by)",
                ExpressionAttributeValues={":sid": session_id, ":now": now_iso()},
            )
            queue_url = ddb.get_item(Key={"pool_queue_id": pool_id})["Item"]["queue_url"]
            drain_queue(queue_url, max_seconds=3.0)  # see HIGH-03 fix below
            return queue_url
        except ConditionalCheckFailedException:
            continue  # racer beat us between Scan and Update; try the next candidate
    return None  # all candidates were claimed during the race window
```

Median claim cost: 1 DDB Scan (10 ms) + 1 DDB UpdateItem (10 ms) + 1 DDB GetItem (5 ms) + drain (50-500 ms) = 75-525 ms typical. Under high contention (all 32 racing for the same slots): still bounded by `len(candidates) × 10 ms` for the conditional loop, which is far smaller than the v4 "32 × 10 ms" worst case since `candidates` is pre-filtered to only the actually-unclaimed slots.

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

Typical wall-clock cost: 50-500 ms. **Drain cap is 3.0 seconds** (adversarial round-3 HIGH-03), bumped from the v4 1.0 s value: at the observed ~100 messages/sec drain throughput, 3 seconds reliably clears even a 250-message backlog (50 s of audio at 5 fps; realistic worst-case prior-session crash residue).

**Belt-and-suspenders: per-frame received_at filter at the GPU consumer.** Independent of the drain-then-claim guarantee, the GPU container's `sqs_consumer.py` checks each frame's `received_at` (set by `streaming-router._route_audio_frame`); frames with `received_at < container_started_at - 5.0s` are silently dropped at the consumer (defense against any drain-cap edge case the queue-side missed). The drain-then-claim is the primary mechanism; this is the safety net.

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

**`streaming-router._route_audio_frame` change (adversarial CRIT-02 fix): per-Lambda in-memory cache of `connection_id` ➜ `frame_queue_url`.**

The router reads `frame_queue_url` from the DDB session row ONLY on the first `audio-frame` for a `connection_id` per Lambda warm execution. Subsequent frames for the same connection on the same Lambda hit the in-memory cache. Cache eviction (round-4 NIT correction): bounded at 1024 entries; eviction policy is **oldest-cached-first** (FIFO, NOT LRU; the eviction code below picks the minimum-by-cached_at, not minimum-by-last-accessed); TTL 30 minutes. Also: `_route_disconnect` invalidates the cache entry for the disconnecting connection (round-4 NIT correction) so a reconnect (new connection_id, may collide on hash) does not see stale data:

```python
# In Router.__init__:
self._queue_url_cache: dict[str, tuple[str, float]] = {}  # cid → (url, cached_at)
self._CACHE_MAX = 1024
self._CACHE_TTL_SECONDS = 1800

# In _route_audio_frame, before sqs.send_message:
cached = self._queue_url_cache.get(connection_id)
now = time.monotonic()
if cached and (now - cached[1]) < self._CACHE_TTL_SECONDS:
    queue_url = cached[0]
else:
    item = self._sessions.get_item(Key={"session_id": connection_id}).get("Item") or {}
    queue_url = item.get("frame_queue_url")
    if not queue_url:
        logger.info("audio-frame for %s with no queue_url; dropped", connection_id)
        return _ok({"route": "audio-frame", "dropped": "no-queue-url"})
    self._queue_url_cache[connection_id] = (queue_url, now)
    if len(self._queue_url_cache) > self._CACHE_MAX:
        oldest = min(self._queue_url_cache.items(), key=lambda kv: kv[1][1])[0]
        self._queue_url_cache.pop(oldest, None)
self._sqs.send_message(QueueUrl=queue_url, ...)

# In _route_disconnect (existing handler), invalidate the cache entry:
self._queue_url_cache.pop(connection_id, None)
```

**Cache miss rate:** API GW WS does not pin a connection to a specific Lambda. Under typical warm-pool sizes (provisioned-concurrency=3 to 5; see MED-03), each session's frames land on 3-5 different Lambda execution environments. Per-session DDB read count is ~3-5 across a session's lifetime, NOT 1 per frame. At 50 sessions/day × 5 reads = 250 DDB reads/day. Negligible cost.

**Cache miss latency penalty:** ~5-15 ms added to the FIRST audio-frame per Lambda-warm-pool member per session. Acceptable inside the budget (line 308: 10-30 ms streaming-router Lambda becomes 15-45 ms on first frame, then 10-30 ms steady-state).

**SPA's ready-message race (adversarial HIGH-04):** the SPA MUST wait for the GPU container's `{"type":"ready"}` message before sending the first `audio-frame`. If the SPA pushes a frame before the row has `frame_queue_url`, `streaming-router._route_audio_frame` returns the silent-drop path (logged at INFO, NOT WARN per HIGH-01 follow-up: WARN-level on a routine cold-start race would flood the log). To enforce this strictly:

1. The router's silent-drop path is INFO-level (not WARN). Real WARN entries from the router are reserved for genuine error paths.
2. SPA's `streaming-session.ts` keeps a `state = "connecting" | "spawning-gpu" | "ready" | ...` machine. Frames are queued client-side until `state === "ready"`. Once `ready` arrives, queued frames flush in order. The `spawning-gpu` UI shows "Bringing up the GPU; this takes about 90 seconds on cold start."

**Pool-queue cleanup:** when the lifecycle reaper terminates an instance, it clears `claimed_by` from the DDB pool row (single conditional UpdateItem, fast). **PurgeQueue is never called**; any residual frames from the freshly-ended session are discarded by the next claimant's drain-then-claim. No leaked queue claims, no 60-second tombstone, no message-loss race.

**Instance tag:** `panakoes:session-id=<id>`. Lets the gpu-spawner idle-reaper find orphaned instances.

**Idle reaper:** existing `gpu-spawner` cron OR a new EventBridge schedule that runs every 5 min, lists instances tagged with `panakoes:session-id`, joins against DDB session rows, terminates any instance whose session row is `disconnected` and has been so for ≥ 5 min OR has been `connecting` without a `connected` flip for ≥ 10 min (stuck spawn).

**DDB TTL on `streaming-sessions` (adversarial round-3 MED-04 + round-4 NIT correction):** the table gets a DDB TTL on a new `ttl_epoch_seconds` attribute. On normal disconnect, the lifecycle reaper writes `ttl_epoch_seconds = disconnected_at + 604800` (7 days). For stuck `connecting` rows (the row was created at `$connect` but the GPU never finished spawning, so `disconnected_at` is never set), `streaming-router._route_connect` ALSO writes a default `ttl_epoch_seconds = connected_at + 7200` (2 hours). This guarantees orphan rows auto-prune at most 2 hours after creation; the reaper overwrites the TTL to the longer 7-day window when it sees a legitimate `disconnected_at`. Terraform: `time_to_live { attribute_name = "ttl_epoch_seconds" enabled = true }` in `infra/dev/streaming-sessions-ddb/main.tf`.

**Stop-during-cold-start (adversarial round-3 MED-05, was Open Question 3):** if the user clicks Stop at 60 s while the GPU is still in its 102-192 s cold start, the SPA closes the WS; `streaming-router._route_disconnect` updates the DDB row to `disconnected`; the lifecycle-watcher on the still-loading container detects disconnect at next-poll boundary and exits gracefully OR the idle reaper terminates the orphan instance ~5 min later. **v1 accepts the spent compute** (~$0.013 per cancelled spawn, ~$0.05 worst case at the 192 s cold-start mark). Below the noise floor of demo cost. v1.5 lever: a "cancel-spawn" path that immediately terminates the instance on disconnect.

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
- **During the `spawning-gpu` state (HIGH-05 fix):** the SPA sends `{"action": "ping"}` every 60 seconds. The streaming-router's `ping`/`ping-echo` routes (registered in Terraform per BLOCK-01) accept it, return 200, no work. This keeps API Gateway's 10-minute idle timer fresh through any extended cold-start path. Costs $0.
- **Burst-flush rate-limiting (adversarial round-4 DEG-03 fix):** during `spawning-gpu`, the SPA queues PCM frames locally. When `ready` arrives, it MUST NOT dump the entire queue into the WS at line-rate (that stacks ~500 frames into SQS at the start, putting the GPU 50-192 s behind real-time and breaking the partial-latency promise). Instead, on `ready`, the SPA enters a **catch-up state** that replays the queued frames at **10 Hz** (2x normal capture rate) until drained, then switches to normal 5 Hz live capture. With a 100 s cold-start backlog (500 frames), catch-up drains in 50 s and the GPU runs at 1.5x real-time for that window (faster-whisper-large at 0.3-0.5 RTF on T4 = 100-160 ms per 200 ms frame, well within the budget). After catch-up, partials latch back to 200-500 ms.
- **Latency-budget caveat:** the 200-500 ms partials latency claim applies to STEADY-STATE only. The first 30-60 s after `ready` runs the catch-up flush; partial latency on those frames is ~5-20 s (frames are buffered, replayed faster than real-time, transcribed in order; the most-recent frame's partial appears with low marginal latency once catch-up completes). SPA help copy reflects this: "Transcription catching up..." status during the burst replay window.
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
- **Warmup clip and transitive deps (adversarial round-5 NIT-03):** the warmup file at `/opt/whisper/warmup-1s.wav` is also baked into the AMI alongside the weights. `librosa` (used by upstream's `warmup_asr` to decode the WAV) must be a regular Python dependency of `services/transcriber-stream/pyproject.toml`, NOT just an AMI-level binary. The container's `pip install` chain pulls librosa + its transitive (numba, soundfile) at image build, NOT at runtime; the AMI ships the wheels pre-resolved. The container's startup assertion (above) now verifies BOTH the weights directory AND the warmup clip's presence before invoking the factory.
- AMI lineage tracking: `infra/dev/batch/variables.tf` documents the GPU AMI ID pattern (the ECS-Optimized GPU AMI for Batch); the streaming path needs its own variable, e.g., `var.streaming_gpu_ami_id` in `infra/dev/ecs/` or wherever `gpu-spawner` lives.

## Observability metrics (adversarial round-3 XC-2)

All emitted via `cloudwatch.put_metric_data` to the `panakoes/streaming` namespace, with per-metric dimensions documented for dashboard wiring. The implementing agent for `transcriber-stream` is responsible for the GPU-side metrics; `streaming-router` and `gpu-spawner` agents emit theirs.

| Metric | Source | Dimensions | Purpose |
|---|---|---|---|
| `frame.routed` | streaming-router | `session_id` | Count of frames successfully forwarded to per-session queue. |
| `frame.dropped.no_queue_url` | streaming-router | `session_id`, `connection_age_seconds` | Count of frames silently dropped because the session row's `frame_queue_url` was absent (race-window detector). |
| `frame.capture_jitter_ms` | transcriber-stream | `session_id` | Difference between consecutive `ts_ms_delta` values minus the expected 200 ms cadence; surfaces SPA-side audio CAPTURE timing irregularities (round-4 NIT correction: this is browser-side jitter, NOT GPU processing delay). |
| `frame.gpu_processing_ms` | transcriber-stream | `session_id` | Time from `online.insert_audio_chunk` to the corresponding `process_iter()` return for a given frame; surfaces GPU inference latency separately from capture jitter. |
| `spawn.spot-no-capacity` | gpu-spawner | `availability_zone` | Spot exhaustion event count; AZ dimension lets operators see regional patterns. |
| `spawn.rejected.capacity-exhausted` | gpu-spawner | (none) | Count of 503 responses from pool-exhausted. Dashboards alarm at >0 per 5 min as the trigger to raise pool size. |
| `spawn.failed.ami-missing` | gpu-spawner | (none) | Operator-grade error; CloudWatch alarm pages. |
| `partial.emitted` | transcriber-stream | `session_id` | Count of partial transcripts pushed to SPA. |
| `final.emitted` | transcriber-stream | `session_id` | Count of committed final tokens (one per sentence-end). |
| `drain.triggered_by_410` | transcriber-stream | `session_id` | Distinguishes the 410-on-PostToConnection drain path from the normal `$disconnect` drain. |
| `drain.triggered_by_spot` | transcriber-stream | `session_id` | Spot-interruption drain count; CloudWatch alarm at >1 per hour signals capacity instability. |

Dashboards in `infra/dev/observability/dashboards/streaming.json` (new file in Stage 2's Terraform brief).

## Vendor NOTICE drift test (adversarial round-3 XC-3)

`services/transcriber-stream/tests/test_vendor_attribution.py` (new test in Stage 2) asserts that the `NOTICE` file's modifications list matches a canonical list in `services/transcriber-stream/vendor/README.md`. The test parses both files, diffs the modification-numbered entries, and fails if either side drifts. Prevents the "we changed the vendor code but forgot to update NOTICE" attribution drift. Trivial to write; binding contract enforcement.

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
| User closes browser tab mid-session (XC-1) | `$disconnect` fires immediately when WS closes; lifecycle watcher detects within 10 s | Container drains via `online.finish()`, writes final transcript to S3 + DDB, exits. Reconnection from a fresh tab opens a NEW session (new connection_id, new GPU spawn); no automatic stitch in v1. Spent compute on the abandoned container: ~$0.001. |

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
