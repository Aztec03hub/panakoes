data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Network module remote state (consumed)
#
# Provides the dev VPC ID, the three private subnet IDs (us-east-1a/b/c)
# the ALB spans, and the VPC CIDR block (10.10.0.0/16) that gates the
# ALB security group's inbound port-80 rule.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/network/terraform.tfstate"
    region = "us-east-1"
  }
}
