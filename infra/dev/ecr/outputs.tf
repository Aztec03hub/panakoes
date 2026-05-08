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
  description = "ARN of the CMK encrypting all dev ECR repositories. Required in IAM policies that grant kms:Decrypt to image consumers (ECS task execution roles, EC2 instance roles)."
  value       = aws_kms_key.ecr.arn
}
