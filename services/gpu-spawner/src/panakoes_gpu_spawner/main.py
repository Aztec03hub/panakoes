"""FastAPI entrypoint for the Panakoes GPU Spawner."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3
import structlog
import uvicorn
from fastapi import FastAPI

from panakoes_gpu_spawner.aws.ec2 import GpuInstanceManager, RunInstancesFailure
from panakoes_gpu_spawner.config import Settings
from panakoes_gpu_spawner.eventbridge_consumer import (
    EventBridgeConsumer,
    SpawnIntent,
)
from panakoes_gpu_spawner.pool_claim import PoolClaim, PoolExhaustedError
from panakoes_gpu_spawner.routes import health, spawn
from panakoes_gpu_spawner.status_publisher import StatusPublisher

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


settings = Settings()

logging.basicConfig(level=settings.log_level)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelNamesMapping()[settings.log_level]
    ),
)

logger = structlog.get_logger(__name__)


def make_spawn_callback(
    *,
    pool_claimer: PoolClaim,
    sessions_table: Table,
    manager: GpuInstanceManager,
    status_publisher: StatusPublisher | None = None,
    max_concurrent_sessions: int = 0,
) -> Callable[[SpawnIntent], None]:
    """Build the spawn callback the EventBridge consumer dispatches into.

    Returns a closure that, for each `SpawnIntent`:

    1. Claims one queue from the frame-queue pool. On `PoolExhaustedError`
       we log + re-raise so the consumer leaves the SQS message visible
       for redrive (eventually the DLQ).
    2. Stamps `frame_queue_url`, `pool_queue_id`, and `status='spawning-gpu'`
       onto the existing streaming-sessions row via a conditional
       `UpdateItem`. The `attribute_exists(session_id)` guard prevents
       a stale event from accidentally creating a row the streaming
       router never wrote (the router is the only legitimate creator).
    3. Calls `manager.run_instance` with the freshly-claimed queue URL.
       If `RunInstances` raises, the pool slot is released so we do not
       leak a claim on a launch that never happened.

    When a `status_publisher` is provided, the callback emits a status
    envelope back to the SPA at every step. Emits are best-effort:
    failures are swallowed inside `StatusPublisher.post`, and a missing
    publisher (None) is a no-op. The spawn pipeline runs identically
    with or without observability wired up.

    Exposed as a module-level factory so unit tests can construct the
    callback with mock pool/sessions/manager arguments without going
    through `_start_consumer_thread`.
    """

    def _post(
        intent: SpawnIntent,
        stage: str,
        detail: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort status emit wrapper. Never propagates errors."""
        if status_publisher is None:
            return
        try:
            status_publisher.post(
                connection_id=intent.session_id,
                stage=stage,
                detail=detail,
                extra=extra,
            )
        except Exception:
            # StatusPublisher.post already swallows internally, but
            # defence-in-depth in case the helper itself raises (e.g.
            # a bad mock in tests). Log and continue.
            logger.exception(
                "spawn_callback.status_post_failed",
                session_id=intent.session_id,
                stage=stage,
            )

    def _evict_oldest_if_at_cap(intent: SpawnIntent) -> None:
        """LRU evict before launching a new EC2 if we are at the cap.

        Keeps the system self-healing under the account's vCPU service
        quota: rather than the new spawn failing with `VcpuLimitExceeded`
        and the user staring at a dead session, we terminate the oldest
        instance (presumably a forgotten tab or orphan that escaped
        cleanup) and proceed. Eviction is best-effort: if DescribeInstances
        or TerminateInstances fails we log and continue; the subsequent
        RunInstances will surface the real capacity error if eviction
        did not actually free a slot.
        """
        if max_concurrent_sessions <= 0:
            return
        try:
            running = manager.list_running_instances()
        except Exception:
            logger.exception("spawn_callback.evict_describe_failed")
            return
        if len(running) < max_concurrent_sessions:
            return
        running.sort(
            key=lambda i: i.get("launched_at") or datetime.min.replace(tzinfo=UTC)
        )
        # Evict enough to leave room for one new instance.
        victims = running[: len(running) - max_concurrent_sessions + 1]
        for victim in victims:
            victim_id = victim.get("id")
            victim_sid = victim.get("session_id") or "(no-sid)"
            _post(
                intent,
                "session-evicted",
                (
                    f"At concurrent-session cap ({max_concurrent_sessions}); "
                    f"evicting older session {victim_sid}"
                ),
                extra={
                    "evicted_instance_id": victim_id,
                    "evicted_session_id": victim_sid,
                    "cap": max_concurrent_sessions,
                },
            )
            try:
                manager.terminate_instance(victim_id)
            except Exception:
                logger.exception(
                    "spawn_callback.evict_terminate_failed",
                    evicted_instance_id=victim_id,
                )

    def spawn_callback(intent: SpawnIntent) -> None:
        logger.info(
            "eventbridge_consumer.spawn",
            session_id=intent.session_id,
            user_id=intent.user_id,
        )
        _post(intent, "spawn-message-received", "Spawn intent picked up from queue")
        _evict_oldest_if_at_cap(intent)

        try:
            claim_result = pool_claimer.claim(intent.session_id)
        except PoolExhaustedError:
            logger.error(
                "spawn_callback.pool_exhausted",
                session_id=intent.session_id,
            )
            _post(
                intent,
                "spawn-failed",
                "All 32 frame queues are claimed; retry in ~60s",
                extra={"error_code": "pool-exhausted"},
            )
            # Re-raise so the EventBridge consumer leaves the SQS
            # message visible for redrive; eventually the DLQ.
            raise

        pool_id = claim_result.pool_id
        queue_url = claim_result.queue_url
        _post(
            intent,
            "pool-claimed",
            f"Pool queue {pool_id} claimed",
            extra={"pool_id": pool_id, "queue_url": queue_url},
        )

        try:
            sessions_table.update_item(
                Key={"session_id": intent.session_id},
                UpdateExpression=(
                    "SET frame_queue_url = :url, pool_queue_id = :pid, #st = :status"
                ),
                ConditionExpression="attribute_exists(session_id)",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":url": queue_url,
                    ":pid": pool_id,
                    ":status": "spawning-gpu",
                },
            )
        except Exception:
            # The session row went missing (streaming router never wrote
            # it, or it was deleted between $connect and our consume).
            # Release the pool slot so we do not leak the claim and
            # re-raise so SQS redrives.
            logger.exception(
                "spawn_callback.session_update_failed",
                session_id=intent.session_id,
                pool_id=pool_id,
            )
            _post(
                intent,
                "spawn-failed",
                "Session row update failed (row missing or DDB error)",
                extra={"error_code": "session-row-update-failed"},
            )
            pool_claimer.release(pool_id, intent.session_id)
            raise

        _post(intent, "session-row-updated", "Session row updated with frame_queue_url")
        _post(
            intent,
            "run-instances-issued",
            "Requesting EC2 g4dn.xlarge Spot",
        )

        try:
            instance_id = manager.run_instance(
                session_id=intent.session_id,
                user_id=intent.user_id,
                frame_queue_url=queue_url,
            )
        except RunInstancesFailure as exc:
            logger.exception(
                "spawn_callback.run_instance_failed",
                session_id=intent.session_id,
                pool_id=pool_id,
            )
            _post(
                intent,
                "spawn-failed",
                f"{exc.error_code}: {exc.aws_message}",
                extra={
                    "error_code": exc.error_code,
                    "aws_error_code": exc.aws_error_code,
                },
            )
            # Release the pool slot so a future redrive does not pile
            # up orphaned claims on a session whose EC2 never launched.
            pool_claimer.release(pool_id, intent.session_id)
            raise
        except Exception:
            logger.exception(
                "spawn_callback.run_instance_failed",
                session_id=intent.session_id,
                pool_id=pool_id,
            )
            _post(
                intent,
                "spawn-failed",
                "RunInstances failed with an unexpected error",
                extra={"error_code": "unknown-spawn-failure"},
            )
            pool_claimer.release(pool_id, intent.session_id)
            raise

        _post(
            intent,
            "instance-launching",
            f"Instance {instance_id} launching",
            extra={"instance_id": instance_id},
        )

    return spawn_callback


