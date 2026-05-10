variable "aws_region" {
  description = "AWS region for the dev environment security observability resources. Config and GuardDuty are regional services; Security Hub aggregates cross-region but its account-level resource still binds to the home region declared here."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name used for tagging and resource naming."
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used for resource naming and tagging."
  type        = string
  default     = "panakoes"
}

variable "enable_config" {
  description = "When true, the AWS Config configuration recorder is started (begins recording + rule evaluations, both billable). Default false so a fresh apply does not begin charging. The recorder, delivery channel, S3 bucket, KMS CMK, IAM service role, and managed rules are always provisioned regardless of this flag; only the recorder's running state and the rule evaluations gate on it."
  type        = bool
  default     = false
}

variable "enable_guardduty" {
  description = "When true, the GuardDuty detector is enabled and begins ingesting CloudTrail / VPC Flow Log / DNS data sources. Default false (the detector resource is still provisioned with `enable = false` so plan/apply is idempotent and the flip is a one-line change)."
  type        = bool
  default     = false
}

variable "enable_security_hub" {
  description = "When true, the Security Hub account is enabled and the AWS Foundational Security Best Practices + CIS AWS Foundations standards are subscribed. Default false. Security Hub has no `enabled` attribute on the resource itself; presence enables the service. We gate via `count = var.enable_security_hub ? 1 : 0` so disabling is a destroy-on-next-apply, which matches the cost model (Security Hub bills per finding ingested + per check evaluated)."
  type        = bool
  default     = false
}

variable "config_rules" {
  description = "Managed AWS Config rules to deploy. Free-tier rules only by default; rule evaluations themselves cost $0.001 each but a sub-100-resource dev environment evaluates at most a few dozen times a day. Add a rule here to deploy it; remove to destroy it."
  type        = set(string)
  default = [
    "s3-bucket-public-read-prohibited",
    "iam-password-policy",
    "incoming-ssh-disabled",
  ]
}
