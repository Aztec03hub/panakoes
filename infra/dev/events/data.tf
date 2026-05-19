# Discovers the current AWS account and partition so that resource
# policies can scope publish/subscribe rights to same-account
# principals without hardcoding the account id.
data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

# Pulls the audio-uploads bucket ARN from the storage module's remote
# state. EventBridge needs the bucket-level ARN inside its event-pattern
# match so we react only to ObjectCreated events on this specific
# bucket rather than every bucket in the account.
data "terraform_remote_state" "storage" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/storage/terraform.tfstate"
    region = "us-east-1"
  }
}

# Consolidated KMS module remote state (W2-T1, PR #365). Surfaces the
# `panakoes/app-data` CMK ARN; every aws_sqs_queue and aws_sns_topic
# in this module migrates onto this key as of W2-T3. The local
# aws_kms_key.events resource is retained below for W2-T7 retirement.
data "terraform_remote_state" "kms" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/kms/terraform.tfstate"
    region = "us-east-1"
  }
}
