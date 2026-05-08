# Terraform Bootstrap

Creates the foundational infra that the rest of the panakoes Terraform
depends on: S3 state bucket, KMS key, DynamoDB lock table.

## One-time apply

This module uses LOCAL Terraform state (deliberately). The state for
this module lives on Phil's machine in
`infra/bootstrap/terraform.tfstate` and is NEVER committed (already
covered by `.gitignore` at the repo root).

Run from this directory:

    export AWS_PROFILE=lafayettelabs
    aws sts get-caller-identity   # confirm you are the phil IAM user
    terraform init
    terraform plan                # review carefully
    terraform apply               # type yes when prompted

After apply, save the outputs:

    terraform output

Use the bucket name, table name, and KMS key ARN to populate the
`backend "s3"` block in `infra/<env>/providers.tf` for every other
Terraform configuration in this repo.

## Why local state for bootstrap

The S3 backend for Terraform state cannot exist before something
creates it. The bootstrap module is the chicken; all other Terraform
configs are the eggs that depend on the bucket existing.

## Disaster recovery

If `infra/bootstrap/terraform.tfstate` is lost:
- The bucket and lock table still exist (Terraform losing state does
  not destroy the resources).
- Recreate by `terraform import` of each resource into a fresh state
  file using the names from the AWS console.
- Or: destroy the (empty) bootstrap-managed resources from the
  console and re-run terraform apply.
