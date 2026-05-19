output "repository_urls" {
  description = "Map of service name to ECR repository URL. Use these in CI as the docker push target."
  value = {
    for service, repo in aws_ecr_repository.service :
    service => repo.repository_url
  }
}

output "repository_arns" {
  description = "Map of service name to ECR repository ARN. Use these in IAM policies that grant pull or push access."
  value = {
    for service, repo in aws_ecr_repository.service :
    service => repo.arn
  }
}

output "kms_key_arn" {
  description = "ARN of the per-repo CMK encrypting the legacy dev ECR repositories. Required in IAM policies that grant kms:Decrypt to image consumers (ECS task execution roles, EC2 instance roles) for the legacy `panakoes-dev-<service>` repos. The v2 repos (`panakoes-dev-<service>-v2`) use the consolidated `panakoes/app-data` CMK (see `kms_key_arn_v2`). Both keys are referenced by ECS task execution role grants during the W2-T3 transition; the per-repo CMK is retired in W2-T7."
  value       = aws_kms_key.ecr.arn
}

# ---------------------------------------------------------------------------
# W2-T3 parallel v2 outputs.
#
# These outputs publish the new repository URLs / ARNs and the
# consolidated KMS key ARN so downstream consumers can wire IAM grants
# and ECS task definitions against the v2 cohort. The legacy outputs
# above remain populated during the transition so any consumer that
# has not been updated yet keeps working.
# ---------------------------------------------------------------------------

output "repository_urls_v2" {
  description = "Map of service name to v2 ECR repository URL (`panakoes-dev-<service>-v2`). Use these in CI as the docker push target for the W2-T3 transition and in ECS task definitions as the image source. After W2-T7 retirement of the legacy repos these outputs become the canonical `repository_urls`."
  value = {
    for service, repo in aws_ecr_repository.service_v2 :
    service => repo.repository_url
  }
}

output "repository_arns_v2" {
  description = "Map of service name to v2 ECR repository ARN. Use these in IAM policies that grant pull or push access to the v2 cohort. The execution-role policies need both legacy and v2 ARNs during transition; after W2-T7 the v2 set is the only one."
  value = {
    for service, repo in aws_ecr_repository.service_v2 :
    service => repo.arn
  }
}

output "kms_key_arn_v2" {
  description = "ARN of the consolidated panakoes/app-data CMK that encrypts the v2 ECR repositories. Resolved from infra/dev/kms remote state so a future key rotation upstream propagates automatically. ECS task execution roles consuming v2 images need kms:Decrypt on this ARN; the iam module reads it via remote_state of this ecr module."
  value       = data.terraform_remote_state.kms.outputs.app_data_key_arn
}
