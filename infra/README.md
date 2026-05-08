# infra

Terraform configurations for Panakoes infrastructure.

## Layout

- bootstrap/  One-time setup creating remote state backend (S3 + KMS
              + DynamoDB lock). Apply this first, before anything else.
- global/     Account-wide, region-agnostic resources: GitHub Actions
              OIDC identity provider and the IAM role workflows assume.
              Uses the S3 backend from `bootstrap/`.
- dev/network/  Per-environment networking primitives for the dev
              environment (VPC, subnets, NAT, IGW, route tables, flow
              logs). First config that consumes the bootstrap-created
              S3 backend.
- dev/data/   Per-environment DynamoDB tables for the dev environment
              (ingestion records, audit log, streaming session state).
              All tables PAY_PER_REQUEST, SSE enabled, point-in-time
              recovery, deletion protection.
- dev/storage/  Per-environment S3 buckets for the dev environment:
              audio uploads (client uploads via the Ingestion API),
              transcripts (transcription pipeline output), and the
              long-term log archive. Each bucket has a dedicated CMK,
              versioning, public-access blocked, TLS-only policy, and
              tier-and-expiry lifecycle rules.
- dev/secrets/  Per-environment AWS Secrets Manager secrets for the
              dev environment microservices: JWT signing key,
              Anthropic API key, Stripe test + webhook secrets,
              Postgres password, database URL, and SES SMTP
              credentials. All secrets KMS-encrypted with a
              dedicated dev CMK, created with placeholder values
              (real values written post-apply via the AWS CLI).
- dev/iam/    Per-environment least-privilege IAM roles for every
              Panakoes microservice. Provisions a task role per
              service (runtime identity for application code) and
              an ECS task execution role per ECS service (image pull
              + log shipping + startup-secret injection). Also
              creates the GPU instance role + instance profile that
              gpu-spawner passes to launched EC2 instances. Every
              policy uses explicit Resource ARNs and condition keys
              wherever the AWS API allows.
- dev/ecr/    Per-environment ECR repositories for the dev environment,
              one per Panakoes microservice (11 repos: auth, billing,
              event-router, gpu-spawner, ingestion-api, notification,
              query-api, session-manager, summarization,
              transcriber-batch, transcriber-stream). All repositories
              use `IMMUTABLE` tag mutability, scan-on-push, a single
              shared customer-managed KMS key, and a lifecycle policy
              that keeps the last 10 tagged images and expires
              untagged images after 14 days.
- dev/waf/    Per-environment regional WAFv2 web ACL fronting the
              public-facing Panakoes APIs (Ingestion, Query, Auth)
              once their ALBs / API Gateways exist. ACL composes
              four AWS Managed Rule Groups (Common, Known Bad
              Inputs, IP Reputation, SQL Database), a 1000-req /
              5-min per-IP rate limit (with `/health` exempt via
              scope-down), and a commented geo-block placeholder.
              Logs flow to a dedicated KMS-encrypted CloudWatch log
              group with `Authorization` and `Cookie` headers
              redacted.
- dev/observability/  Per-environment CloudWatch observability
              primitives for the dev environment: dedicated KMS CMK
              `alias/panakoes-dev-logs`, one CloudWatch Log Group per
              service at `/panakoes/dev/<service>` (30-day retention,
              KMS-encrypted), per-service error-count metric filters,
              and a long-term S3 log archive bucket with multi-tier
              lifecycle (STANDARD to IA at 30d, GLACIER_IR at 90d,
              DEEP_ARCHIVE at 365d). Subscription-filter wiring
              (Firehose vs Lambda forwarder) deferred to a follow-up.
- dev/step-functions/  Per-environment Step Functions state machine
              that orchestrates the long-audio transcription
              pipeline. STANDARD-type workflow with a Choice between
              a single-Batch-job short path (audio under 10 minutes)
              and a chunk-and-fan-out long path (parallel Map over
              8-minute overlapping chunks, MaxConcurrency 8). Every
              Task carries an exponential-backoff retry policy and a
              Catch handler that routes to a NotifyFailure Lambda.
              KMS-encrypted CloudWatch log group at
              `/aws/states/panakoes-dev-long-audio`, level=ALL with
              execution data included. Bypasses Lambda's 15-minute
              hard ceiling, which is the architectural reason this
              module exists.
- dev/security/  Per-environment security observability stack for
              the dev environment: AWS Config (recorder + delivery
              channel + three free-tier managed rules), Amazon
              GuardDuty detector, and AWS Security Hub (account +
              AWS Foundational Security Best Practices + CIS AWS
              Foundations standards). Plan-clean by default. Each
              paid service gates behind a `bool` variable
              (`enable_config`, `enable_guardduty`,
              `enable_security_hub`, all defaulting to false) so a
              default `terraform apply` provisions the supporting
              infrastructure (KMS CMK `alias/panakoes-dev-security`,
              S3 delivery bucket with TLS-only policy and tier-to-IA
              at 90d / expire at 1y lifecycle, IAM service role with
              `AWS_ConfigRole` managed policy, idle GuardDuty
              detector) without starting any per-event billing.
              Flipping each variable to true is a deliberate
              post-apply step.
