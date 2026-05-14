# Dev environment service-discovery: AWS Cloud Map private DNS namespace.
#
# This module provisions the `panakoes-dev.local` private DNS namespace that
# ECS Service Connect uses for service-to-service discovery. Each ECS service
# registers itself as `<service-name>.panakoes-dev.local:<port>` via the
# Service Connect sidecar. No NAT gateway or NLB is required for intra-cluster
# traffic once services are enrolled.
#
# Migration context (Wave 1, 2026-05-14):
# The current architecture runs 11 internal NLBs (~$198/mo). Wave 1 replaces
# them with one shared ALB (for API GW integration) plus ECS Service Connect
# (for service-to-service calls). This namespace is W1-T1 and is a hard
# prerequisite for W1-T4 (enrolling ECS services into Service Connect).
#
# Backend values are hardcoded because Terraform does not allow variables in
# the backend block. They mirror the outputs of `infra/bootstrap/`.

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
    key          = "dev/service-discovery/terraform.tfstate"
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
      Module      = "service-discovery"
    }
  }
}
