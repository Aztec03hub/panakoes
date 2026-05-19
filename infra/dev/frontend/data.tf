# Cross-config data sources.
#
# `aws_caller_identity` underpins KMS key-policy principals.
#
# The CloudFront WAF remote-state lookup is wrapped in `try()` so this
# module can `terraform plan` cleanly even before
# `infra/dev/cloudfront-waf/` has been applied. When the upstream
# state file does not yet exist, `try()` yields `null` and the
# distribution is provisioned without a WAF association (see
# `var.associate_waf`).
#
# The `infra/dev/waf/` module's web ACL is scoped REGIONAL and
# therefore cannot be attached to a CloudFront distribution
# (CloudFront requires scope = CLOUDFRONT). `infra/dev/cloudfront-waf/`
# owns the CloudFront-scoped ACL; this module reads its ARN and sets
# the distribution's `web_acl_id` attribute via `var.associate_waf`,
# which now defaults to true.

data "aws_caller_identity" "current" {}

data "terraform_remote_state" "cloudfront_waf" {
  backend = "s3"

  # `defaults` lets `terraform plan` succeed before the
  # cloudfront-waf state file exists in S3. Without it,
  # `terraform_remote_state` raises a hard error rather than yielding
  # an empty outputs object, and `try()` cannot rescue a read failure
  # on the data source itself. Once the upstream module is applied,
  # the real outputs override these placeholders.
  defaults = {
    web_acl_arn = null
  }

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/cloudfront-waf/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  # try() is belt-and-suspenders alongside the `defaults` block above:
  # if a future refactor renames the upstream output, the local stays
  # null instead of raising. Resolves to the CloudFront-scoped web
  # ACL's ARN once cloudfront-waf is applied. CloudFront's
  # `web_acl_id` field is misleadingly named; it accepts an ARN.
  waf_web_acl_arn = try(data.terraform_remote_state.cloudfront_waf.outputs.web_acl_arn, null)

  # Consolidated app-data CMK ARN (W2-T1, PR #365). Replaces the
  # per-bucket aws_kms_key.frontend CMK at the SSE layer; the old
  # aws_kms_key.frontend resource is intentionally left in place so
  # W2-T7 (orchestrator-only) can schedule it for deletion via AWS CLI.
  # The consolidated key's policy grants kms:Decrypt /
  # kms:GenerateDataKey to cloudfront.amazonaws.com under the
  # aws:CallerAccount condition, which preserves the OAC + SSE-KMS
  # delivery path this bucket relies on.
  app_data_kms_key_arn = data.terraform_remote_state.kms.outputs.app_data_key_arn
}

# Consolidated KMS module remote state (W2-T1). Surfaces the
# `panakoes/app-data` CMK ARN so the frontend SSE config stops using
# its per-bucket key.
data "terraform_remote_state" "kms" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/kms/terraform.tfstate"
    region = "us-east-1"
  }
}
