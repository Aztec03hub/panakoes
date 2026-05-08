# dev/batch

AWS Batch GPU compute environment, job queue, and job definition for the
Panakoes async transcription pipeline (Whisper-large-v3 fp16 on
g4dn.xlarge Spot). Per `CLAUDE.md`, async transcription runs on
EC2 Spot via Batch; this module is the infrastructure side of that
decision.

## What this module creates

- `aws_batch_compute_environment.transcribe` (`panakoes-dev-transcribe`)
  - Type `MANAGED`, allocation `SPOT_CAPACITY_OPTIMIZED`
  - Instance type `g4dn.xlarge`
  - vCPUs 0/0/16 (min/desired/max). Scales from zero so an idle queue
    costs nothing.
  - Launches into the dev VPC's three private subnets (read from the
    `dev/network/` remote state).
- `aws_batch_job_queue.transcribe` (`panakoes-dev-transcribe-queue`)
  - Priority 1, single queue.
- `aws_batch_job_definition.transcribe` (`panakoes-dev-transcribe-batch`)
  - Type `container`, vCPU 4, memory 15000 MiB, GPU 1.
  - Image is the `transcriber-batch` ECR repository (`:latest` tag).
  - Job role is the `transcriber-batch` task role from `dev/iam/`.
  - Env vars: `S3_INPUT_BUCKET`, `S3_OUTPUT_BUCKET`,
    `MODEL_PATH=/opt/whisper/models/large-v3.pt`.
- IAM:
  - `panakoes-dev-batch-service` (Batch service role,
    `AWSBatchServiceRole`)
  - `panakoes-dev-batch-spot-fleet` (Spot Fleet tagging role,
    `AmazonEC2SpotFleetTaggingRole`)
- `aws_security_group.batch` (no inbound; egress all, NAT-bound)
- `aws_cloudwatch_log_group.batch` (`/aws/batch/panakoes-dev-transcribe`,
  30-day retention)
- `aws_sns_topic.system_alerts` (`panakoes-dev-system-alerts`). Project-wide
  alarm destination; this module owns it because no upstream module does.
- `aws_cloudwatch_metric_alarm.failed_jobs`
  (`panakoes-dev-batch-failed-jobs`): fires when `AWS/Batch FailedJobs`
  on the queue exceeds 0 over a 5-minute window.

## Inputs

| Variable | Default | Notes |
|---|---|---|
| `aws_region` | `us-east-1` | |
| `environment` | `dev` | |
| `project_name` | `panakoes` | |
| `gpu_ami_id` | `ami-PLACEHOLDER` | Replace once the GPU AMI Packer build ships. `terraform apply` is intentionally blocked at the placeholder; Batch rejects an invalid AMI ID at `CreateComputeEnvironment` time. |

## Outputs

| Output | Purpose |
|---|---|
| `compute_env_arn` | Compute environment ARN. |
| `job_queue_arn` | Submit-target ARN for async transcription jobs. |
| `job_def_arn` | Job definition ARN (latest revision). |
| `system_alerts_topic_arn` | Project-wide alarm SNS topic ARN. Future alarm modules should consume from `dev/batch/`. |
| `batch_log_group_name` | CloudWatch log group the awslogs driver targets. |

## Upstream dependencies

- `dev/network/` (VPC, private subnets)
- `dev/iam/` (`transcriber-batch` task role, `gpu_instance_profile_arn`)
- `dev/ecr/` (transcriber-batch repository URL). Wrapped in `try()`;
  if `dev/ecr/` is not yet applied, the module falls back to a
  constructed default URI (`<acct>.dkr.ecr.<region>.amazonaws.com/<repo>:latest`).

## Deferred / follow-ups

- Real GPU AMI: replace `gpu_ami_id` placeholder with the AMI ID
  emitted by the Packer build.
- Image tag pinning: add a `transcriber_batch_image_tag` variable
  wired from the deploy pipeline so we stop pulling `:latest`.
- Email subscription on the `panakoes-dev-system-alerts` SNS topic
  (currently created with no subscribers, so alarms still fire on
  CloudWatch but no one is paged). Wire after the on-call story is
  resolved.
- Per-tier job queues (paid-tier higher priority) once billing slice
  ships.

## Standard workflow

```bash
cd infra/dev/batch
terraform init
terraform plan
terraform apply
```

`terraform validate` and `terraform plan` work today against the
placeholder AMI; `terraform apply` will fail at
`CreateComputeEnvironment` until `gpu_ami_id` points to a real AMI.
