# Dev Environment Network

Per-environment Terraform configuration creating the VPC and core
networking primitives for the Panakoes `dev` environment. This is the
first config to consume the S3 remote state backend created by
`infra/bootstrap/`.

## What this creates

- VPC `panakoes-dev` with CIDR `10.10.0.0/16`
- 3 public subnets, one per AZ (`us-east-1a/b/c`)
- 3 private subnets, one per AZ
- Internet Gateway for public subnet egress
- Single NAT Gateway in `us-east-1a` for private subnet egress
- Locked-down default security group (no inbound, no outbound)
- VPC Flow Logs to CloudWatch Logs with 30-day retention

The VPC is built from the community
[terraform-aws-modules/vpc/aws](https://registry.terraform.io/modules/terraform-aws-modules/vpc/aws/latest)
module pinned to `~> 5.21`. Pinning to the v5 line keeps the module
compatible with our root AWS provider pin of `~> 5.0`; v6 of the
module requires AWS provider 6.28 or higher.

## CIDR plan

The 10.10 prefix distinguishes this VPC from staging and prod when (or
if) they land in the same account.

| Environment | VPC CIDR      |
|-------------|---------------|
| dev         | 10.10.0.0/16  |
| staging     | 10.20.0.0/16  |
| prod        | 10.30.0.0/16  |

Per-environment subnet layout inside the /16:

| Tier    | AZ a            | AZ b             | AZ c             |
|---------|-----------------|------------------|------------------|
| public  | 10.10.0.0/20    | 10.10.16.0/20    | 10.10.32.0/20    |
| private | 10.10.48.0/20   | 10.10.64.0/20    | 10.10.80.0/20    |

Each /20 holds 4,096 addresses, plenty for ECS tasks, RDS, Lambda ENIs,
and Batch GPU instances combined.

## Single NAT vs multi-AZ NAT

Dev runs a single NAT Gateway in `us-east-1a`. NAT Gateways cost about
$32/month plus per-GB data processing; one NAT per AZ would be roughly
$96/month for HA we do not need at dev volumes.

For production, flip these in `main.tf`:

    single_nat_gateway     = false
    one_nat_gateway_per_az = true

The trade-off is fault tolerance: if `us-east-1a` has an outage, the
dev VPC's private subnets in `b` and `c` lose internet egress until
the AZ recovers. Acceptable for dev, not for production.

## Apply

    cd infra/dev/network
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS provider and the VPC module, then
initializes the S3 backend (talks to the bucket created by
`infra/bootstrap/`). It will fail without AWS credentials, hence the
`AWS_PROFILE=lafayettelabs` prefix.

## Consuming outputs from other configs

Downstream configurations (ECS cluster, RDS instance, Lambda VPC
config, Batch GPU compute environment) read this VPC's IDs and CIDRs
via a `terraform_remote_state` data source pointing at the same
backend bucket and the `dev/network/terraform.tfstate` key:

    data "terraform_remote_state" "network" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/network/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.network.outputs.vpc_id
    #   data.terraform_remote_state.network.outputs.private_subnet_ids
    #   data.terraform_remote_state.network.outputs.nat_gateway_public_ip

## VPC endpoints (deferred)

Not included in v0.1 to keep dev costs low. Each Interface endpoint is
about $7/month per AZ; gateway endpoints (S3 and DynamoDB) are free.

When NAT bandwidth becomes a real cost concern (likely once the
async transcription pipeline starts pulling Whisper container layers
from ECR through the NAT), add Interface endpoints for:

- `secretsmanager`
- `kms`
- `ecr.api` and `ecr.dkr`
- `logs` (CloudWatch Logs)

And add free Gateway endpoints for `s3` and `dynamodb` whenever those
services join the dev environment.

## Outputs

| Output                      | Type   | Purpose                                                               |
|-----------------------------|--------|-----------------------------------------------------------------------|
| `vpc_id`                    | string | VPC ID                                                                |
| `vpc_cidr_block`            | string | VPC CIDR                                                              |
| `public_subnet_ids`         | list   | Three public subnet IDs in AZ order                                   |
| `private_subnet_ids`        | list   | Three private subnet IDs in AZ order                                  |
| `availability_zones`        | list   | AZ names matching the subnet ordering                                 |
| `default_security_group_id` | string | Locked-down default SG; resources should bring their own              |
| `nat_gateway_public_ip`     | string | NAT EIP, useful for third-party IP allowlisting                       |
| `internet_gateway_id`       | string | IGW ID                                                                |
