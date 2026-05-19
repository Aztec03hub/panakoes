output "secret_arns" {
  description = "Map of secret_name (e.g. 'jwt-signing-secret') to the secret's full ARN. Consumers reference these in IAM policies and at runtime via the AWS SDK. Secret values are NOT exposed; this output is ARN-only by design."
  value = {
    for name, secret in aws_secretsmanager_secret.this : name => secret.arn
  }
}

output "groq_api_key_secret_arn" {
  description = "ARN of the Groq API key secret. Convenience output for IAM policies on the transcribe-worker and ingestion-api task roles. Equivalent to secret_arns[\"groq-api-key\"]."
  value       = aws_secretsmanager_secret.this["groq-api-key"].arn
}

output "openai_api_key_secret_arn" {
  description = "ARN of the OpenAI API key secret. Convenience output for the future openai-transcriber and any openai-fallback summarization role. Equivalent to secret_arns[\"openai-api-key\"]."
  value       = aws_secretsmanager_secret.this["openai-api-key"].arn
}

output "anthropic_api_key_secret_arn" {
  description = "ARN of the Anthropic API key secret. Convenience output for the summarization service task role. Equivalent to secret_arns[\"anthropic-api-key\"]."
  value       = aws_secretsmanager_secret.this["anthropic-api-key"].arn
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting every secret in this module. Required in any IAM policy that grants kms:Decrypt to a service consuming these secrets. W2-T3: now returns the consolidated panakoes/app-data CMK ARN; the module-local aws_kms_key.secrets resource is retained for W2-T7 retirement but no longer encrypts new versions."
  value       = local.app_data_kms_key_arn
}
