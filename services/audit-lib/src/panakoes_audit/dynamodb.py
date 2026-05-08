"""DynamoDB-backed audit store.

The audit table is provisioned by Terraform (out of scope for this
library; see ADR-017 and the future `infra/global/audit/` module).
The schema this store assumes:

- Partition key `pk = "AUDIT#" + source_service + "#" + actor_id`
- Sort key `sk = timestamp_iso + "#" + request_id`

That key shape lets us cheaply query "all events from service X about
actor Y in time range Z" without a scan, which is the dominant access
pattern for the audit dashboard.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import boto3

from panakoes_audit.event import AuditEvent

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


class DynamoDBAuditStore:
    """Persist audit events to a DynamoDB table.

    A single table is shared by every service. Item keys are derived
    from the event itself, so callers do not need to know the schema.
    """

    def __init__(
        self,
        table_name: str,
        region_name: str = "us-east-1",
    ) -> None:
        """Bind to the named table in the given region.

        `boto3` resolves credentials via the default chain
        (env vars, EC2 instance profile, etc.) so this constructor
        carries no secret material.
        """
        self._table_name = table_name
        self._region_name = region_name
        self._resource = boto3.resource("dynamodb", region_name=region_name)
        self._table: Table = self._resource.Table(table_name)

    @property
    def table_name(self) -> str:
        """The DynamoDB table this store writes to."""
        return self._table_name

    @property
    def region_name(self) -> str:
        """The AWS region the boto3 client is bound to."""
        return self._region_name

    @staticmethod
    def _build_item(event: AuditEvent) -> dict[str, Any]:
        """Serialize `event` into a DynamoDB-ready item dict.

        We round-trip through `model_dump_json` so types Pydantic
        knows how to render (datetime, UUID, etc.) come out as strings
        DynamoDB accepts. Then we layer the partition/sort keys on top.
        """
        base: dict[str, Any] = json.loads(event.model_dump_json())
        pk = f"AUDIT#{event.source_service}#{event.actor_id}"
        sk = f"{event.timestamp.isoformat()}#{event.request_id}"
        return {"pk": pk, "sk": sk, **base}

    async def record(self, event: AuditEvent) -> None:
        """Write `event` to the underlying DynamoDB table.

        boto3 is synchronous; we wrap the call here so the public
        `AuditStore.record` contract stays async. For the per-event
        write volumes the audit log sees, the cost of a sync call on
        the event loop is negligible. If that ever changes, swap to
        `aioboto3` here without touching callers.
        """
        item = self._build_item(event)
        self._table.put_item(Item=item)
