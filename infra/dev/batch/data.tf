# Account and region data sources used to build explicit ARNs and the
# constructed ECR image URI fallback.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Network module remote state: VPC + private subnets the compute
# environment attaches to. Async Batch workloads land in private
# subnets (NAT-egress only) so the GPU instances reach S3 + ECR + the
# AWS API but never accept inbound traffic.
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/network/terraform.tfstate"
    region = "us-east-1"
  }
}

# IAM module remote state: provides the transcriber-batch task role
# (job role, runtime identity for the container) and the GPU instance
# profile (instance role, Batch uses this when launching the EC2
# Spot fleet).
data "terraform_remote_state" "iam" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/iam/terraform.tfstate"
    region = "us-east-1"
  }
}

# ECR module remote state: surfaces the per-service repository URLs.
# Wrapped in `try()` because the ECR module ships in a separate slice
# and may not yet be applied when this config first runs. When the
# remote state is missing we fall back to the constructed default
# repository URI for the transcriber-batch service so `terraform
# validate` and `init -backend=false` stay clean.
data "terraform_remote_state" "ecr" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/ecr/terraform.tfstate"
    region = "us-east-1"
  }

  # `defaults` lets the data source return a usable shape even before
  # the upstream config has been applied. The fallback URI mirrors what
  # the ECR module produces (`<acct>.dkr.ecr.<region>.amazonaws.com/<repo>`).
  defaults = {
    repository_urls = {}
  }
}

# Events module remote state: surfaces the project-wide system-alerts
# SNS topic ARN. The events module owns the topic; this module is a
# leaf consumer that wires it into the FailedJobs CloudWatch alarm's
# alarm_actions / ok_actions. Previously this module re-declared the
# topic, which caused `InvalidParameter: Topic already exists with
# different tags` on first apply 2026-05-09 because events had already
# applied. The fix is to pull the ARN from events' remote state.
data "terraform_remote_state" "events" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/events/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  system_alerts_topic_arn = data.terraform_remote_state.events.outputs.sns_topic_arns["system-alerts"]

  ecr_fallback_repo_url = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${var.project_name}-${var.environment}-transcriber-batch"

  transcriber_batch_repo_url = try(
    data.terraform_remote_state.ecr.outputs.repository_urls["transcriber-batch"],
    local.ecr_fallback_repo_url,
  )

  # Default to the `:latest` tag for now. CI publishes immutable
  # git-sha tags to the ECR repo (see `infra/dev/ecr/`), and this
  # module should be updated to consume a `transcriber_batch_image_tag`
  # variable wired from the deploy pipeline once it lands.
  transcriber_batch_image_uri = "${local.transcriber_batch_repo_url}:latest"
}
