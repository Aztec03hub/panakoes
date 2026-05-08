# Bootstrap module providers.
#
# IMPORTANT: This module deliberately uses LOCAL Terraform state.
# It cannot use the S3 backend because the bucket the S3 backend would
# use does not exist yet; this module is what creates it. After this
# module is applied once, every other Terraform configuration in the
# repository uses the S3 backend defined by the resources here.
#
# The local state file (terraform.tfstate) is git-ignored at the repo
# root and lives only on Phil's machine. See README.md in this
# directory for the disaster recovery plan.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = "shared"
      ManagedBy   = "terraform"
      Module      = "bootstrap"
    }
  }
}
