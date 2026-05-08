# Dev Environment ECR Repositories

Per-environment Terraform configuration creating the Elastic Container
Registry repositories that back every Panakoes microservice container
image for the `dev` environment. Consumes the S3 remote state backend
created by `infra/bootstrap/`; state lives at
`dev/ecr/terraform.tfstate`.

## What this creates

One AWS KMS customer-managed key (alias `alias/panakoes-dev-ecr`,
rotation enabled, 7-day deletion window) and 11 ECR repositories, one
per current and near-future Panakoes service. Each repository:

- Name: `panakoes-dev-<service>`
- Image tag mutability: `IMMUTABLE` (a given tag always points to the
  same image bytes; prevents silent overwrites)
- Scan on push: enabled (ECR basic scanning, free, surfaces CVEs
  immediately)
- Encryption: KMS with the shared dev ECR CMK
- Lifecycle policy: keep last 10 tagged images, expire untagged after
  14 days

### Repository list

| Service | Repository |
|---|---|
| auth | `panakoes-dev-auth` |
| billing | `panakoes-dev-billing` |
| event-router | `panakoes-dev-event-router` |
| gpu-spawner | `panakoes-dev-gpu-spawner` |
| ingestion-api | `panakoes-dev-ingestion-api` |
| notification | `panakoes-dev-notification` |
| query-api | `panakoes-dev-query-api` |
| session-manager | `panakoes-dev-session-manager` |
| summarization | `panakoes-dev-summarization` |
| transcriber-batch | `panakoes-dev-transcriber-batch` |
| transcriber-stream | `panakoes-dev-transcriber-stream` |

## Why a single shared CMK instead of per-repository keys

Per-repo CMKs would isolate blast radius for key rotation or grant
changes, but ECR images are application binaries, not regulated PII or
payment data. A shared key keeps cost flat (about $1/month total
instead of about $11/month across 11 repos) and simplifies IAM grants
for cross-service consumers (ECS task execution roles, the
gpu-spawner's EC2 instance role). If a future threat model demands
per-repo isolation we can split keys later by importing each repo
under a new key resource.

## Why `IMMUTABLE` tag mutability

Tag mutability `MUTABLE` lets a tag silently change content under
callers, which is a supply-chain ambiguity we do not want in any
environment. `IMMUTABLE` requires every push of a given tag to be a
new tag string (for example `v0.4.2-rc1` instead of overwriting
`v0.4.2`), so a SHA-pinned or tag-pinned deploy is reproducible.

## Why `scan_on_push`

Free baseline CVE detection. ECR enhanced scanning (Inspector v2) is
deferred; we can flip it on later via an
`aws_ecr_registry_scanning_configuration` resource without touching
these repositories.

## Why this lifecycle policy

`Keep last 10 tagged` lets us roll back to any of the previous 10
releases without deciding now what the right deeper history is.
`Expire untagged after 14 days` cleans up orphan layers from rebuilds
and failed pushes; 14 days is a safe window for any in-flight
investigation that might still need them. Together these bound
storage cost without losing rollback flexibility.

## No apply yet

This module has not been applied. The `terraform init` step has been
run to produce the lock file; `terraform plan` and `terraform apply`
require AWS credentials and remain a deliberate follow-up before any
service starts pushing images.

## Apply (when ready)

    cd infra/dev/ecr
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

## Variables

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `aws_region` | string | `us-east-1` | AWS region for the ECR repositories |
| `environment` | string | `dev` | Environment name used for tagging and resource naming |
| `project_name` | string | `panakoes` | Project name used for resource naming and tagging |

## Outputs

| Output | Type | Purpose |
|---|---|---|
| `repository_urls` | map(string) | Service name to ECR repository URL (CI push target) |
| `repository_arns` | map(string) | Service name to ECR repository ARN (IAM policy resource) |
| `kms_key_arn` | string | ARN of the shared dev ECR CMK (IAM policy `kms:Decrypt` resource) |

## Consuming outputs from other configs

Downstream configurations (ECS task definitions, gpu-spawner IAM
policy, CI deploy roles) read the repository URLs, ARNs, and KMS key
ARN via a `terraform_remote_state` data source pointing at the same
backend bucket and the `dev/ecr/terraform.tfstate` key:

    data "terraform_remote_state" "ecr" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/ecr/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.ecr.outputs.repository_urls["auth"]
    #   data.terraform_remote_state.ecr.outputs.repository_arns["ingestion-api"]
    #   data.terraform_remote_state.ecr.outputs.kms_key_arn
