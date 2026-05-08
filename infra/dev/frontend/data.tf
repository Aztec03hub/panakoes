# Cross-config data sources.
#
# `aws_caller_identity` underpins KMS key-policy principals.
#
# The WAF remote-state lookup is wrapped in `try()` so this module can
# `terraform plan` cleanly even before `infra/dev/waf/` has been
# applied. When the upstream state file does not yet exist, `try()`
# yields `null` and the distribution is provisioned without a WAF
# association (see `var.associate_waf`).
#
# Note: the existing `infra/dev/waf/` web ACL is scoped REGIONAL and
# therefore cannot be attached to a CloudFront distribution (CloudFront
# requires scope = CLOUDFRONT). `var.associate_waf` defaults to `false`
# until a CloudFront-scoped ACL is provisioned in a future
# `infra/dev/waf-cloudfront/` module. The data source is wired up now
# so swapping the ACL later is a single-line variable flip plus a
# remote-state pointer change.

data "aws_caller_identity" "current" {}

data "terraform_remote_state" "waf" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/waf/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  # try() lets `terraform plan` succeed before the waf state exists.
  waf_web_acl_arn = try(data.terraform_remote_state.waf.outputs.web_acl_arn, null)
}
