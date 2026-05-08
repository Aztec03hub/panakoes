"""S3 and DynamoDB helpers for the summarization service.

Centralizes the key shapes so handlers and tests share one source of
truth. Owner-scoping is enforced here: every read and write derives the
S3 prefix and DynamoDB partition key from the user_id supplied by the
JWT layer, so a handler cannot accidentally fan out across users.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import ClientError

from panakoes_summarization.models import SummaryRecord, Tier

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table
    from mypy_boto3_s3.client import S3Client


def transcript_key(user_id: str, transcript_id: str) -> str:
    """Return the canonical S3 key for a stored transcript text file."""
    return f"transcripts/{user_id}/{transcript_id}.txt"


def summary_key(user_id: str, transcript_id: str) -> str:
    """Return the canonical S3 key for a stored summary markdown file."""
    return f"summaries/{user_id}/{transcript_id}.md"


def summary_pk(user_id: str) -> str:
    """Return the DynamoDB partition key for a user's summaries."""
    return f"USER#{user_id}"


def summary_sk(transcript_id: str) -> str:
    """Return the DynamoDB sort key for a single summary item."""
    return f"SUMMARY#{transcript_id}"


class TranscriptNotFoundError(Exception):
    """Raised when a transcript cannot be read for the given owner.

    Handlers translate this into a 404 so cross-user access cannot be
    distinguished from a genuine missing-resource case.
    """


class SummaryStorage:
    """Reads and writes for transcript text, summary markdown, and metadata.

    boto3 clients are constructed lazily so tests can swap in moto's
    mock implementations by activating `moto.mock_aws()` before the
    instance is built.
    """

    def __init__(
        self,
        *,
        transcripts_bucket: str,
        summaries_bucket: str,
        summaries_table: str,
        region_name: str = "us-east-1",
    ) -> None:
        """Bind to the configured S3 buckets and DynamoDB table."""
        self._transcripts_bucket = transcripts_bucket
        self._summaries_bucket = summaries_bucket
        self._summaries_table_name = summaries_table
        self._region_name = region_name
        self._s3: S3Client = boto3.client("s3", region_name=region_name)
        self._ddb_resource = boto3.resource("dynamodb", region_name=region_name)
        self._table: Table = self._ddb_resource.Table(summaries_table)

    @property
    def transcripts_bucket(self) -> str:
        """The S3 bucket storing transcript text files."""
        return self._transcripts_bucket

    @property
    def summaries_bucket(self) -> str:
        """The S3 bucket storing summary markdown files."""
        return self._summaries_bucket

    @property
    def summaries_table(self) -> str:
        """The DynamoDB table storing summary metadata."""
        return self._summaries_table_name

    def read_transcript(self, user_id: str, transcript_id: str) -> str:
        """Return the transcript text for `user_id`/`transcript_id`.

        Raises `TranscriptNotFoundError` if the object is missing. The
        404 mapping happens in the handler so this layer stays
        framework-agnostic.
        """
        key = transcript_key(user_id, transcript_id)
        try:
            response = self._s3.get_object(Bucket=self._transcripts_bucket, Key=key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in {"NoSuchKey", "404", "NoSuchBucket"}:
                raise TranscriptNotFoundError(transcript_id) from exc
            raise
        body = response["Body"].read()
        return body.decode("utf-8")

    def write_summary(
        self,
        *,
        user_id: str,
        transcript_id: str,
        summary: str,
        tier: Tier,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> SummaryRecord:
        """Persist a summary to S3 and write its metadata to DynamoDB."""
        key = summary_key(user_id, transcript_id)
        created_at = datetime.now(UTC)
        self._s3.put_object(
            Bucket=self._summaries_bucket,
            Key=key,
            Body=summary.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
        record = SummaryRecord(
            transcript_id=transcript_id,
            user_id=user_id,
            tier=tier,
            model=model,
            summary=summary,
            s3_key=key,
            created_at=created_at,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        item: dict[str, Any] = {
            "pk": summary_pk(user_id),
            "sk": summary_sk(transcript_id),
            "transcript_id": transcript_id,
            "user_id": user_id,
            "tier": tier,
            "model": model,
            "s3_key": key,
            "created_at": created_at.isoformat(),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        self._table.put_item(Item=item)
        return record

    def get_summary(self, user_id: str, transcript_id: str) -> SummaryRecord | None:
        """Return a single summary record for the owner, or `None` if missing."""
        response = self._table.get_item(
            Key={"pk": summary_pk(user_id), "sk": summary_sk(transcript_id)}
        )
        item = response.get("Item")
        if item is None:
            return None
        return self._record_from_item(item)

    def list_summaries(
        self,
        user_id: str,
        *,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[SummaryRecord], str | None]:
        """Return a page of summaries owned by `user_id`, plus a continuation cursor."""
        query_kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": {":pk": summary_pk(user_id)},
            "Limit": limit,
        }
        if cursor is not None:
            query_kwargs["ExclusiveStartKey"] = {
                "pk": summary_pk(user_id),
                "sk": cursor,
            }
        response = self._table.query(**query_kwargs)
        records: list[SummaryRecord] = []
        # Items are dicts whose `summary` attribute is omitted on the list page so
        # we read the markdown lazily; for now we hydrate from S3 to keep the API
        # response self-contained for the v0.1 client.
        for item in response.get("Items", []):
            records.append(self._record_from_item(item, include_summary=True))
        last_key = response.get("LastEvaluatedKey")
        next_cursor: str | None = str(last_key["sk"]) if last_key else None
        return records, next_cursor

    def _record_from_item(
        self,
        item: dict[str, Any],
        *,
        include_summary: bool = True,
    ) -> SummaryRecord:
        """Translate a DynamoDB item into a `SummaryRecord`.

        Hydrates the markdown body from S3 when requested. Keeping this
        in one place means tests and handlers see the same shape.
        """
        s3_key = str(item["s3_key"])
        summary_text = ""
        if include_summary:
            obj = self._s3.get_object(Bucket=self._summaries_bucket, Key=s3_key)
            summary_text = obj["Body"].read().decode("utf-8")
        tier_value = str(item["tier"])
        if tier_value not in {"haiku", "sonnet"}:  # pragma: no cover  # defensive: writes are gated
            raise ValueError(f"unexpected tier value: {tier_value!r}")
        return SummaryRecord(
            transcript_id=str(item["transcript_id"]),
            user_id=str(item["user_id"]),
            tier=tier_value,  # type: ignore[arg-type]
            model=str(item["model"]),
            summary=summary_text,
            s3_key=s3_key,
            created_at=datetime.fromisoformat(str(item["created_at"])),
            input_tokens=int(item.get("input_tokens", 0)),
            output_tokens=int(item.get("output_tokens", 0)),
        )
