# Dev environment streaming-frame-queues providers.
#
# Owns the 32-slot pre-allocated SQS frame-queue pool and the DDB
# pool-state table backing the gpu-spawner's drain-then-claim
# protocol. The streaming-router fans audio frames into one of these
# queues per session; the GPU container consumes from the same queue.
# Per the design doc "Frame-queue strategy (CRIT-01 + HIGH-06 fix)".

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
    key          = "dev/streaming-frame-queues/terraform.tfstate"
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
      Module      = "streaming-frame-queues"
      Service     = "streaming"
      Component   = "frame-pool"
    }
  }
}
