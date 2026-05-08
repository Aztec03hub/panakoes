output "bucket_name" {
  description = "Name of the S3 origin bucket holding SvelteKit static assets. Used by the deploy command (aws s3 sync ...)."
  value       = aws_s3_bucket.frontend.id
}

output "bucket_arn" {
  description = "ARN of the S3 origin bucket. Used in IAM policies that grant the deploy role write access."
  value       = aws_s3_bucket.frontend.arn
}

output "distribution_id" {
  description = "CloudFront distribution ID for panakoes-dev-admin. Used by the deploy invalidation command (aws cloudfront create-invalidation --distribution-id ...)."
  value       = aws_cloudfront_distribution.admin.id
}

output "distribution_arn" {
  description = "CloudFront distribution ARN. Reference when crafting cross-account or service-principal policies that target this distribution."
  value       = aws_cloudfront_distribution.admin.arn
}

output "distribution_domain_name" {
  description = "CloudFront distribution domain name (the *.cloudfront.net hostname viewers reach until a custom domain is wired). DNS records and admin-app environment variables consume this."
  value       = aws_cloudfront_distribution.admin.domain_name
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting the frontend origin bucket. Required in IAM policies that grant kms:Decrypt to deploy roles or future readers."
  value       = aws_kms_key.frontend.arn
}

output "kms_key_alias" {
  description = "Alias of the CMK encrypting the frontend origin bucket (alias/panakoes-dev-frontend)."
  value       = aws_kms_alias.frontend.name
}

output "logs_bucket_name" {
  description = "Name of the dedicated CloudFront access-log bucket. Lifecycle expires logs at the configured retention window."
  value       = aws_s3_bucket.frontend_logs.id
}

output "logs_bucket_arn" {
  description = "ARN of the CloudFront access-log bucket."
  value       = aws_s3_bucket.frontend_logs.arn
}

output "origin_access_control_id" {
  description = "ID of the CloudFront Origin Access Control (OAC) the distribution uses to sign requests to the S3 origin. Useful when adding additional origins that should reuse the same OAC."
  value       = aws_cloudfront_origin_access_control.frontend.id
}