- dev/auth-db/  Per-environment Aurora Serverless v2 PostgreSQL 16
              cluster backing the Better-Auth tables (`user`,
              `session`, `account`, `verification`) for the auth
              microservice. Cluster sits in the three private
              subnets from `dev/network/` via a dedicated DB subnet
              group, scales between 0.5 and 4 ACUs, and is reachable
              on 5432/TCP from inside the dev VPC CIDR. Storage and
              Performance Insights encrypted with a dedicated CMK
              (`alias/panakoes-dev-auth-db`, rotation enabled,
              7-day deletion window). 7-day backup retention,
              deletion protection on, `skip_final_snapshot = true`
              for dev. Master password resolves from
              `panakoes-dev/postgres-auth-db-password` via
              `terraform_remote_state` with a `try()` fallback to a
              Terraform-managed `random_password` so plans run
              clean before secrets is applied; rotated post-apply
              via `aws rds modify-db-cluster`. RDS Enhanced
              Monitoring role (60s granularity) and Performance
              Insights (free tier, 7-day retention) provisioned for
              the writer instance.
- dev/events/   Per-environment async messaging backbone for the dev
              environment: a custom EventBridge bus (`panakoes-dev`),
              three pipeline-stage rules (audio uploaded, transcript
              completed, summary completed), four SQS queues with
              matching DLQs, three SNS fan-out topics (system alerts,
              billing events, user notifications), and CloudWatch
              alarms on every DLQ. Single shared CMK encrypts all
              SNS topics and SQS queues.
- dev/batch/  Per-environment AWS Batch GPU compute environment, job
              queue, and job definition for the async transcription
              pipeline (Whisper-large-v3 fp16 on g4dn.xlarge Spot).
              Compute environment is `MANAGED` with allocation
              `SPOT_CAPACITY_OPTIMIZED`, scales 0/0/16 vCPUs (pure
              pay-per-use), and runs in the dev VPC's private
              subnets. Job definition pins 4 vCPU / 15000 MiB /
              1 GPU and uses the `transcriber-batch` task role from
              `dev/iam/`. Owns the project-wide `system-alerts` SNS
              topic and a CloudWatch alarm on `FailedJobs > 0` over
              5 minutes. GPU AMI is a placeholder until the Packer
              build module ships.
- dev/api-gateway/  Per-environment public ingress for every
              Panakoes microservice. Provisions an
              `aws_apigatewayv2_api` (HTTP API named
              `panakoes-dev-public`) with CORS for the production
              marketing domain, the LaFayette Labs site, and the
              local Vite dev server; a shared `aws_apigatewayv2_vpc_link`
              spanning all three private subnets; one
              `aws_apigatewayv2_integration` per upstream service
              (auth, ingestion-api, summarization, notification,
              query-api, session-manager, billing) wired to
              placeholder NLB listener ARNs (real NLBs land with
              ECS); 25 routes plus a public `GET /health` MOCK
              integration; an auto-deploy `dev` stage with
              throttling burst 5000 / rate 10000 and structured-JSON
              access logs to a KMS-encrypted CloudWatch log group;
              optional WAF web ACL association via `try()` against
              `dev/waf`; and three CloudWatch alarms (4xx > 10% over
              10 min, 5xx > 1% over 5 min, integration latency p99
              > 2 s). Custom domain (`api.panakoes.com`) is left as
              a commented-out skeleton until DNS and ACM are wired.
- dev/vpc-endpoints/  Per-environment VPC endpoints for the dev
              environment. Two free gateway endpoints (S3,
              DynamoDB) attach to all private route tables. Ten
              paid interface endpoints (secretsmanager, ssm, kms,
              ecr.api, ecr.dkr, logs, events, sns, sqs, sts) attach
              to all three private subnets with private DNS
              enabled. A shared security group fronts the
              interface endpoints, allowing 443/TCP from the VPC
              CIDR. Routes AWS API traffic off the NAT to cut
              egress costs and keeps service traffic inside the
              VPC.
- (TBD)       Additional per-environment configurations (staging,
              prod, ECS, RDS, Lambda) land here in subsequent
              commits.

## AMIs

Custom Amazon Machine Images built with Packer rather than Terraform.
Packer is the right tool here: it bakes a long-lived artifact (AMI ID)
that downstream Terraform consumes. Mixing both into one Terraform
config would couple `terraform apply` cadence to AMI rebuilds, which is
the opposite of what we want.

- ami/gpu-transcribe/  Packer template for the GPU AMI used by both
              transcription paths. Source = latest AWS Deep Learning AMI
              GPU on Ubuntu 22.04 (NVIDIA drivers + CUDA + Docker
              pre-installed). Provisioners pre-bake Whisper-large-v3
              fp16 weights, faster-whisper-large CT2 weights, Silero VAD
              weights, and the panakoes-dev-transcriber-stream container
              image. Build host = g4dn.xlarge to mirror runtime
              hardware; bake cost ~$0.20-0.50 per build. Output AMI is
              tagged `Project=panakoes`, `Environment=<env>`,
              `AmiPurpose=gpu-transcribe`, `BakedAt=<UTC timestamp>`.
              See `infra/ami/gpu-transcribe/README.md` for the full
              build + rotation procedure.

## Standard workflow

1. Apply the bootstrap module once per AWS account.
2. Reference the bootstrap outputs in each environment's
   `providers.tf` `backend "s3"` block.
3. Subsequent configurations use the S3 backend with state locking
   via DynamoDB.

## Conventions

- All resources tagged: Project, Environment, ManagedBy, Module.
- All S3 buckets have public access blocked, versioning enabled,
  KMS-encrypted.
- All DynamoDB tables use PAY_PER_REQUEST billing.
- KMS keys have rotation enabled and 30-day deletion window.
