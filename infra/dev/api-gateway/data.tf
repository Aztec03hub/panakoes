# Account and region context. Used to build explicit ARNs for the
# CloudWatch metric namespace and the placeholder NLB integration
# targets that downstream services will replace.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Network module remote state
#
# Provides the VPC ID and the private subnet IDs the VPC Link binds to.
# Required: this module cannot stand up without the dev VPC. The
# `terraform_remote_state` data source raises an explicit error if the
# `dev/network/` state is missing, which surfaces the dependency
# clearly in plan output rather than failing inside an obscure
# resource.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/network/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# WAF module remote state (optional)
#
# `try()` lets this module apply even if `dev/waf/` has not yet been
# applied (for example on a fresh-account bootstrap where the modules
# land in a different order). Once the WAF state exists, the
# `aws_wafv2_web_acl_association` resource picks up the ACL ARN
# automatically. Until then, `local.web_acl_arn` is null and the
# association resource has count=0.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "waf" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/waf/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# Observability module remote state (placeholder)
#
# A dedicated `infra/dev/observability/` module is on the backlog and
# will own the shared dev CMK that encrypts CloudWatch log groups,
# X-Ray exports, and CloudWatch metric streams. Until then, the
# `try()` wrapper returns null and the module falls back to its own
# locally-provisioned KMS key (see `aws_kms_key.api_gateway_logs` in
# main.tf). When the observability module ships, switch the local
# `log_kms_key_arn` reference to use the remote-state output and
# delete the inline key resource in a follow-up PR.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "observability" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/observability/terraform.tfstate"
    region = "us-east-1"
  }
}
