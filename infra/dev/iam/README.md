# Dev Environment IAM

Per-environment Terraform configuration creating the least-privilege
IAM roles every Panakoes microservice runs as in the `dev`
environment. State lives at `dev/iam/terraform.tfstate` in the shared
S3 backend created by `infra/bootstrap/`.

## What this creates

Three classes of IAM roles, all tagged `Project=panakoes`,
`Environment=dev`, `ManagedBy=terraform`, `Module=iam`:

1. **Task roles** (one per service, eleven total). The runtime
   identity application code assumes. Naming:
   `panakoes-dev-<service>-task`. Each role's inline policy is
   scoped to the exact actions the service performs against the
   exact resources it touches.
2. **ECS task execution roles** (one per ECS service, seven total).
   The identity the ECS agent itself uses to pull container images
   from ECR, ship logs to CloudWatch, and inject Secrets Manager
   values into the task's environment variables at startup. Naming:
   `panakoes-dev-<service>-execution`.
3. **GPU instance role + instance profile**. The role attached to
   the EC2 GPU instances `gpu-spawner` launches. Defined here so
   `iam:PassRole` on `gpu-spawner` can target a specific role ARN
   instead of `*`.

## Least-privilege approach

Every policy in this module follows three rules:

1. **Resource ARNs are explicit.** No `Resource = "*"` except where
   the AWS API itself has no resource-level authorization
   (`cloudwatch:PutMetricData`, `ec2:Describe*`, `ses:SendEmail`).
   Where the AWS API forces `*`, we tighten with condition keys
   instead (the cloudwatch namespace condition, the SES
   `FromAddress` condition, IAM `PassedToService`, EC2 tag-on-create
   conditions).
2. **Action lists are minimal.** Each policy lists only the
   operations the service actually performs. `query-api` is the
   clearest example: it gets `dynamodb:Query`, `dynamodb:GetItem`,
   `dynamodb:BatchGetItem` and nothing else, even though it shares
   the table with services that write.
3. **Conditions tighten where possible.** `ec2:RunInstances`
   requires `aws:RequestTag/Project=panakoes` and a specific
   `Spawner` tag, plus a constrained `ec2:InstanceType`.
   `ec2:TerminateInstances` mirrors with `ec2:ResourceTag`. Secrets
   Manager grants are per-secret, not per-service.
   `cloudwatch:PutMetricData` is restricted to the
   `panakoes/transcribe` namespace.

The split between task and execution role is the same defense-in-
depth: a compromise of application code yields the task role, not
the agent's image-pull and log-write privileges. The execution role
holds only what ECS itself needs at task startup.

## Service-by-service map

| Service              | Trust principal             | Key permissions |
|----------------------|-----------------------------|-----------------|
| `ingestion-api`      | `ecs-tasks.amazonaws.com`   | S3 PutObject (audio-uploads, `audio/` prefix), DDB Put/Get/Query on ingestion + GSIs, KMS Encrypt/Decrypt on audio CMK, Secrets Manager (jwt-signing, database-url), audit-log Put |
| `summarization`      | `ecs-tasks.amazonaws.com`   | S3 GetObject (transcripts), S3 PutObject (transcripts/`summaries/`), DDB Put/Update on summaries, KMS Encrypt/Decrypt on transcripts CMK, Secrets Manager (anthropic-api-key, jwt-signing), audit-log Put |
| `notification`       | `ecs-tasks.amazonaws.com`   | SES SendEmail (gated by `ses:FromAddress` to the verified domain), DDB Put/Get/Query/Update on notifications, Secrets Manager (jwt-signing, ses-smtp), audit-log Put |
| `query-api`          | `ecs-tasks.amazonaws.com`   | DDB Query/Get/BatchGet on ingestion + summaries + streaming-sessions (READ-ONLY), Secrets Manager (jwt-signing), audit-log Put |
| `auth`               | `ecs-tasks.amazonaws.com`   | audit-log Put. Postgres access is via VPC + security groups, not IAM. Secrets Manager (database-url, jwt-signing) is loaded at task startup by the execution role, not the task role. |
| `session-manager`    | `ecs-tasks.amazonaws.com`   | DDB Put/Get/Update/Delete/Query on streaming-sessions (full CRUD), Secrets Manager (jwt-signing), audit-log Put |
| `billing`            | `ecs-tasks.amazonaws.com`   | DDB Put/Get/Query/Update on `panakoes-dev-billing-events` (forward-referenced; table TBD), Secrets Manager (stripe-test-key, stripe-webhook-signing-secret, jwt-signing), audit-log Put |
| `gpu-spawner`        | `lambda.amazonaws.com`      | EC2 RunInstances (gated to instance type + `Project`/`Spawner` tags), TerminateInstances (only `Spawner=panakoes-dev-gpu-spawner` tagged), iam:PassRole on the gpu-instance role only, Secrets Manager (jwt-signing) |
| `transcriber-batch`  | `lambda.amazonaws.com`      | S3 GetObject (audio-uploads, `audio/` prefix), S3 PutObject (transcripts), DDB UpdateItem on ingestion, KMS Decrypt audio + Encrypt transcripts |
| `transcriber-stream` | `ec2.amazonaws.com`         | S3 PutObject (transcripts), DDB UpdateItem on streaming-sessions, CloudWatch PutMetricData scoped to `panakoes/transcribe` namespace, KMS Encrypt transcripts |
| `event-router`       | `lambda.amazonaws.com`      | S3 GetObject (audio-uploads), DDB UpdateItem on ingestion, EventBridge PutEvents on the project bus, Lambda Invoke on the pipeline-target Lambdas, KMS Decrypt audio |

