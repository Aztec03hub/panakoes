# services/event-router

AWS Lambda function that processes S3 `ObjectCreated:*` notifications on the audio-uploads bucket and routes them downstream into the Panakoes transcription pipeline.

## Handler

**Input event:** S3 ObjectCreated event, accepted in either of two shapes:
- Raw S3 notification (`{"Records": [{"s3": {...}}]}`).
- EventBridge wrapper with the same `s3` payload nested under `detail`.

For each record the handler:

1. Parses the object key `audio/{user_id}/{ingestion_id}/{filename}` into ids.
2. Conditionally flips the matching `IngestionRecord` in `panakoes-dev-ingestion` from `status=pending` to `status=uploaded`. The conditional update makes the Lambda idempotent: re-delivery of the same S3 event is a no-op.
3. Publishes a custom EventBridge event so downstream consumers (Step Functions fan-out, AWS Batch transcription job, Session Manager) can pick the upload up:

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

**Output:** `{"statusCode": 200, "processed": <n>}` where `n` is the number of records routed.

**Failure modes:**

| Condition | Behavior |
|---|---|
| Object key does not parse to `audio/u/i/file` | Skip; emit `unknown_record` metric (`Reason=unparseable_key`). |
| DDB row not found | Skip; emit `unknown_record` metric (`Reason=record_missing`). |
| DDB row not in `pending` (already uploaded) | Skip silently; idempotent re-delivery. |
| `ConditionalCheckFailedException` from a non-routing reason | Logged + skipped per branch above. |
| Any other DDB / EventBridge / IAM error | Raised so Lambda retry / DLQ kicks in. |
| CloudWatch metric publication fails | Logged and swallowed. The metric is observability, not correctness. |

## Environment variables

| Variable | Required / Default | Description |
|---|---|---|
| `DDB_INGESTION_TABLE` | required | DynamoDB table holding ingestion records (Terraform-managed). |
| `EVENTBRIDGE_BUS_NAME` | required | Custom EventBridge bus the routed event publishes to. |
| `AWS_REGION` | auto | Set by the Lambda runtime. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | no | OTLP/gRPC collector endpoint; defaults to `http://localhost:4317` (ADOT in prod). |
| `OTEL_SDK_DISABLED` | no | Set to `true` in tests + offline dev to wire NoOp providers. |
| `SERVICE_VERSION` | no | Stamped onto the `service.version` resource attribute; defaults to `0.0.0`. |
| `DEPLOYMENT_ENVIRONMENT` | no | Stamped onto the `deployment.environment` resource attribute; defaults to `dev`. |

`load_settings()` raises if either required variable is missing.

## Local development

Lambdas do not run a local server. Test with:

```bash
uv sync --group dev
uv run pytest
uv run ruff check
uv run mypy src
```

Integration tests use `moto`'s `mock_aws` to stub DynamoDB + EventBridge + CloudWatch; no live AWS required.

## Deployment

The build context is the repo root because we COPY the sibling `services/audit-lib` path-dep into the image:

```bash
cd /path/to/panakoes
docker build \
    -f services/event-router/Dockerfile \
    -t panakoes-event-router .
```

The image follows the AWS Lambda container-image convention (`public.ecr.aws/lambda/python:3.12` base), so deploying is `docker push <ecr-uri>` followed by Terraform updating the function's `image_uri`.

**IAM dependencies** (Terraform-managed): the function role needs `dynamodb:UpdateItem` on `DDB_INGESTION_TABLE`, `events:PutEvents` on `EVENTBRIDGE_BUS_NAME`, and `cloudwatch:PutMetricData` for the `unknown_record` observability metric. S3 read access is not required; the handler only consumes the notification payload.

## Idempotency

Re-delivery of the same S3 event is a no-op by design. The DynamoDB update is conditional on `status = "pending"`, so the second arrival hits `ConditionalCheckFailedException` and exits via the "already uploaded" branch without republishing the EventBridge event. This means S3's at-least-once delivery semantics never produce duplicate downstream work, and the Lambda's own retry policy (on transient AWS errors) is safe to leave at the default 2 retries.
