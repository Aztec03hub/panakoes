# Async Transcription Smoke Test

## Purpose

End-to-end validation that the async transcription path (S3 audio upload, EventBridge fan-out, SQS trigger, transcribe-worker Lambda, OR the GPU Batch fallback for long-form audio) actually transcribes a file and writes a transcript artifact. Run this after any change to `infra/dev/batch/`, `infra/dev/transcribe-worker/`, `services/transcribe-worker/`, or the gpu-transcribe AMI in `infra/ami/gpu-transcribe/`.

## When to use this runbook

- After deploying or rotating the GPU AMI used by AWS Batch (`infra/dev/batch/variables.tf`, `gpu_ami_id`).
- After cutting a new image for the transcribe-worker Lambda or the (future) transcriber-batch GPU container.
- After any IAM change on the `transcriber-batch` task role, the `transcribe-worker-task` role, the audio-uploads bucket policy, or the transcripts bucket policy.
- After any change to the SQS trigger queue, EventBridge rule, or S3 `aws_s3_bucket_notification` on the audio-uploads bucket.
- On a quarterly schedule to confirm the cold path still warms.

## Prerequisites

- `aws` CLI configured against the `panakoes-admin` profile (`aws configure list --profile panakoes-admin` returns the dev account `659225405128`).
- Local read access to the dev audio-uploads bucket and write access to PutObject under `s3://panakoes-dev-audio-uploads-<suffix>/audio/`.
- `ffmpeg` installed locally for synthesizing the 10-second test WAV. Verify: `ffmpeg -version | head -1`.
- Knowledge of which path you are smoking: Lambda (Groq backend, default for files under the long-audio cutoff) or AWS Batch (GPU Whisper, fallback for >10min files).

## Current path status (2026-05-11)

