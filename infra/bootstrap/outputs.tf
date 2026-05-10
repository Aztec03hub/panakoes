output "state_bucket_name" {
  description = "Name of the S3 bucket holding remote Terraform state."
  value       = aws_s3_bucket.tf_state.bucket
}

output "state_kms_key_arn" {
  description = "ARN of the KMS key encrypting the state bucket."
  value       = aws_kms_key.tf_state.arn
}

output "state_kms_key_alias" {
  description = "Alias of the KMS key encrypting the state bucket."
  value       = aws_kms_alias.tf_state.name
}
