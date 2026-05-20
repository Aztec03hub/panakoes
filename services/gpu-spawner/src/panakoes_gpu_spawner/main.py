"""FastAPI entrypoint for the Panakoes GPU Spawner."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import boto3
import structlog
import uvicorn
from fastapi import FastAPI

from panakoes_gpu_spawner.aws.ec2 import GpuInstanceManager
from panakoes_gpu_spawner.config import Settings
from panakoes_gpu_spawner.eventbridge_consumer import (
    EventBridgeConsumer,
    SpawnIntent,
)
from panakoes_gpu_spawner.routes import health, spawn

settings = Settings()

logging.basicConfig(level=settings.log_level)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelNamesMapping()[settings.log_level]
    ),
)

logger = structlog.get_logger(__name__)


def _start_consumer_thread(stop_event: threading.Event) -> threading.Thread | None:
    """Start the EventBridge consumer in a daemon thread.

    Returns the thread (or None if the consumer is disabled). The
    consumer pulls SQS messages from the spawn queue and calls
    GpuInstanceManager.run_instance for each one. Lives in a daemon
    thread so a uvicorn shutdown does not block on it; stop_event is
    set to ask the loop to exit cleanly between polls.
    """
    if not settings.spawn_queue_url:
        logger.info("eventbridge_consumer.disabled", reason="SPAWN_QUEUE_URL unset")
        return None

    sqs_client = boto3.client("sqs", region_name=settings.aws_region)
    ec2_client = boto3.client("ec2", region_name=settings.aws_region)
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
        region_name=settings.aws_region,
        client=ec2_client,
    )

    def spawn_callback(intent: SpawnIntent) -> None:
        logger.info(
            "eventbridge_consumer.spawn",
            session_id=intent.session_id,
            user_id=intent.user_id,
        )
        manager.run_instance(session_id=intent.session_id, user_id=intent.user_id)

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
