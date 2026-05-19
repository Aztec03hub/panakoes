# W2-T3 parallel v2 ECR repositories (consolidated app-data CMK).
#
# These resources implement step 1 of the parallel-repos migration
# documented in main.tf header. They live in their own file so the
# transition is a clean diff and the W2-T7 retirement PR can delete
# this file (plus a one-line change in main.tf to drop the original
# aws_ecr_repository.service / aws_ecr_lifecycle_policy.service /
# aws_kms_key.ecr block) without an interleaved edit.
#
# Identical to the production block in main.tf EXCEPT:
#   - name suffix `-v2`
#   - encryption_configuration.kms_key references the consolidated
#     `panakoes/app-data` CMK provisioned by `infra/dev/kms/`
#   - tags include `MigrationGeneration = "v2"` so a future audit can
#     distinguish the two cohorts by tag query.

# ---------------------------------------------------------------------------
# KMS module remote state (consumed read-only).
#
# Provides the consolidated app-data CMK ARN. The kms module's
# outputs were established by PR #365 (W2-T1) and consumed by PR #405
# (W2-T2..T6 storage / events / observability / api-gateway / secrets).
# This module is the next consumer in line.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "kms" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/kms/terraform.tfstate"
    region = "us-east-1"
  }
}

resource "aws_ecr_repository" "service_v2" {
  for_each = toset(local.services)

  name                 = "${local.name_prefix}-${each.key}-v2"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = data.terraform_remote_state.kms.outputs.app_data_key_arn
  }

  tags = merge(local.common_tags, {
    Service             = each.key
    MigrationGeneration = "v2"
  })
}

resource "aws_ecr_lifecycle_policy" "service_v2" {
  for_each = aws_ecr_repository.service_v2

  repository = each.value.name
  policy     = local.lifecycle_policy
}
