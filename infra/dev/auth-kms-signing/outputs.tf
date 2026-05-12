output "jwt_signing_key_id" {
  description = "KMS key ID for the auth-service RS256 signing key. Consumers pass this as `AUTH_JWT_KMS_KEY_ID` to the auth service and reference it in `kms:Sign` + `kms:GetPublicKey` IAM grants."
  value       = aws_kms_key.jwt_signing.key_id
}

output "jwt_signing_key_arn" {
  description = "Full ARN of the RS256 signing CMK. Use this in IAM policies attached to the auth task role (services need exact ARNs, not aliases, on `kms:Sign` grants)."
  value       = aws_kms_key.jwt_signing.arn
}

output "jwt_signing_key_alias" {
  description = "KMS alias for the RS256 signing key. The auth service references the key via this alias so a future key rotation is a Terraform-only change (re-point the alias) with no application restart."
  value       = aws_kms_alias.jwt_signing.name
}
