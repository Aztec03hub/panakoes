# ---------------------------------------------------------------------------
# Public outputs (safe to print, no secret material)
# ---------------------------------------------------------------------------

output "configuration_set_name" {
  description = "Name of the SES configuration set fronting the dev environment."
  value       = aws_sesv2_configuration_set.this.configuration_set_name
}

output "domain_identity_arn" {
  description = "ARN of the lafayettelabs.com domain identity. Verification status is queried via `aws sesv2 get-email-identity`."
  value       = aws_sesv2_email_identity.domain.arn
}

output "primary_sender_identity_arn" {
  description = "ARN of the phil@lafayettelabs.com email identity used for sandbox-mode smoke testing."
  value       = aws_sesv2_email_identity.primary_sender.arn
}

output "dkim_tokens" {
  description = "Three DKIM tokens that must be published as CNAME records at the domain registrar. Format: each token T becomes `<T>._domainkey.<domain>` CNAME `<T>.dkim.amazonses.com`."
  value       = aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens
}

output "dkim_cname_records" {
  description = "Pre-formatted CNAME records to publish in Cloudflare DNS for `lafayettelabs.com`. Each entry is `{name, value}` with full hostnames; just paste into Cloudflare."
  value = [
    for token in aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens : {
      name  = "${token}._domainkey.${var.sender_domain}"
      type  = "CNAME"
      value = "${token}.dkim.amazonses.com"
      proxy = "DNS only"
      ttl   = "Auto"
    }
  ]
}

output "smtp_endpoint" {
  description = "SES SMTP endpoint hostname for this region. Use port 587 with STARTTLS."
  value       = "email-smtp.${data.aws_region.current.region}.amazonaws.com"
}

output "smtp_iam_user_arn" {
  description = "ARN of the IAM user whose access key derives the SMTP credentials."
  value       = aws_iam_user.ses_smtp.arn
}

# ---------------------------------------------------------------------------
# Sensitive outputs (never echoed by Terraform unless explicitly requested)
#
# Read these post-apply via:
#   terraform output -raw smtp_access_key_id
#   terraform output -raw smtp_secret_access_key
#
# Then convert to an SMTP password via the helper script
# `scripts/ses_smtp_password.py` (see docs/runbooks/ses-bootstrap.md).
# NEVER paste either value into a commit, a log file, or a chat
# transcript. Write directly to AWS Secrets Manager.
# ---------------------------------------------------------------------------

output "smtp_access_key_id" {
  description = "Access key id for the SES SMTP IAM user. Doubles as the SMTP username. SENSITIVE."
  value       = aws_iam_access_key.ses_smtp.id
  sensitive   = true
}

output "smtp_secret_access_key" {
  description = "Secret access key for the SES SMTP IAM user. Input to the HMAC-SHA256 conversion that yields the SMTP password. SENSITIVE."
  value       = aws_iam_access_key.ses_smtp.secret
  sensitive   = true
}
