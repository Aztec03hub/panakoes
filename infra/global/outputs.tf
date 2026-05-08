output "github_actions_role_arn" {
  description = "ARN of the IAM role GitHub Actions workflows assume via OIDC. Use this as `role-to-assume` in `aws-actions/configure-aws-credentials`."
  value       = aws_iam_role.github_actions.arn
}

output "github_oidc_provider_arn" {
  description = "ARN of the IAM OIDC identity provider for GitHub Actions."
  value       = aws_iam_openid_connect_provider.github.arn
}
