# Dev environment streaming WebSocket API providers.
#
# This module provisions the public-facing AWS API Gateway v2 WebSocket
# API (`panakoes-dev-streaming-ws`) that fronts the streaming
# transcription session lifecycle. It is intentionally separate from
# `infra/dev/api-gateway/` because:
#
#   1. The two APIs have different `protocol_type` values (HTTP vs
#      WEBSOCKET); a single `aws_apigatewayv2_api` resource cannot
#      serve both.
#   2. The route shapes are unrelated. The HTTP API fronts CRUD
#      microservices; the WebSocket API carries bidirectional audio
#      frames and partial transcripts.
#   3. Splitting their Terraform states isolates blast radius. A
#      WebSocket route refactor cannot accidentally take down the
#      synchronous CRUD path.
#
# State namespace: `dev/api-gateway-ws/`. Backend values mirror the
# outputs of `infra/bootstrap/` (the same S3 bucket + KMS key every
# dev module shares).

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }

    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  backend "s3" {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/api-gateway-ws/terraform.tfstate"
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
      Module      = "api-gateway-ws"
    }
  }
}
