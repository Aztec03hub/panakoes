# services/event-router

AWS Lambda function that processes S3 `ObjectCreated:*` notifications on the
audio-uploads bucket and routes them downstream into the Panakoes
transcription pipeline.

## What this Lambda does

1. Accepts S3 ObjectCreated events (raw S3 notification or EventBridge wrapper).
2. Parses the object key `audio/{user_id}/{ingestion_id}/{filename}` into ids.
3. Conditionally flips the matching `IngestionRecord` in
   `panakoes-dev-ingestion` from `status=pending` to `status=uploaded`.
   The conditional update makes the Lambda idempotent: re-delivery of the
   same S3 event is a no-op.
4. Publishes a custom EventBridge event so downstream consumers (Step
   Functions fan-out, AWS Batch transcription job, Session Manager) can
   pick the upload up:

   ```json
   {
     "Source": "panakoes.ingest",
     "DetailType": "AudioUploaded",
     "Detail": {
       "user_id": "...",
       "ingestion_id": "...",
       "filename": "..."
     }
   }
   ```

5. Returns `{"statusCode": 200, "processed": <n>}`.

## Failure modes

| Condition | Behavior |
|---|---|
| Object key does not parse to `audio/u/i/file` | Skip; emit `unknown_record` metric (`Reason=unparseable_key`). |
| DDB row not found | Skip; emit `unknown_record` metric (`Reason=record_missing`). |
| DDB row not in `pending` (already uploaded) | Skip silently; idempotent re-delivery. |
| `ConditionalCheckFailedException` from a non-routing reason | Logged + skipped per branch above. |
| Any other DDB / EventBridge / IAM error | Raised so Lambda retry / DLQ kicks in. |
| CloudWatch metric publication fails | Logged and swallowed. The metric is observability, not correctness. |

## Environment variables

| Var | Required | Description |
|---|---|---|
| `DDB_INGESTION_TABLE` | yes | DynamoDB table holding ingestion records (Terraform-managed). |
| `EVENTBRIDGE_BUS_NAME` | yes | Custom EventBridge bus the routed event publishes to. |
| `AWS_REGION` | auto | Set by the Lambda runtime. |

`load_settings()` raises if either required variable is missing.

## Running locally

```bash
uv sync --group dev
uv run pytest
```

## Linting and type checking

```bash
uv run ruff check
uv run mypy src
```

## Building the container image

The build context is the repo root because we COPY the sibling
`services/audit-lib` path-dep:

```bash
cd /path/to/panakoes
docker build \
    -f services/event-router/Dockerfile \
    -t panakoes-event-router .
```

The image follows the AWS Lambda container-image convention
(`public.ecr.aws/lambda/python:3.12` base), so deploying is
`docker push <ecr-uri>` followed by Terraform updating the function's
`image_uri`.
