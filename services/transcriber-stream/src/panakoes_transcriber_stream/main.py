"""asyncio entrypoint for the ``transcriber-stream`` container.

The flow follows design v7, "transcriber-stream container -> Main loop".
See ``docs/design/realtime-streaming-transcription.md`` for the full
spec; the inline comments below cite the round-N fixes that shaped each
step.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import numpy as np

from .asr_proxy import SeededOnlineASRProcessor
from .config import Config, ConfigError, load_config_from_env
from .lifecycle import LifecycleWatcher, SpotDrainHandler
from .persistence import Persistence, read_prompt_seed_from_ddb
from .sqs_consumer import SQSConsumer
from .transcribe import build_asr, chunk_tokens_for_ws
from .ws_publisher import WsPublisher

logger = logging.getLogger("panakoes_transcriber_stream")


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string (millisecond precision)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


async def _emit_status(
    ws: WsPublisher,
    *,
    stage: str,
    detail: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Best-effort status envelope emit; never raises.

    A failure here MUST NOT abort container startup or runtime. The
    underlying `WsPublisher.send` already swallows `GoneException`; we
    also catch the broad case in case of a transient transport fault.
    """

    envelope: dict[str, Any] = {
        "type": "status",
        "stage": stage,
        "detail": detail,
        "ts": _now_iso(),
    }
    if extra:
        for key, value in extra.items():
            if key in ("type", "stage", "detail", "ts"):
                continue
            envelope[key] = value
    try:
        await ws.send(envelope)
    except Exception:
        logger.warning("transcriber_stream_status_emit_failed stage=%s", stage)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def _assert_ami_assets(cfg: Config, ws: WsPublisher) -> None:
    """Round-5 NIT-02 + NIT-03: pre-factory AMI-asset assertion.

    Fails fast if the AMI bake is missing the model directory or the
    warmup clip, BEFORE the long backend-factory call. On failure we
    push a structured error to the SPA so the operator-facing message
    is precise instead of "GPU never came up".
    """

    for path in (cfg.model_dir, cfg.warmup_clip_path):
        if not os.path.exists(path):  # noqa: ASYNC240 (one-shot bootstrap check)
            await ws.send({"type": "error", "code": "ami-asset-missing", "path": path})
            raise RuntimeError(f"AMI missing expected asset at {path}")


async def _drain_and_exit(
    *,
    online: SeededOnlineASRProcessor,
    ws: WsPublisher,
    persist: Persistence,
    loop: asyncio.AbstractEventLoop,
    gpu_pool: ThreadPoolExecutor,
    reason: str,
    status: str,
) -> None:
    """Run the shared end-of-session finalize path."""

    try:
        remaining_tokens, final_processed_upto = await loop.run_in_executor(gpu_pool, online.finish)
    except Exception:
        logger.exception("transcriber_stream_finish_failed", extra={"reason": reason})
        remaining_tokens, final_processed_upto = [], 0.0

    await persist.write_final_tokens(
        remaining_tokens,
        audio_upto=final_processed_upto,
        committed=True,
        status=status,
    )

    if not ws.gone:
        chunks = chunk_tokens_for_ws(remaining_tokens)
        total = len(chunks)
        for seq, chunk in enumerate(chunks):
            await ws.send(
                {
                    "type": "final-chunk",
                    "seq": seq,
                    "total": total,
                    "tokens": chunk,
                }
            )
        await ws.send(
            {
                "type": "ended",
                "reason": reason,
                "expected_chunks": total,
                "audio_upto": final_processed_upto,
            }
        )


