variable "aws_region" {
  description = "AWS region the consolidated CMKs are created in. Single-region by design; the keys are not multi-region (multi-region adds operational complexity that the dev environment does not need)."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name used for tagging only. The new key aliases deliberately omit the environment from their names (`alias/panakoes/app-data` instead of `alias/panakoes-dev-app-data`) because the same aliases will be reused in prod under a separate AWS account with its own KMS keystore."
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used for tagging only. Same rationale as `environment`: the alias names use a fixed `panakoes/` prefix rather than templating against the variable, because the alias identity is part of the cross-PR contract that downstream modules consume."
  type        = string
  default     = "panakoes"
}
