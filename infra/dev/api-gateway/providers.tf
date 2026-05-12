# Dev environment API Gateway providers.
#
# This config provisions the public-facing AWS API Gateway v2 HTTP API
# (`panakoes-dev-public`) that fronts every Panakoes microservice.
# An HTTP API was chosen over a REST API because the Panakoes APIs do
# not need request/response transformations, API keys, or usage plans;
# HTTP APIs ship JWT authorization, CORS, and VPC Link integrations at
# roughly one third the per-million-request cost of REST APIs.
#
# State namespace: `dev/api-gateway/`. Backend values are hardcoded
# because Terraform does not allow variables in the backend block. They
# mirror the outputs of `infra/bootstrap/`.

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
    key          = "dev/api-gateway/terraform.tfstate"
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
      Module      = "api-gateway"
      Service     = "api-gateway"
      Component   = "network"
    }
  }
}
