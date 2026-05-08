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
- (TBD)       Additional per-environment configurations (staging,
              prod, ECS, RDS, Lambda, Batch GPU) land here in
              subsequent commits.

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