The "execution role" column is not in the table because every ECS
service's execution role is the same shape: AWS-managed
`AmazonECSTaskExecutionRolePolicy` plus an inline
`secretsmanager:GetSecretValue` scoped to the secrets that service
references at task startup.

## Forward references

A few resources do not exist yet but will land in subsequent slices.
We construct ARNs explicitly for them so the policies stay tight the
moment the resource is created, with no broader `Resource = "*"`
gap in the meantime:

- `panakoes-dev-summaries` DynamoDB table (consumed by `query-api`
  and written by `summarization`).
- `panakoes-dev-notifications` DynamoDB table (consumed by
  `notification`).
- `panakoes-dev-billing-events` DynamoDB table (consumed by
  `billing`).
- `panakoes-<env>` EventBridge bus (consumed by `event-router`).
- `panakoes-<env>-summarization` / `-notification` /
  `-transcriber-batch-trigger` Lambdas (invoke targets for
  `event-router`).
- Secrets Manager secrets at `panakoes/<env>/<name>` for every
  secret referenced (jwt-signing, database-url, anthropic-api-key,
  ses-smtp, stripe-test-key, stripe-webhook-signing-secret). When
  `infra/dev/secrets/` is added, the `local.secret_arns` map in
  `data.tf` collapses into a `terraform_remote_state` reference and
  the policies stay identical.

## Apply

    cd infra/dev/iam
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS provider and initializes the S3
backend. The `dev/storage` and `dev/data` configurations must
already be applied; this module reads their state outputs for the
S3 bucket ARNs, KMS key ARNs, and DynamoDB table ARNs it grants
access to.

## Consuming outputs from other configs

Downstream service Terraform configurations (ECS task definitions,
Lambda function definitions) read these role ARNs via a
`terraform_remote_state` data source pointing at this config:

    data "terraform_remote_state" "iam" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/iam/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # ECS task definition example:
    resource "aws_ecs_task_definition" "ingestion_api" {
      family                = "panakoes-dev-ingestion-api"
      task_role_arn         = data.terraform_remote_state.iam.outputs.task_role_arns["ingestion-api"]
      execution_role_arn    = data.terraform_remote_state.iam.outputs.execution_role_arns["ingestion-api"]
      # ... container definitions, network mode, etc.
    }

    # Lambda function example (gpu-spawner):
    resource "aws_lambda_function" "gpu_spawner" {
      function_name = "panakoes-dev-gpu-spawner"
      role          = data.terraform_remote_state.iam.outputs.task_role_arns["gpu-spawner"]
      # ... handler, runtime, etc.
    }

## Outputs

| Output                          | Type           | Purpose                                                                |
|---------------------------------|----------------|------------------------------------------------------------------------|
| `task_role_arns`                | `map(string)`  | Service name -> task role ARN                                          |
| `task_role_names`               | `map(string)`  | Service name -> task role name                                         |
| `execution_role_arns`           | `map(string)`  | ECS service name -> execution role ARN                                 |
| `execution_role_names`          | `map(string)`  | ECS service name -> execution role name                                |
| `gpu_instance_role_arn`         | `string`       | ARN of the IAM role on GPU EC2 instances                               |
| `gpu_instance_profile_name`     | `string`       | Name of the GPU instance profile                                       |
| `gpu_instance_profile_arn`      | `string`       | ARN of the GPU instance profile                                        |
| `assume_role_policies_summary`  | `map(string)`  | Service name -> trust principal (audit aid)                            |
