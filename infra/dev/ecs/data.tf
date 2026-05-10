# Account and region context. Used to construct explicit ARNs for the
# ECR image URI, the IAM policy resources, and the CloudWatch log group
# encryption-context conditions.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Network module remote state
#
# Provides the VPC ID, the private subnet IDs the NLB and the ECS
# tasks attach to, and the VPC CIDR block used by the auth task SG's
# egress rule.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/network/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# IAM module remote state
#
# Provides the per-service task role ARN (`task_role_arns["auth"]`)
# and the ECS task execution role ARN (`execution_role_arns["auth"]`).
# The execution role already has the AmazonECSTaskExecutionRolePolicy
# attached (image pull, log write) plus a least-privilege Secrets
# Manager `GetSecretValue` policy scoped to `jwt-signing-secret` and
# `database-url` (see infra/dev/iam/main.tf execution_secret_arns).
# This module does NOT define any IAM roles; it consumes them.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "iam" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/iam/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# Observability module remote state
#
# Provides the per-service CloudWatch log group ARN map. The auth log
# group already exists at `/panakoes/dev/auth` (provisioned by
# observability) and is encrypted with the shared logs CMK. We
# reference the existing log group rather than creating a new one
# here; that keeps log-group ownership with the observability module
# (single source of truth for retention, encryption, metric filters).
#
# Key point on KMS: the observability CMK conditions encrypt rights on
# log-group ARNs matching `/panakoes/dev/*`; `/panakoes/dev/auth`
# matches that pattern, so the auth task's awslogs driver writes
# successfully. This is unlike the Lambda case (PR #189, PR #192)
# where `/aws/lambda/*` does NOT match the observability key's
# condition and each Lambda module had to provision its own CMK. ECS
# tasks land in `/panakoes/dev/<service>` and inherit the shared key
# correctly.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "observability" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/observability/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# Secrets module remote state
#
# Provides ARNs for the JWT signing secret, the database URL secret,
# and the auth-db password secret. The auth task definition references
# these ARNs in its `secrets` block; ECS resolves them at task start
# and injects the plaintext values as environment variables before the
# Node.js process boots.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "secrets" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/secrets/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# auth-db module remote state
#
# Provides the Aurora cluster's security group ID. The auth task SG
# does NOT need to reference the cluster endpoint here (that lives in
# the database-url secret); the SG-to-SG rule (auth task SG -> Aurora
# SG on 5432) is owned by the auth-db module today (open to the VPC
# CIDR), and a future tightening pass will replace the CIDR rule with
# a security-group reference using the `auth_task_sg_id` output we
# expose below.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "auth_db" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/auth-db/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# api-gateway module remote state
#
# Provides the VPC Link's security group ID. The auth task SG allows
# inbound on the container port from this SG only; nothing else in
# the VPC can reach the auth tasks directly. This is the dual side of
# api-gateway/outputs.tf's `vpc_link_security_group_id` output and
# enforces the contract that all public traffic enters through the
# API Gateway VPC Link.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "api_gateway" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/api-gateway/terraform.tfstate"
    region = "us-east-1"
  }
}
