# Storage module remote state: provides the log-archive S3 bucket ARN
# used as the destination for VPC Flow Logs (cheaper than CloudWatch
# Logs ingestion at our volume). The bucket is created in
# `infra/dev/storage/main.tf` and its ARN is exported as
# `log_archive_bucket_arn` in that module's outputs.
data "terraform_remote_state" "storage" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/storage/terraform.tfstate"
    region = "us-east-1"
  }
}
