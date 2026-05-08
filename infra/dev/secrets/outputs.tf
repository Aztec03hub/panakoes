output "secret_arns" {
  description = "Map of secret_name (e.g. 'jwt-signing-secret') to the secret's full ARN. Consumers reference these in IAM policies and at runtime via the AWS SDK. Secret values are NOT exposed; this output is ARN-only by design."
  value = {
    for name, secret in aws_secretsmanager_secret.this : name => secret.arn
  }
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting every secret in this module. Required in any IAM policy that grants kms:Decrypt to a service consuming these secrets."
  value       = aws_kms_key.secrets.arn
}