| Component | Resource | State |
|---|---|---|
| Lambda function | `panakoes-dev-transcribe-worker` | DEPLOYED, Image package, role `panakoes-dev-transcribe-worker-task`, env `TRANSCRIBER_BACKEND=groq`. |
| Lambda trigger | EventBridge S3 ObjectCreated -> SQS `panakoes-dev-transcribe-trigger` -> Lambda | WIRED via `infra/dev/transcribe-worker/`. |
| Batch compute env | `panakoes-dev-transcribe` (g4dn.xlarge Spot, max 16 vCPU) | ENABLED + VALID + `ComputeEnvironment Healthy`. AMI currently `ami-091f07e77f51e6b42` (stock AL2023 NVIDIA) NOT the pinned bespoke AMI from PR #237 (`ami-0dee04ee5042c94cf`). See "Pending: roll Batch CE to bespoke GPU AMI" below. |
| Batch job queue | `panakoes-dev-transcribe-queue` | ENABLED + VALID. |
| Batch job definition | `panakoes-dev-transcribe-batch:1` | ACTIVE. Image URI `659225405128.dkr.ecr.us-east-1.amazonaws.com/panakoes-dev-transcriber-batch:latest`. |
| Batch container image | `panakoes-dev-transcriber-batch:latest` in ECR | NOT PUSHED. Repository exists but holds zero images. The `services/transcriber-batch/` source directory does not exist in the monorepo; the Whisper GPU container source code is a follow-up. |
| GPU bake workflow | `.github/workflows/image-bake-on-change.yml` (PR #268) | NOT MERGED at 2026-05-11. Use local buildx only as a last resort and DO NOT push the `:latest` tag until the bake workflow is the source of truth. |

## Decision flowchart

```
Need to smoke?
  |
  +-- Just the Lambda (Groq, files <= 10min): GO TO Lane A.
  |
  +-- The GPU Batch path (Whisper, files > 10min):
        |
        +-- Is transcriber-batch image in ECR?
              |
              +-- NO:  STOP. Document the blocker and link this runbook.
              |        The smoke is not runnable until the image source ships
              |        AND PR #268's bake workflow merges. Re-run when both clear.
              |
              +-- YES: GO TO Lane B.
```

## Lane A: Lambda smoke (Groq backend)

### A.1 Synthesize a 10-second mono PCM WAV

```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=10" -ac 1 -ar 16000 /tmp/smoke-10s.wav
```

Expected: a ~320KB WAV at `/tmp/smoke-10s.wav`.

### A.2 Upload to the audio-uploads bucket under the `audio/` prefix

```bash
SMOKE_KEY="audio/smoke/$(date -u +%Y%m%dT%H%M%SZ)-10s.wav"
AUDIO_BUCKET=$(aws --profile panakoes-admin --region us-east-1 \
  lambda get-function --function-name panakoes-dev-transcribe-worker \
  --query 'Configuration.Environment.Variables.AUDIO_UPLOADS_BUCKET' --output text)
aws --profile panakoes-admin --region us-east-1 s3 cp /tmp/smoke-10s.wav \
  "s3://${AUDIO_BUCKET}/${SMOKE_KEY}"
echo "Uploaded to s3://${AUDIO_BUCKET}/${SMOKE_KEY}"
```

The EventBridge rule narrowed to `prefix: audio/` fires within ~1s, lands a single message on the trigger SQS, and the Lambda event-source mapping invokes within seconds (batch_size=1, no batching window).

### A.3 Tail the Lambda log group

```bash
aws --profile panakoes-admin --region us-east-1 logs tail \
  /aws/lambda/panakoes-dev-transcribe-worker --since 5m --follow
```

Look for a structured log line referencing `transcribe_ingestion` and the upload key. Expected first-receive completion: under 30s for a 10s audio clip on Groq's API.

### A.4 Verify the DynamoDB ingestion record was updated

The worker calls `transcribe_ingestion` which writes `transcript_status` and `transcript_text` (or `transcript_url`) onto the ingestion row keyed by upload id. Expected end-state: `transcript_status = completed`. If `transcript_status = failed`, capture `failure_reason` and feed back into `incident-response.md`.

```bash
aws --profile panakoes-admin --region us-east-1 dynamodb scan \
  --table-name panakoes-dev-ingestion \
  --filter-expression "begins_with(upload_key, :prefix)" \
  --expression-attribute-values "{\":prefix\":{\"S\":\"${SMOKE_KEY}\"}}" \
  --max-items 5
```

### A.5 Confirm DLQ stayed empty

```bash
aws --profile panakoes-admin --region us-east-1 sqs get-queue-attributes \
  --queue-url $(aws --profile panakoes-admin --region us-east-1 sqs get-queue-url \
    --queue-name panakoes-dev-transcribe-trigger-dlq --query QueueUrl --output text) \
  --attribute-names ApproximateNumberOfMessages
```

Expected: `0`. Any non-zero means a poison message survived 3 receives; investigate before declaring the smoke green.

### A.6 Cleanup

```bash
aws --profile panakoes-admin --region us-east-1 s3 rm "s3://${AUDIO_BUCKET}/${SMOKE_KEY}"
```

The ingestion DDB row is left in place for audit; it is small and self-prunes by TTL.

## Lane B: AWS Batch GPU smoke (Whisper)

### B.1 Sanity-check the job definition image actually exists

```bash
aws --profile panakoes-admin --region us-east-1 ecr describe-images \
  --repository-name panakoes-dev-transcriber-batch \
  --query 'imageDetails[?contains(imageTags, `latest`)].[imageDigest,imagePushedAt]' \
  --output table
```

Empty output means STOP. Refer to "Current path status" above.

### B.2 Stage a 10-second test audio object in the audio bucket

Same `ffmpeg` synth as Lane A.1 and same upload as A.2.

### B.3 Submit a single Batch job

```bash
JOB_NAME="smoke-$(date -u +%Y%m%dT%H%M%SZ)"
aws --profile panakoes-admin --region us-east-1 batch submit-job \
  --job-name "${JOB_NAME}" \
  --job-queue panakoes-dev-transcribe-queue \
  --job-definition panakoes-dev-transcribe-batch \
  --container-overrides "environment=[{name=INPUT_KEY,value=${SMOKE_KEY}}]" \
  --query '[jobId,jobName]' --output table
```

### B.4 Watch the job through its states

`SUBMITTED -> PENDING -> RUNNABLE -> STARTING -> RUNNING -> SUCCEEDED`.

```bash
JOB_ID="<from B.3>"
watch -n 15 "aws --profile panakoes-admin --region us-east-1 batch describe-jobs \
  --jobs ${JOB_ID} --query 'jobs[0].[status,statusReason,startedAt,stoppedAt]'"
```

Expected first-job wallclock budget: 5-15 minutes of `RUNNABLE` (Spot acquisition + AMI boot + container pull + Whisper warm-up) then ~30-90 seconds of `RUNNING` for a 10-second clip. Total: under 20 minutes typical, 30 minutes pessimistic.

### B.5 PendingVerification fall-through

If the first GPU Spot launch in a fresh region trips `aws_pending_verification_first_ec2_launch` (per memory `aws_pending_verification_first_ec2_launch.md`), the job sits in `RUNNABLE` indefinitely with a recent failed `RunInstances` call visible in CloudTrail. CloudTrail query:

```bash
aws --profile panakoes-admin --region us-east-1 cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=RunInstances \
  --max-results 10 --query 'Events[].{Time:EventTime,User:Username,Err:ErrorCode}'
```

Look for `ErrorCode = PendingVerification`. Recovery window per memory: minutes to ~4 hours, no operator action required. Capture the timestamp here in the run report, terminate the smoke job (`aws batch terminate-job --job-id ${JOB_ID} --reason 'pending-verification, will re-run'`), and re-run this lane after the window passes.

### B.6 Verify the transcript landed in S3

The transcriber-batch container is contracted to write to `s3://panakoes-dev-transcripts-<suffix>/<input-key>.json`. Confirm:

```bash
TRANSCRIPTS_BUCKET=$(aws --profile panakoes-admin --region us-east-1 s3api list-buckets \
  --query 'Buckets[?starts_with(Name,`panakoes-dev-transcripts`)].Name | [0]' --output text)
aws --profile panakoes-admin --region us-east-1 s3 ls \
  "s3://${TRANSCRIPTS_BUCKET}/${SMOKE_KEY}" --recursive
```

### B.7 Confirm CloudWatch metric

The `panakoes-dev-batch-failed-jobs` alarm should be `OK`. Any `ALARM` state means a job failed in the 5-minute window; investigate via `/aws/batch/panakoes-dev-transcribe`.

### B.8 Cleanup

```bash
aws --profile panakoes-admin --region us-east-1 s3 rm "s3://${AUDIO_BUCKET}/${SMOKE_KEY}"
aws --profile panakoes-admin --region us-east-1 s3 rm "s3://${TRANSCRIPTS_BUCKET}/${SMOKE_KEY}.json"
```

Compute environment scales back to 0 vCPU within ~5 minutes of the last job ending. No idle spend expected. Verify:

```bash
aws --profile panakoes-admin --region us-east-1 batch describe-compute-environments \
  --compute-environments panakoes-dev-transcribe \
  --query 'computeEnvironments[0].computeResources.desiredvCpus'
```

Should return to `0`.

## Pending: roll Batch CE to bespoke GPU AMI

The compute environment currently runs the stock AL2023 NVIDIA AMI (`ami-091f07e77f51e6b42`), not the bespoke `ami-0dee04ee5042c94cf` baked in PR #237 and pinned in `infra/dev/batch/variables.tf`. A naive `terraform apply` on `infra/dev/batch/` fails because:

```
ClientException: Cannot delete, found existing JobQueue relationship.
```

Terraform tries to replace the compute environment (image_id change forces replacement) while the queue still references it. Recovery procedure:

1. Drain the queue: confirm no jobs are RUNNING or RUNNABLE (`aws batch list-jobs --job-queue panakoes-dev-transcribe-queue --job-status RUNNING`).
2. Disable the queue: `aws batch update-job-queue --job-queue panakoes-dev-transcribe-queue --state DISABLED`.
3. Detach the CE from the queue via console or API by removing the `compute_environment_order` entry, OR refactor the CE resource to use `name_prefix` + `lifecycle { create_before_destroy = true }` so Terraform builds the new CE before destroying the old one and the queue swap is in-flight.
4. Apply the Terraform change.
5. Re-enable the queue.

Tracked as a follow-up: refactor `infra/dev/batch/main.tf` to use `name_prefix` on the compute environment so future AMI rolls are zero-downtime. The bake workflow in PR #268 will make this much more frequent.

## Verification

The smoke is GREEN if and only if all of:

- The S3 PutObject succeeded.
- The corresponding ingestion DDB row reaches `transcript_status = completed` (Lane A) OR a transcript JSON appears under the transcripts bucket (Lane B).
- The trigger DLQ depth is 0.
- The `panakoes-dev-batch-failed-jobs` alarm is `OK` (Lane B only).
- Lambda concurrency executions did not spike above 1 (Lane A) and Batch desired_vcpus returned to 0 within 5 minutes (Lane B).

## Rollback

Smoke is a read-mostly verification path; the only side effects are:

- An audio object under `s3://panakoes-dev-audio-uploads-*/audio/smoke/*` (delete in step A.6 / B.8).
- An ingestion DDB row (leave for audit; TTL will prune).
- A transcript JSON under `s3://panakoes-dev-transcripts-*/...` (Lane B only; delete in B.8).
- Lambda log lines and Batch log lines (retained per the 30-day log-group policy; no action).
- CloudWatch metrics emitted (no action; metric retention is automatic).

If the smoke surfaced a regression that needs immediate rollback, that is `incident-response.md` territory, not this runbook.

## References

- `infra/dev/batch/` (compute environment, queue, job definition; this module's IAM uses the `transcriber-batch` task role from `infra/dev/iam/`).
- `infra/dev/transcribe-worker/` (SQS trigger queue, EventBridge rule on the audio bucket, Lambda function + event-source mapping, DLQ alarm).
- `services/transcribe-worker/` (Lambda handler source; container image built per `Dockerfile`).
- `docs/runbooks/gpu-ami-bake.md` (AMI bake + rotation procedure that produces the pinned `gpu_ami_id`).
- `docs/runbooks/incident-response.md` (when the smoke fails in a way that suggests production impact).
- PR #237 (first bespoke GPU AMI bake + variable pin).
- PR #268 (image-bake-on-change.yml workflow; not yet merged at 2026-05-11).
- Memory `aws_pending_verification_first_ec2_launch.md` (fresh-account GPU Spot first-launch gate).
- Memory `aws_lambda_container_image_gotchas.md` (Lambda container image build + deploy traps).
