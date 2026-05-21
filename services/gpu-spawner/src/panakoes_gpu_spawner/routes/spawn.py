"""Spawn endpoints: launch, describe, terminate.

Authorization model:

- `POST /spawn` and `DELETE /spawn/{instance_id}` require an
  `actor_type=service` JWT. The session-manager mints these for itself
  before calling us; user JWTs reaching these routes get 403 (not 401:
  the caller IS authenticated, just not authorised).
- `GET /spawn/{instance_id}` accepts both actor types. User actors are
  scoped to their own session via the `SessionId` tag check; service
  actors skip that gate because they are managing the lifecycle.

Defence in depth: every termination path verifies the instance's
`Spawner` tag matches our identity before the EC2 API call. The IAM
policy enforces this too, but the application-layer check stops a
confused-deputy from accidentally terminating someone else's instance
even if the IAM policy were ever loosened in error.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from panakoes_audit import record_event

from panakoes_gpu_spawner.auth import (
    AuthenticatedPrincipal,
    get_current_principal,
    require_service_actor,
)
from panakoes_gpu_spawner.aws.ec2 import GpuInstanceManager
from panakoes_gpu_spawner.config import Settings
from panakoes_gpu_spawner.models import (
    InstanceStateResponse,
    SpawnRequest,
    SpawnResponse,
    TerminateResponse,
)

router = APIRouter(prefix="/spawn", tags=["spawn"])


def get_settings() -> Settings:
    """FastAPI dependency: build a fresh `Settings` per request.

    Tests override this with a fixture that returns deterministic env
    values; production accepts the cost of re-instantiation.
    """
    return Settings()


def get_instance_manager(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GpuInstanceManager:
    """FastAPI dependency: provide an EC2-backed `GpuInstanceManager`."""
    return GpuInstanceManager(
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
    )


@router.post(
    "",
    response_model=SpawnResponse,
    status_code=status.HTTP_201_CREATED,
)
async def spawn_instance(
    request: SpawnRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_service_actor)],
    manager: Annotated[GpuInstanceManager, Depends(get_instance_manager)],
) -> SpawnResponse:
    """Launch one Spot g4dn.xlarge for the caller's session.

    This HTTP route is the legacy session-manager dispatch path. The
    real streaming dispatch flows through the EventBridge consumer in
    `main._start_consumer_thread`, which claims a pool queue before
    calling `run_instance`. Service callers reaching this route do not
    get a pool slot wired in; the spawned instance launches but its
    transcriber-stream container will exit on the empty
    `FRAME_QUEUE_URL`. Production traffic goes through the consumer.
    """
    instance_id = manager.run_instance(
        session_id=request.session_id,
        user_id=request.user_id,
        frame_queue_url="",
    )

    await record_event(
        actor_id=principal.actor_id,
        actor_type="service",
        action="gpu_spawner.spawned",
        resource_type="ec2_instance",
        resource_id=instance_id,
        source_service="gpu-spawner",
        details={
            "session_id": request.session_id,
            "user_id": request.user_id,
        },
    )

    return SpawnResponse(instance_id=instance_id)


@router.get("/{instance_id}", response_model=InstanceStateResponse)
async def get_instance_state(
    instance_id: str,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    manager: Annotated[GpuInstanceManager, Depends(get_instance_manager)],
) -> InstanceStateResponse:
    """Return the latest state of `instance_id`.

    Owner-scoped for user actors: the instance's `SessionId` tag must
    match the caller's `session_id` claim, otherwise we 404 (not 403)
    so the existence of unrelated instances is not leaked. Service
    actors skip this check because they are the lifecycle owner.
    """
    details = manager.describe_instance(instance_id)
    if details is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="instance not found",
        )

    if details.tags.get("Spawner") != manager.spawner_tag:
        # Confused-deputy guard: never report on instances we did not
        # launch, even if AWS returned them via a wide-open IAM scope.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="instance not found",
        )

    if principal.actor_type == "user" and details.tags.get("SessionId") != principal.session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="instance not found",
        )

    return InstanceStateResponse(
        instance_id=details.instance_id,
        state=details.state,
        session_id=details.tags.get("SessionId"),
        user_id=details.tags.get("UserId"),
    )


@router.delete("/{instance_id}", response_model=TerminateResponse)
async def terminate_instance(
    instance_id: str,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_service_actor)],
    manager: Annotated[GpuInstanceManager, Depends(get_instance_manager)],
) -> TerminateResponse:
    """Terminate `instance_id` after verifying we launched it.

    The `Spawner` tag check is the application-layer half of the same
    ownership invariant the IAM policy enforces. Both must agree before
    EC2 sees the call.
    """
    details = manager.describe_instance(instance_id)
    if details is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="instance not found",
        )

    if details.tags.get("Spawner") != manager.spawner_tag:
        # Defense against confused-deputy: refuse to terminate
        # instances we did not launch even if IAM would let us.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="instance not owned by this spawner",
        )

    previous_state, current_state = manager.terminate_instance(instance_id)

    await record_event(
        actor_id=principal.actor_id,
        actor_type="service",
        action="gpu_spawner.terminated",
        resource_type="ec2_instance",
        resource_id=instance_id,
        source_service="gpu-spawner",
        details={
            "session_id": details.tags.get("SessionId"),
            "user_id": details.tags.get("UserId"),
            "previous_state": previous_state,
            "current_state": current_state,
        },
    )

    return TerminateResponse(
        instance_id=instance_id,
        previous_state=previous_state,
        current_state=current_state,
    )
