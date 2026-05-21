"""Unit tests for the spawn-callback factory in `main.py`.

The callback returned by `make_spawn_callback` is the integration
point between the EventBridge consumer (which decodes SQS messages
into `SpawnIntent` objects) and the three downstream side effects:
pool claim, streaming-sessions row update, EC2 RunInstances.

The callback's contract:

1. Claim a pool slot. On `PoolExhaustedError`, re-raise without
   touching DDB or EC2; the EventBridge consumer treats the
   exception as "leave the SQS message visible for redrive".
2. Update the streaming-sessions row to attach the claimed queue URL
   and pool id, with a conditional check that the row already exists
   (the streaming router is the only legitimate creator).
3. Call `manager.run_instance` with the claimed queue URL.
4. On any failure in step 2 or step 3, release the pool slot before
   re-raising so a redrive does not leak claims.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any
from unittest.mock import MagicMock

import pytest

from panakoes_gpu_spawner.aws.ec2 import RunInstancesFailure
from panakoes_gpu_spawner.eventbridge_consumer import SpawnIntent
from panakoes_gpu_spawner.main import make_spawn_callback
from panakoes_gpu_spawner.pool_claim import PoolClaimResult, PoolExhaustedError

_DEFAULT_QUEUE_URL = (
    "https://sqs.us-east-1.amazonaws.com/659225405128/panakoes-dev-stream-frames-pool-7"
)
_DEFAULT_CLAIM_RESULT = PoolClaimResult(queue_url=_DEFAULT_QUEUE_URL, pool_id=7)


def _intent(session_id: str = "sess-1", user_id: str = "user-1") -> SpawnIntent:
    """Build a SpawnIntent fixture without touching SQS."""
    return SpawnIntent(session_id=session_id, user_id=user_id, tenant_id="tenant-x")


def _build(
    *,
    claim_result: PoolClaimResult | Exception = _DEFAULT_CLAIM_RESULT,
    update_item_error: Exception | None = None,
    run_instance_error: Exception | None = None,
) -> tuple[Any, Any, Any]:
    """Return `(pool_claimer, sessions_table, manager)` mocks for tests."""
    pool_claimer = MagicMock()
    if isinstance(claim_result, Exception):
        pool_claimer.claim.side_effect = claim_result
    else:
        pool_claimer.claim.return_value = claim_result

    sessions_table = MagicMock()
    if update_item_error is not None:
        sessions_table.update_item.side_effect = update_item_error

    manager = MagicMock()
    if run_instance_error is not None:
        manager.run_instance.side_effect = run_instance_error
    else:
        manager.run_instance.return_value = "i-deadbeef"

    return pool_claimer, sessions_table, manager


@pytest.mark.unit
def test_spawn_callback_happy_path_claims_updates_session_runs_instance() -> None:
    """A clean dispatch claims, updates the session row, and launches EC2."""
    pool, sessions, manager = _build()
    callback = make_spawn_callback(pool_claimer=pool, sessions_table=sessions, manager=manager)

    callback(_intent())

    # The pool slot was claimed for the right session.
    pool.claim.assert_called_once_with("sess-1")
    # The session row was updated with the claimed URL + pool id.
    update_kwargs = sessions.update_item.call_args.kwargs
    assert update_kwargs["Key"] == {"session_id": "sess-1"}
    assert update_kwargs["ExpressionAttributeValues"][":pid"] == 7
    assert update_kwargs["ExpressionAttributeValues"][":url"] == _DEFAULT_QUEUE_URL
    assert update_kwargs["ExpressionAttributeValues"][":status"] == "spawning-gpu"
    # The session row update has the conditional guard so a missing row
    # cannot be silently created.
    assert "attribute_exists(session_id)" in update_kwargs["ConditionExpression"]
    # run_instance was called with the claimed URL.
    run_kwargs = manager.run_instance.call_args.kwargs
    assert run_kwargs["session_id"] == "sess-1"
    assert run_kwargs["user_id"] == "user-1"
    assert run_kwargs["frame_queue_url"] == _DEFAULT_QUEUE_URL
    # No release on the happy path.
    pool.release.assert_not_called()


@pytest.mark.unit
def test_spawn_callback_reraises_on_pool_exhausted_without_side_effects() -> None:
    """`PoolExhaustedError` re-raises and neither DDB nor EC2 is touched."""
    pool, sessions, manager = _build(
        claim_result=PoolExhaustedError("all 32 slots taken"),
    )
    callback = make_spawn_callback(pool_claimer=pool, sessions_table=sessions, manager=manager)

    with pytest.raises(PoolExhaustedError):
        callback(_intent())

    sessions.update_item.assert_not_called()
    manager.run_instance.assert_not_called()
    pool.release.assert_not_called()


@pytest.mark.unit
def test_spawn_callback_releases_pool_on_session_update_failure() -> None:
    """A missing session row releases the pool slot before re-raising."""
    pool, sessions, manager = _build(
        update_item_error=RuntimeError("ConditionalCheckFailedException"),
    )
    callback = make_spawn_callback(pool_claimer=pool, sessions_table=sessions, manager=manager)

    with pytest.raises(RuntimeError):
        callback(_intent())

    pool.release.assert_called_once_with(7, "sess-1")
    manager.run_instance.assert_not_called()


@pytest.mark.unit
def test_spawn_callback_releases_pool_on_run_instance_failure() -> None:
    """A RunInstances failure releases the pool slot before re-raising."""
    pool, sessions, manager = _build(
        run_instance_error=RuntimeError("InsufficientInstanceCapacity"),
    )
    callback = make_spawn_callback(pool_claimer=pool, sessions_table=sessions, manager=manager)

    with pytest.raises(RuntimeError):
        callback(_intent())

    # Session row update happened (we got past it before EC2 errored)
    sessions.update_item.assert_called_once()
    # Pool slot was released so a redrive does not leak claims.
    pool.release.assert_called_once_with(7, "sess-1")


# ===========================================================================
# Status-publisher emit assertions (real-time observability)
# ===========================================================================


def _stage_sequence(status_publisher: MagicMock) -> list[str]:
    """Pull the ordered list of `stage` arguments from a mock publisher."""
    return [call.kwargs["stage"] for call in status_publisher.post.call_args_list]


@pytest.mark.unit
def test_spawn_callback_emits_full_happy_path_status_sequence() -> None:
    """Happy path: caller sees the canonical 5-stage progress sequence."""
    pool, sessions, manager = _build()
    pub = MagicMock()
    callback = make_spawn_callback(
        pool_claimer=pool,
        sessions_table=sessions,
        manager=manager,
        status_publisher=pub,
    )

    callback(_intent())

    stages = _stage_sequence(pub)
    assert stages == [
        "spawn-message-received",
        "pool-claimed",
        "session-row-updated",
        "run-instances-issued",
        "instance-launching",
    ]
    # The pool-claimed emit carries the pool_id + queue_url extras.
    pool_claimed_call = next(
        call for call in pub.post.call_args_list if call.kwargs["stage"] == "pool-claimed"
    )
    assert pool_claimed_call.kwargs["extra"]["pool_id"] == 7
    assert pool_claimed_call.kwargs["extra"]["queue_url"] == _DEFAULT_QUEUE_URL
    # The terminal instance-launching emit carries the instance_id.
    launch_call = next(
        call for call in pub.post.call_args_list if call.kwargs["stage"] == "instance-launching"
    )
    assert launch_call.kwargs["extra"]["instance_id"] == "i-deadbeef"


@pytest.mark.unit
def test_spawn_callback_emits_pool_exhausted_spawn_failed() -> None:
    """A `PoolExhaustedError` emits `spawn-failed` before re-raising."""
    pool, sessions, manager = _build(
        claim_result=PoolExhaustedError("all 32 slots taken"),
    )
    pub = MagicMock()
    callback = make_spawn_callback(
        pool_claimer=pool,
        sessions_table=sessions,
        manager=manager,
        status_publisher=pub,
    )

    with pytest.raises(PoolExhaustedError):
        callback(_intent())

    stages = _stage_sequence(pub)
    assert "spawn-message-received" in stages
    assert "spawn-failed" in stages
    failed_call = next(
        call for call in pub.post.call_args_list if call.kwargs["stage"] == "spawn-failed"
    )
    assert failed_call.kwargs["extra"]["error_code"] == "pool-exhausted"


@pytest.mark.unit
def test_spawn_callback_emits_spawn_failed_on_run_instances_failure() -> None:
    """A structured `RunInstancesFailure` flows the error_code into the
    status envelope so the SPA event log shows the actual cause."""
    pool, sessions, manager = _build(
        run_instance_error=RunInstancesFailure(
            error_code="spot-no-capacity",
            aws_error_code="InsufficientInstanceCapacity",
            aws_message="No capacity in us-east-1c",
        ),
    )
    pub = MagicMock()
    callback = make_spawn_callback(
        pool_claimer=pool,
        sessions_table=sessions,
        manager=manager,
        status_publisher=pub,
    )

    with pytest.raises(RunInstancesFailure):
        callback(_intent())

    stages = _stage_sequence(pub)
    assert "run-instances-issued" in stages
    assert "spawn-failed" in stages
    failed_call = next(
        call for call in pub.post.call_args_list if call.kwargs["stage"] == "spawn-failed"
    )
    extra = failed_call.kwargs["extra"]
    assert extra["error_code"] == "spot-no-capacity"
    assert extra["aws_error_code"] == "InsufficientInstanceCapacity"


@pytest.mark.unit
def test_spawn_callback_emits_spawn_failed_on_unknown_run_instance_error() -> None:
    """A non-structured exception still emits a status envelope."""
    pool, sessions, manager = _build(
        run_instance_error=RuntimeError("network down"),
    )
    pub = MagicMock()
    callback = make_spawn_callback(
        pool_claimer=pool,
        sessions_table=sessions,
        manager=manager,
        status_publisher=pub,
    )

    with pytest.raises(RuntimeError):
        callback(_intent())

    stages = _stage_sequence(pub)
    assert "spawn-failed" in stages
    failed_call = next(
        call for call in pub.post.call_args_list if call.kwargs["stage"] == "spawn-failed"
    )
    assert failed_call.kwargs["extra"]["error_code"] == "unknown-spawn-failure"


@pytest.mark.unit
def test_spawn_callback_works_without_a_status_publisher() -> None:
    """When no publisher is provided the spawn pipeline still runs."""
    pool, sessions, manager = _build()
    callback = make_spawn_callback(
        pool_claimer=pool,
        sessions_table=sessions,
        manager=manager,
        status_publisher=None,
    )

    callback(_intent())

    pool.claim.assert_called_once()
    sessions.update_item.assert_called_once()
    manager.run_instance.assert_called_once()


@pytest.mark.unit
def test_spawn_callback_swallows_status_publisher_failures() -> None:
    """A status emit that raises must not break the spawn pipeline."""
    pool, sessions, manager = _build()
    pub = MagicMock()
    pub.post.side_effect = RuntimeError("publisher broken")
    callback = make_spawn_callback(
        pool_claimer=pool,
        sessions_table=sessions,
        manager=manager,
        status_publisher=pub,
    )

    # The spawn must complete normally despite the publisher being broken.
    callback(_intent())
    sessions.update_item.assert_called_once()
    manager.run_instance.assert_called_once()


# ---------------------------------------------------------------------------
# LRU eviction tests (max_concurrent_sessions)
# ---------------------------------------------------------------------------


def _instance(*, id: str, sid: str | None, launched_at) -> dict:
    """Helper: fake list_running_instances entry."""
    return {"id": id, "session_id": sid, "launched_at": launched_at}


@pytest.mark.unit
def test_spawn_callback_no_evict_when_below_cap() -> None:
    """3 running, cap 4: no eviction, spawn proceeds normally."""
    from datetime import datetime

    pool, sessions, manager = _build()
    manager.list_running_instances.return_value = [
        _instance(id="i-a", sid="sess-a", launched_at=datetime(2026, 5, 21, 10, 0, tzinfo=UTC)),
        _instance(id="i-b", sid="sess-b", launched_at=datetime(2026, 5, 21, 10, 5, tzinfo=UTC)),
        _instance(id="i-c", sid="sess-c", launched_at=datetime(2026, 5, 21, 10, 10, tzinfo=UTC)),
    ]
    callback = make_spawn_callback(
        pool_claimer=pool, sessions_table=sessions, manager=manager, max_concurrent_sessions=4
    )

    callback(_intent("sess-new"))

    manager.terminate_instance.assert_not_called()
    manager.run_instance.assert_called_once()


@pytest.mark.unit
def test_spawn_callback_evicts_oldest_at_cap() -> None:
    """Cap 1, 1 already running: evict the running one, spawn proceeds."""
    from datetime import datetime

    pool, sessions, manager = _build()
    manager.list_running_instances.return_value = [
        _instance(id="i-old", sid="sess-old", launched_at=datetime(2026, 5, 21, 9, 0, tzinfo=UTC)),
    ]
    pub = MagicMock()
    callback = make_spawn_callback(
        pool_claimer=pool,
        sessions_table=sessions,
        manager=manager,
        status_publisher=pub,
        max_concurrent_sessions=1,
    )

    callback(_intent("sess-new"))

    manager.terminate_instance.assert_called_once_with("i-old")
    manager.run_instance.assert_called_once()
    # session-evicted status envelope emitted before the spawn proceeds.
    stages = _stage_sequence(pub)
    assert "session-evicted" in stages
    evicted_call = next(
        call for call in pub.post.call_args_list if call.kwargs["stage"] == "session-evicted"
    )
    assert evicted_call.kwargs["extra"]["evicted_instance_id"] == "i-old"
    assert evicted_call.kwargs["extra"]["evicted_session_id"] == "sess-old"


@pytest.mark.unit
def test_spawn_callback_evicts_multiple_when_over_cap() -> None:
    """3 running, cap 1: evict 3 (leaving room for the new one)."""
    from datetime import datetime

    pool, sessions, manager = _build()
    manager.list_running_instances.return_value = [
        _instance(id="i-mid", sid="sess-mid", launched_at=datetime(2026, 5, 21, 10, 5, tzinfo=UTC)),
        _instance(id="i-old", sid="sess-old", launched_at=datetime(2026, 5, 21, 10, 0, tzinfo=UTC)),
        _instance(
            id="i-recent", sid="sess-recent", launched_at=datetime(2026, 5, 21, 10, 10, tzinfo=UTC)
        ),
    ]
    callback = make_spawn_callback(
        pool_claimer=pool, sessions_table=sessions, manager=manager, max_concurrent_sessions=1
    )

    callback(_intent("sess-new"))

    # All 3 evicted, oldest first.
    assert manager.terminate_instance.call_count == 3
    evicted_ids = [c.args[0] for c in manager.terminate_instance.call_args_list]
    assert evicted_ids == ["i-old", "i-mid", "i-recent"]


@pytest.mark.unit
def test_spawn_callback_evict_describe_failure_swallowed() -> None:
    """If list_running_instances raises, eviction is skipped + spawn proceeds."""
    pool, sessions, manager = _build()
    manager.list_running_instances.side_effect = RuntimeError("describe failed")
    callback = make_spawn_callback(
        pool_claimer=pool, sessions_table=sessions, manager=manager, max_concurrent_sessions=1
    )

    callback(_intent("sess-new"))

    manager.terminate_instance.assert_not_called()
    manager.run_instance.assert_called_once()


@pytest.mark.unit
def test_spawn_callback_evict_disabled_when_cap_zero() -> None:
    """`max_concurrent_sessions=0` disables LRU evict entirely."""
    pool, sessions, manager = _build()
    callback = make_spawn_callback(
        pool_claimer=pool, sessions_table=sessions, manager=manager, max_concurrent_sessions=0
    )

    callback(_intent("sess-new"))

    manager.list_running_instances.assert_not_called()
    manager.terminate_instance.assert_not_called()
