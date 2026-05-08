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
- (TBD)       Additional per-environment configurations (staging,
              prod, ECS, RDS, Lambda, Batch GPU) land here in
              subsequent commits.

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
