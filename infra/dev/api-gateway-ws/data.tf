# Account and region context. Used to build explicit ARNs for the
# CloudWatch log group KMS condition key and the SQS integration URI.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# KMS module remote state (Wave 2 consolidated CMKs, PR #365)
#
# W2-T4 extension: surfaces the consolidated `panakoes/logs` CMK ARN
# so every aws_cloudwatch_log_group in this module migrates onto the
# shared key. The module-local aws_kms_key.ws_logs and
# aws_kms_key.lambda_logs resources are retained below for W2-T7
# retirement (orchestrator-only step) but no longer encrypt any
# log group.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "kms" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/kms/terraform.tfstate"
    region = "us-east-1"
  }
}
