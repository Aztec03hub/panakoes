locals {
  common_tags = {
    Project     = var.project_name
    Environment = "shared"
    ManagedBy   = "terraform"
    Module      = "global"
  }
}

# Fetch the live GitHub OIDC issuer certificate at plan time so the
# thumbprint stays in sync as GitHub rotates its TLS chain. Using a
# data source here avoids a stale hardcoded fingerprint becoming a
# silent CI outage the day GitHub rotates.
data "tls_certificate" "github_oidc" {
  url = "https://token.actions.githubusercontent.com"
}

# IAM OIDC identity provider for GitHub Actions.
#
# AWS now validates the JWT signature against GitHub's published JWKS
# directly, so the thumbprint_list is essentially a backstop, but the
# AWS API still requires at least one entry. We pass the live
# fingerprint of the leaf cert from the TLS data source above.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github_oidc.certificates[0].sha1_fingerprint]

  tags = local.common_tags
}

# Trust policy: any GitHub Actions invocation from the configured
# repository may assume this role. The audience condition pins the
# token to AWS STS; the subject condition pins it to this repo.
data "aws_iam_policy_document" "github_actions_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name                 = "panakoes-github-actions"
  description          = "Assumed by GitHub Actions workflows in Aztec03hub/panakoes repo via OIDC"
  max_session_duration = 3600
  assume_role_policy   = data.aws_iam_policy_document.github_actions_trust.json

  tags = local.common_tags
}

# AdministratorAccess is intentionally broad for early development
# velocity. Before any production usage this MUST be replaced with a
# least-privilege policy scoped to the specific actions CI needs
# (ECR push, ECS / Lambda update, S3 write to deployment artifact
# buckets, CloudWatch Logs read, IAM PassRole only for the specific
# roles CI passes to ECS / Lambda). Tracked as a backlog item in
# SECURITY.md.
resource "aws_iam_role_policy_attachment" "github_actions_admin" {
  role       = aws_iam_role.github_actions.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
