locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "ecr"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # Service list. One ECR repository per service, named
  # `panakoes-dev-<service>`. Order is alphabetical for readability;
  # Terraform's for_each is unordered so this is purely cosmetic.
  services = [
    "admin-api",
    "auth",
    "billing",
    "cost-api",
    "cost-rollup-aggregator",
    "event-router",
    "gpu-spawner",
    "health-aggregator",
    "ingestion-api",
    "notification",
    "query-api",
    "session-manager",
    "streaming-router",
    "summarization",
    "transcribe-worker",
    "transcriber-batch",
    "transcriber-stream",
    "ws-authorizer",
  ]

  # Shared lifecycle policy. ECR evaluates rules in priority order
  # (lowest first). We use two rules:
  #
  #   1. Keep only the last 10 tagged images. Older tagged images are
  #      expired so the registry does not grow without bound. 10 is
  #      enough headroom for rolling back a few releases without
  #      bloating storage cost.
  #   2. Expire untagged images after 14 days. Untagged images are
  #      almost always orphaned layers from old builds; 14 days is a
  #      safe window for any in-flight investigation that might still
  #      need them.
  #
  # The `tagStatus = "any"` rule MUST have the highest priority number
  # because more-specific rules are evaluated first. Untagged comes
  # before tagged-any in priority order.
  lifecycle_policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images older than 14 days."
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep only the last 10 tagged images."
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = 10
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# KMS CMK
#
# Single shared customer-managed key for all dev ECR repositories.
# Trade-off note: per-repo CMKs would isolate blast radius if a single
# key needed rotation or grant changes, but ECR images are not
# regulated PII or payment data; they are application binaries. A
# shared key keeps cost flat (~$1/month total instead of ~$11/month
# across 11 repos) and simplifies IAM grants for cross-account or
# cross-service consumers later. The 7-day deletion window is short
# (vs the 30-day default we use on storage) because losing a dev ECR
# key only loses dev images, which CI can rebuild from source.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "ecr" {
  description             = "KMS key for the dev ECR repositories"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  tags = local.common_tags
}

resource "aws_kms_alias" "ecr" {
  name          = "alias/${local.name_prefix}-ecr"
  target_key_id = aws_kms_key.ecr.key_id
}

# ---------------------------------------------------------------------------
# ECR repositories
#
# One repository per service via for_each over the service list.
#
#   - image_tag_mutability = IMMUTABLE: prevents tag overwrite, so a
#     given tag (for example `git-sha-abc123` or `v0.4.2`) always
#     points to the same image bytes. Eliminates a class of supply
#     chain ambiguity where a tag silently changes content under
#     callers; required for reproducible deploys.
#   - scan_on_push = true: triggers ECR's basic scanning on every push.
#     Free, surfaces CVEs as soon as the image lands without us having
#     to wire up enhanced scanning (Inspector v2). We can flip to
#     enhanced later by adding aws_ecr_registry_scanning_configuration.
#   - encryption_type = KMS with our shared CMK: encrypts image layers
#     and image manifests at rest with a key we control rotation on.
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "service" {
  for_each = toset(local.services)

  name                 = "${local.name_prefix}-${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.ecr.arn
  }

  tags = merge(local.common_tags, {
    Service = each.key
  })
}

# ---------------------------------------------------------------------------
# Lifecycle policy
#
# One policy resource per repository, all using the same JSON document
# from local.lifecycle_policy. Splitting this from the repository
# resource is the standard Terraform pattern (the AWS provider exposes
# them as separate resources) and lets us tweak the policy in one
# place if rules later diverge per repository.
# ---------------------------------------------------------------------------

resource "aws_ecr_lifecycle_policy" "service" {
  for_each = aws_ecr_repository.service

  repository = each.value.name
  policy     = local.lifecycle_policy
}
