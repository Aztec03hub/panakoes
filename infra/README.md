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