def _start_consumer_thread(stop_event: threading.Event) -> threading.Thread | None:
    """Start the EventBridge consumer in a daemon thread.

    Returns the thread (or None if the consumer is disabled). The
    consumer pulls SQS messages from the spawn queue and calls the
    closure built by `make_spawn_callback` for each one. Lives in a
    daemon thread so a uvicorn shutdown does not block on it;
    stop_event is set to ask the loop to exit cleanly between polls.
    """
    if not settings.spawn_queue_url:
        logger.info("eventbridge_consumer.disabled", reason="SPAWN_QUEUE_URL unset")
        return None

    sqs_client = boto3.client("sqs", region_name=settings.aws_region)
    ec2_client = boto3.client("ec2", region_name=settings.aws_region)
    ddb_resource = boto3.resource("dynamodb", region_name=settings.aws_region)
    sessions_table = ddb_resource.Table(settings.streaming_sessions_table)
    pool_table = ddb_resource.Table(settings.stream_frame_pool_table)

    # GpuInstanceManager takes individual settings fields, not a Settings
    # object. Mirror routes/spawn.py:get_instance_manager.
    manager = GpuInstanceManager(
        ami_id=settings.gpu_ami_id,
        instance_type=settings.gpu_instance_type,
        security_group_id=settings.gpu_security_group_id,
        subnet_id=settings.gpu_subnet_id,
        iam_instance_profile=settings.gpu_iam_instance_profile,
        project_tag=settings.project_tag,
        spawner_tag=settings.gpu_spawner_tag,
        session_manager_ws_endpoint=settings.session_manager_ws_endpoint,
        streaming_ws_mgmt_endpoint=settings.streaming_ws_mgmt_endpoint,
        stream_transcriber_image_uri=settings.stream_transcriber_image_uri,
        streaming_sessions_table=settings.streaming_sessions_table,
        stream_frame_pool_table=settings.stream_frame_pool_table,
        transcripts_bucket=settings.transcripts_bucket,
        region_name=settings.aws_region,
        client=ec2_client,
    )

    pool_claimer = PoolClaim(pool_table=pool_table, sqs_client=sqs_client)

    # Real-time observability: post per-stage status events back to the
    # SPA's WebSocket connection (session_id == connection_id). A blank
    # `streaming_ws_mgmt_endpoint` disables emission and the spawn
    # pipeline runs unchanged.
    status_publisher = StatusPublisher(
        endpoint=settings.streaming_ws_mgmt_endpoint,
        region_name=settings.aws_region,
    )

    spawn_callback = make_spawn_callback(
        pool_claimer=pool_claimer,
        sessions_table=sessions_table,
        manager=manager,
        status_publisher=status_publisher,
        max_concurrent_sessions=settings.max_concurrent_sessions,
    )

    consumer = EventBridgeConsumer(
        sqs_client=sqs_client,
        spawn_queue_url=settings.spawn_queue_url,
        spawn_callback=spawn_callback,
        wait_time_seconds=settings.spawn_consumer_wait_seconds,
    )

    def loop() -> None:
        logger.info("eventbridge_consumer.start", queue=settings.spawn_queue_url)
        while not stop_event.is_set():
            try:
                consumer.poll_once()
            except Exception:
                logger.exception("eventbridge_consumer.poll_failed")
                # brief backoff so a sustained AWS failure does not hot-spin
                stop_event.wait(timeout=2.0)
        logger.info("eventbridge_consumer.stopped")

    thread = threading.Thread(target=loop, name="eventbridge-consumer", daemon=True)
    thread.start()
    return thread


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage the EventBridge consumer's lifecycle alongside FastAPI."""
    stop_event = threading.Event()
    thread = _start_consumer_thread(stop_event)
    try:
        yield
    finally:
        stop_event.set()
        if thread is not None:
            thread.join(timeout=5.0)


app = FastAPI(title=f"panakoes-{settings.service_name}", lifespan=lifespan)
app.include_router(health.router)
app.include_router(spawn.router)


def main() -> None:
    """Run the service with `uvicorn` for direct invocation."""
    uvicorn.run(
        "panakoes_gpu_spawner.main:app",
        host="0.0.0.0",  # noqa: S104  # bind on all interfaces inside the container
        port=8000,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
