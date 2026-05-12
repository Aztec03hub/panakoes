locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "auth-kms-signing"
  }

  name_prefix = "${var.project_name}-${var.environment}"
}

# ===========================================================================
# Asymmetric KMS key for RS256 JWT signing
#
# Spec (ADR-041 phase 1):
#   - key_usage     = SIGN_VERIFY (signing only; never used for encryption)
#   - key_spec      = RSA_2048 (the JWS RS256 algorithm)
#   - rotation      = OFF (AWS only supports rotation on symmetric CMKs;
#                     asymmetric keys rotate via manual re-key per AWS docs)
#   - deletion gate = 30 days (asymmetric public keys are baked into
#                     downstream verifier caches; the long undelete window
#                     is the safety net for an accidental destroy)
#
# Why not symmetric AES_256 + manual derivation: the JWS spec for RS256
# requires an actual RSA keypair. ED25519 (key_spec = ECC_NIST_P256) is
# a viable future direction but the jose ecosystem in our Python and
# TypeScript consumers ships with RS256 by default and the upgrade cost
# is not justified at the dev tier.
# ===========================================================================

resource "aws_kms_key" "jwt_signing" {
  description              = "Asymmetric KMS key for auth-service RS256 JWT signing (ADR-041)"
  customer_master_key_spec = "RSA_2048"
  key_usage                = "SIGN_VERIFY"
  enable_key_rotation      = false
  deletion_window_in_days  = 30

  # Key policy: root account has full administrative access; everything
  # else (kms:Sign + kms:GetPublicKey for the auth task role,
  # kms:GetPublicKey for unauthenticated JWKS consumers if we ever
  # expose it directly) is layered via grants from infra/dev/iam/.
  # Keeping the resource policy minimal here avoids drift when downstream
  # services are added.
  policy = data.aws_iam_policy_document.key_policy.json

  tags = merge(local.common_tags, {
    Purpose = "jwt-signing"
  })
}

resource "aws_kms_alias" "jwt_signing" {
  name          = "alias/${local.name_prefix}-jwt-signing"
  target_key_id = aws_kms_key.jwt_signing.key_id
}

# ---------------------------------------------------------------------------
# Key policy
#
# Root-only administrative trust. Application principals receive `kms:Sign`
# and `kms:GetPublicKey` via IAM identity policies attached in
# `infra/dev/iam/main.tf`; we deliberately do NOT broaden the key policy
# itself because identity-policy + key-policy double-gating is the AWS
# best-practice for least-privilege on shared CMKs.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "key_policy" {
  statement {
    sid     = "RootAdmin"
    effect  = "Allow"
    actions = ["kms:*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    resources = ["*"]
  }
}
