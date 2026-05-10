# infra/dev/transcribe-worker

Provisions the auto-trigger pipeline that fires transcription on every
audio upload, replacing the on-demand-only `POST /api/v1/transcribe/{id}`
flow as the primary path.

## What it provisions

- **SQS trigger queue** `panakoes-dev-transcribe-trigger` + DLQ
  (KMS-encrypted with a dedicated CMK; visibility 5 min; retention 14 days;
  redrive after 3 receives).
- **EventBridge rule** on the default bus matching S3 ObjectCreated
  events on the audio-uploads bucket under the `audio/` prefix; target
  is the SQS queue.
- **`aws_s3_bucket_notification`** on the audio-uploads bucket enabling
  EventBridge delivery. S3 only allows ONE such resource per bucket;
  this module owns it. If a future module needs another notification it
  must merge here, not declare a parallel resource.
- **Lambda function** `panakoes-dev-transcribe-worker` (container image
  from the `panakoes-dev-transcribe-worker` ECR repo; 512 MB, 5 min
  timeout, reserved concurrency 5).
- **Inline IAM policy** attached to the existing `transcribe-worker`
  task role (declared in `infra/dev/iam/`) granting least-privilege
  SQS receive/delete + S3 GetObject (audio/* only) + KMS Decrypt
  (audio bucket CMK + queue CMK) + DynamoDB UpdateItem on the
  ingestion table + Secrets Manager GetSecretValue on the Groq key.
- **SQS event-source mapping** (batch_size 1, `ReportBatchItemFailures`
  response shape).
- **CloudWatch log group** `/aws/lambda/panakoes-dev-transcribe-worker`
  (30-day retention, encrypted with the shared logs CMK).
- **CloudWatch alarm** on the DLQ (publishes to the system-alerts SNS
  topic from `infra/dev/events/`).

## Operator follow-up after first apply

The Lambda exists in TF state immediately, but errors on every
invocation until:

1. **Build + push the container image** to the ECR repo:
   ```
   cd <repo-root>
   aws ecr get-login-password --region us-east-1 | \
     docker login --username AWS --password-stdin \
     659225405128.dkr.ecr.us-east-1.amazonaws.com
   docker build -f services/transcribe-worker/Dockerfile -t panakoes-dev-transcribe-worker:latest .
   docker tag panakoes-dev-transcribe-worker:latest \
     659225405128.dkr.ecr.us-east-1.amazonaws.com/panakoes-dev-transcribe-worker:latest
   docker push 659225405128.dkr.ecr.us-east-1.amazonaws.com/panakoes-dev-transcribe-worker:latest
   aws lambda update-function-code \
     --function-name panakoes-dev-transcribe-worker \
     --image-uri 659225405128.dkr.ecr.us-east-1.amazonaws.com/panakoes-dev-transcribe-worker:latest
   ```

2. **Populate the Groq API key** in Secrets Manager:
   ```
   aws secretsmanager put-secret-value \
     --secret-id panakoes-dev/groq-api-key \
     --secret-string '<your-groq-key>'
   ```
   (The secret resource itself is owned by `infra/dev/secrets/`. If it
   does not yet exist there, that needs adding in a separate ops PR.)

3. **Inject the key into the Lambda env** (interim path until the
   Secrets Manager Lambda extension is wired):
   ```
   aws lambda update-function-configuration \
     --function-name panakoes-dev-transcribe-worker \
     --environment 'Variables={DDB_INGESTION_TABLE=panakoes-dev-ingestion,AUDIO_UPLOADS_BUCKET=panakoes-dev-audio-uploads-<suffix>,DEPLOYMENT_ENVIRONMENT=dev,TRANSCRIBER_BACKEND=groq,GROQ_API_KEY=<your-groq-key>}'
   ```

## Cross-module dependencies

| Remote state | Used for |
|---|---|
| `infra/dev/storage/` | audio-uploads bucket name + ARN + CMK ARN |
| `infra/dev/data/` | ingestion table ARN + name |
| `infra/dev/ecr/` | (implicit) the `panakoes-dev-transcribe-worker` repo |
| `infra/dev/iam/` | the existing `transcribe-worker` task role |
| `infra/dev/observability/` | shared CloudWatch logs CMK |
| `infra/dev/events/` | system-alerts SNS topic for the DLQ alarm |

## Outputs

See `outputs.tf`. Notable: `lambda_function_name`, `queue_arn`, `dlq_arn`.
