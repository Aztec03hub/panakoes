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