async def run(
    cfg: Config,
    *,
    factory: Any | None = None,
    s3_client: Any | None = None,
    ddb_resource: Any | None = None,
    sqs_client: Any | None = None,
    ws_client: Any | None = None,
    cuda_summary: dict[str, Any] | None = None,
) -> int:
    """Run a single streaming-session container to completion."""

    loop = asyncio.get_running_loop()

    # WsPublisher must exist before any error path can ws.send; its
    # underlying boto3 client is lazy-initialized.
    ws = WsPublisher(cfg.ws_endpoint, cfg.connection_id, client=ws_client)

    # Real-time observability: the container has just started inside the
    # GPU instance. From here the SPA's event log sees status emits at
    # every meaningful phase boundary so the user does not stare at a
    # silent `spawning-gpu` panel for minutes.
    await _emit_status(
        ws,
        stage="container-started",
        detail=f"Transcriber container started ({cfg.model_size})",
        extra={
            "model_size": cfg.model_size,
            "connection_id": cfg.connection_id,
        },
    )

    # Round-5 NIT-02 + NIT-03: assert pre-baked AMI assets exist BEFORE
    # the (slow) backend factory call so a missing bake fails fast.
    await _assert_ami_assets(cfg, ws)

    cuda_extra = cuda_summary or {}
    cuda_detail = (
        f"GPU ready: cuda_available={cuda_extra.get('cuda_available', 'n/a')}"
        f" device_count={cuda_extra.get('device_count', 'n/a')}"
    )
    await _emit_status(
        ws,
        stage="cuda-checked",
        detail=cuda_detail,
        extra=cuda_extra,
    )

    # Round-4 DEG-02: lifecycle + Spot watchers must be created BEFORE
    # the factory call so a $disconnect or Spot warning during the
    # ~35 s model load is observable.
    lifecycle = LifecycleWatcher(
        cfg.sessions_table,
        cfg.session_id,
        ddb_resource=ddb_resource,
    )
    spot = SpotDrainHandler()
    lifecycle_task = asyncio.create_task(lifecycle.watch())
    spot_task = asyncio.create_task(spot.watch())

    # Round-5 NIT-01: wrap the factory phase in try/finally so a factory
    # exception cannot leak the pre-spawned watcher tasks.
    factory_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="factory")
    try:
        await _emit_status(
            ws,
            stage="model-loading",
            detail="Loading Whisper model from baked AMI",
        )
        load_started_at = time.monotonic()
        try:
            asr = await loop.run_in_executor(factory_pool, lambda: build_asr(cfg, factory=factory))
        except Exception:
            for task in (lifecycle_task, spot_task):
                task.cancel()
            await ws.send(
                {
                    "type": "error",
                    "code": "model-load-failed",
                    "message": "backend_factory raised",
                }
            )
            raise
    finally:
        factory_pool.shutdown(wait=False)

    load_elapsed_s = round(time.monotonic() - load_started_at, 2)
    await _emit_status(
        ws,
        stage="model-loaded",
        detail=f"Whisper model loaded ({load_elapsed_s}s)",
        extra={"elapsed_s": load_elapsed_s},
    )

    # DEG-01 fix: SeededOnlineASRProcessor injects prompt seed via
    # prompt() override; no mutation of committed[].
    prompt_seed = read_prompt_seed_from_ddb(
        cfg.session_id,
        sessions_table=cfg.sessions_table,
        ddb_resource=ddb_resource,
    )
    online = SeededOnlineASRProcessor(asr, prompt_seed_text=prompt_seed, logfile=sys.stderr)
    await _emit_status(
        ws,
        stage="prompt-seed-read",
        detail="Prompt seed read from session row",
    )

    # If $disconnect or Spot warning fired during the cold start, bail
    # before opening the consume loop.
    if lifecycle.event.is_set() or spot.event.is_set():
        await ws.send({"type": "error", "code": "session-cancelled-during-spawn"})
        for task in (lifecycle_task, spot_task):
            task.cancel()
        return 0

    sqs = SQSConsumer(cfg.frame_queue_url, client=sqs_client)
    persist = Persistence(
        cfg.transcripts_bucket,
        cfg.sessions_table,
        cfg.session_id,
        s3_client=s3_client,
        ddb_resource=ddb_resource,
    )

    gpu_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper-gpu")

    keepalive_task = asyncio.create_task(
        ws.keepalive_pings(interval_seconds=cfg.keepalive_ping_seconds)
    )

    # Wire the watcher events into the SQS consumer's stop flag so the
    # consume loop unblocks promptly when a $disconnect or Spot warning
    # fires even if the queue is currently empty (between frames).
    async def _bridge_events_to_sqs_stop() -> None:
        try:
            await asyncio.wait(
                [
                    asyncio.create_task(lifecycle.event.wait()),
                    asyncio.create_task(spot.event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            sqs.stop()
        except asyncio.CancelledError:
            return

    bridge_task = asyncio.create_task(_bridge_events_to_sqs_stop())

    # `warmup-complete` and the canonical `ready` envelope land back-to-back.
    # `build_asr` runs `warmup_asr` internally; by the time we reach here the
    # warmup transcription pass has been done. The status event keeps the
    # SPA event log explicit about the phase boundary; the `ready` envelope
    # is the contract the existing SPA state machine transitions on.
    await _emit_status(
        ws,
        stage="warmup-complete",
        detail="Warmup pass complete",
    )
    await ws.send({"type": "ready"})

    try:
        async for pcm_chunk in sqs.frames():
            samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32) / 32768.0
            online.insert_audio_chunk(samples)

            try:
                committed_tokens, processed_upto = await loop.run_in_executor(
                    gpu_pool, online.process_iter
                )
            except Exception:
                logger.exception("transcriber_stream_process_iter_failed")
                continue

            for token in committed_tokens:
                ok = await ws.send(
                    {
                        "type": "final",
                        "text": getattr(token, "text", ""),
                        "start": getattr(token, "start", None),
                        "end": getattr(token, "end", None),
                        "probability": getattr(token, "probability", None),
                        "audio_upto": processed_upto,
                    }
                )
                if not ok:
                    # ws.gone => the on_gone path will fire and we will
                    # drain on the next iteration check below.
                    break

            await persist.update_last_transcript_tokens(committed_tokens, audio_upto=processed_upto)

            if spot.event.is_set():
                sqs.stop()
                await _drain_and_exit(
                    online=online,
                    ws=ws,
                    persist=persist,
                    loop=loop,
                    gpu_pool=gpu_pool,
                    reason="spot-interrupted",
                    status="interrupted",
                )
                return 0

            if lifecycle.event.is_set() or ws.gone:
                sqs.stop()
                break

        # Normal end-of-session path.
        await _drain_and_exit(
            online=online,
            ws=ws,
            persist=persist,
            loop=loop,
            gpu_pool=gpu_pool,
            reason="disconnected" if not ws.gone else "disconnected_by_410",
            status="ended" if not ws.gone else "disconnected_by_410",
        )
        return 0
    finally:
        for task in (lifecycle_task, spot_task, keepalive_task, bridge_task):
            task.cancel()
        gpu_pool.shutdown(wait=False)


def _log_cuda_environment(model_dir: str) -> dict[str, Any]:
    """Emit GPU + CUDA + filesystem observability at the top of startup.

    Stage 4 debug aid: the container was hanging silently in the
    backend factory (`WhisperModel(...)` load). Logging torch.cuda
    state and the on-AMI model directory listing surfaces the kind of
    silent CPU-fallback or missing-asset case the load is suspected of
    hitting. Best-effort: any logging failure here is swallowed so we
    never break startup over diagnostics.
    """

    summary: dict[str, Any] = {
        "cuda_available": False,
        "device_count": 0,
        "device_name": "n/a",
    }
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        device_count = int(torch.cuda.device_count()) if cuda_available else 0
        device_name = (
            torch.cuda.get_device_name(0) if cuda_available and device_count else "n/a"
        )
        summary["cuda_available"] = cuda_available
        summary["device_count"] = device_count
        summary["device_name"] = device_name
        logger.info(
            f"stage4_cuda_check torch={torch.__version__} cuda_available={cuda_available} "
            f"device_count={device_count} device_name={device_name!r} "
            f"torch_cuda_build={getattr(torch.version, 'cuda', 'n/a')}"
        )
    except Exception as exc:
        logger.warning(
            f"stage4_cuda_check_failed exc_type={type(exc).__name__} exc_msg={str(exc)[:200]!r}"
        )

    try:
        import ctranslate2

        logger.info(
            f"stage4_ctranslate2_check ct2_version={ctranslate2.__version__} "
            f"ct2_cuda_device_count={int(ctranslate2.get_cuda_device_count())}"
        )
    except Exception as exc:
        logger.warning(
            f"stage4_ctranslate2_check_failed exc_type={type(exc).__name__} "
            f"exc_msg={str(exc)[:200]!r}"
        )

    try:
        entries = os.listdir(model_dir) if os.path.isdir(model_dir) else []
        sizes = {
            name: os.path.getsize(os.path.join(model_dir, name))
            for name in entries
            if os.path.isfile(os.path.join(model_dir, name))
        }
        logger.info(
            f"stage4_model_dir_check path={model_dir!r} exists={os.path.isdir(model_dir)} "
            f"files={sizes}"
        )
    except Exception as exc:
        logger.warning(
            f"stage4_model_dir_check_failed exc_type={type(exc).__name__} "
            f"exc_msg={str(exc)[:200]!r}"
        )
    return summary


async def _amain() -> int:
    try:
        cfg = load_config_from_env()
    except ConfigError as exc:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
        logger.error("transcriber_stream_config_error", extra={"error": str(exc)})
        return 1
    _configure_logging(cfg.log_level)
    logger.info(
        "transcriber_stream_starting",
        extra={
            "session_id": cfg.session_id,
            "connection_id": cfg.connection_id,
            "model_size": cfg.model_size,
        },
    )
    cuda_summary = _log_cuda_environment(cfg.model_dir)
    return await run(cfg, cuda_summary=cuda_summary)


def main() -> int:
    """Synchronous wrapper invoked by the container ``CMD``."""

    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
