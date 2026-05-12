# Dev environment events providers.
#
# This config wires the async messaging backbone of the Panakoes
# pipeline: a custom EventBridge bus, three pipeline-stage rules
# (audio uploaded, transcript completed, summary completed), three
# SQS queues (one per stage) plus DLQs, three SNS fan-out topics
# (system alerts, billing events, user notifications), a notification
# queue subscribed to the user-notifications topic, CloudWatch alarms
# on each DLQ, and a dedicated CMK encrypting all of it.
#
# Backend values are hardcoded because Terraform does not allow
# variables in the backend block. They mirror the outputs of
# `infra/bootstrap/`. The `key` namespaces this configuration's state
# inside the bucket so other configs do not collide.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/events/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    kms_key_id   = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "events"
      Service     = "platform"
      Component   = "observability"
    }
  }
}
