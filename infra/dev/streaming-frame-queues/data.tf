data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

# Pulls the consolidated app-data CMK ARN from the KMS module so the
# pool's SQS queues share the same key already used by the events
# module's queues. Keeping the CMK count down avoids the $1/mo/key
# fixed cost and matches the W2-T3 consolidation pattern.
data "terraform_remote_state" "kms" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/kms/terraform.tfstate"
    region = "us-east-1"
  }
}
