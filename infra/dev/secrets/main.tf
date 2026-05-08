locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "secrets"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # ---------------------------------------------------------------------------
  # Secret definitions
  #
  # Naming convention: `panakoes-dev/<purpose>`. The forward slash is
  # accepted by Secrets Manager and groups secrets by environment in
  # the AWS console. Each entry's `placeholder` value is what
  # Terraform writes on first create. The `aws_secretsmanager_secret_version`
  # resource below uses `lifecycle { ignore_changes = [secret_string] }`
  # so subsequent applies do not revert manual rotations performed via
  # the AWS CLI (see README post-apply steps).
  #
  # Plain-string secrets get the literal `REPLACE_ME_AFTER_APPLY`
  # marker. Structured secrets (SES SMTP creds) get a JSON shape with
  # the same marker for each field so consumers can parse the JSON
  # safely even before real values are written.
  # ---------------------------------------------------------------------------
  secrets = {
    "jwt-signing-secret" = {
      description = "HS256 secret used by the auth service for slice 1 JWT signing. Rotate via AWS CLI after apply; consumers read at runtime, not build time."
      placeholder = "REPLACE_ME_AFTER_APPLY"
    }
    "anthropic-api-key" = {
      description = "Anthropic API key used by the summarization service to call Claude Haiku 4.5 (default tier) and Sonnet 4.6 (paid 'deep summary')."
      placeholder = "REPLACE_ME_AFTER_APPLY"
    }
    "stripe-test-key" = {
      description = "Stripe TEST mode secret key for the billing service. Never use a live-mode key in this dev secret."
      placeholder = "REPLACE_ME_AFTER_APPLY"
    }
    "stripe-webhook-signing-secret" = {
      description = "Stripe webhook signing secret used by the billing service to verify incoming webhook payload signatures."
      placeholder = "REPLACE_ME_AFTER_APPLY"
    }
    "postgres-auth-db-password" = {
      description = "Postgres password for the auth service's database user. Read by the auth service at startup; not used directly by Terraform."
      placeholder = "REPLACE_ME_AFTER_APPLY"
    }
    "database-url" = {
      description = "Full Postgres connection URL for the auth service (postgres://user:password@host:5432/dbname). Assembled with the password value above."
      placeholder = "postgres://REPLACE_ME_AFTER_APPLY:REPLACE_ME_AFTER_APPLY@REPLACE_ME_AFTER_APPLY:5432/REPLACE_ME_AFTER_APPLY"
    }
    "ses-smtp-credentials" = {
      description = "SES SMTP credentials (JSON: username + password) used by transactional email senders."
      placeholder = jsonencode({
        username = "REPLACE_ME_AFTER_APPLY"
        password = "REPLACE_ME_AFTER_APPLY"
      })
    }
  }
}

# ---------------------------------------------------------------------------
# Customer-managed KMS key
#
# A single CMK encrypts every secret in this module. Per-secret keys
# would let us scope blast radius further but $1/key/month adds up
# fast at seven secrets, and at the dev tier the marginal security
# benefit does not justify the cost. Production should reconsider.
#
# Rotation enabled (annual, AWS-managed). Deletion window is 7 days
# (deliberately shorter than the 30-day default used by S3 / DynamoDB
# CMKs in sibling modules) so a fat-finger destroy on the dev secrets
# stack does not strand the team for a month while the key sits in
# pending-deletion. Real secret material in dev is replaceable in
# minutes; the long undelete window is overkill here.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "secrets" {
  description             = "KMS key for the dev Secrets Manager secrets"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  tags = local.common_tags
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/${local.name_prefix}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

# ---------------------------------------------------------------------------
# Secrets Manager secrets
#
# `for_each` over the local `secrets` map keeps the module additive:
# adding a new secret is a single-line edit to `local.secrets`. The
# secret name embeds the environment as a path segment
# (`panakoes-dev/<purpose>`) so the AWS console groups them and IAM
# policies can target the entire dev set with a single
# `arn:aws:secretsmanager:...:secret:panakoes-dev/*` resource pattern.
#
# `recovery_window_in_days = 7` matches the KMS deletion window and
# the same dev-tier rationale applies: short enough to recover from
# accident, short enough not to block reuse of the secret name.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "this" {
  for_each = local.secrets

  name                    = "${local.name_prefix}/${each.key}"
  description             = each.value.description
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = 7

  tags = merge(local.common_tags, {
    SecretPurpose = each.key
  })
}

# ---------------------------------------------------------------------------
# Initial secret versions
#
# Terraform writes the placeholder once, then `ignore_changes` on
# `secret_string` prevents subsequent applies from clobbering rotated
# values. The first post-apply step is to overwrite each secret via
# `aws secretsmanager put-secret-value`; see this module's README.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret_version" "this" {
  for_each = local.secrets

  secret_id     = aws_secretsmanager_secret.this[each.key].id
  secret_string = each.value.placeholder

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ---------------------------------------------------------------------------
# Resource-based policies (deferred)
#
# TODO: task #34 wires up IAM service roles for each consuming
# microservice (auth, summarization, billing, ingestion). Once those
# roles exist, attach a resource-based policy per secret that grants
# `secretsmanager:GetSecretValue` only to the role that needs it,
# pinned by aws:PrincipalArn. Sketch:
#
#   data "aws_iam_policy_document" "jwt_signing_secret_access" {
#     statement {
#       sid     = "AllowAuthServiceRead"
#       effect  = "Allow"
#       actions = ["secretsmanager:GetSecretValue"]
#       principals {
#         type        = "AWS"
#         identifiers = [data.terraform_remote_state.iam.outputs.auth_service_role_arn]
#       }
#       resources = [aws_secretsmanager_secret.this["jwt-signing-secret"].arn]
#     }
#   }
#
#   resource "aws_secretsmanager_secret_policy" "jwt_signing_secret" {
#     secret_arn = aws_secretsmanager_secret.this["jwt-signing-secret"].arn
#     policy     = data.aws_iam_policy_document.jwt_signing_secret_access.json
#   }
#
# Until task #34 lands, access is gated only by the consuming role's
# inline / managed IAM policy; the secret itself has no resource-based
# allowlist. AWS account-level isolation plus IAM is sufficient for
# dev; we tighten with resource policies before any non-dev workload
# touches these secrets.
# ---------------------------------------------------------------------------
