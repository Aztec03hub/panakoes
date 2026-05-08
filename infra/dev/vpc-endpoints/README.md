# Dev Environment VPC Endpoints

Per-environment Terraform configuration that attaches VPC endpoints
to the dev VPC's private subnets. Two purposes: cost (route AWS API
traffic off the NAT to avoid data-processing charges) and security
(traffic to AWS services stays inside the VPC, never traversing the
public internet).

## What this creates

Gateway endpoints (free, no per-hour or data-processing charge):

- `com.amazonaws.us-east-1.s3`
- `com.amazonaws.us-east-1.dynamodb`

Both attach to all three private route tables and add a route entry
that directs the service prefix list to the endpoint instead of the
NAT.

Interface endpoints (paid, about $0.01/hr per AZ per endpoint):

- `com.amazonaws.us-east-1.secretsmanager`
- `com.amazonaws.us-east-1.ssm`
- `com.amazonaws.us-east-1.kms`
- `com.amazonaws.us-east-1.ecr.api`
- `com.amazonaws.us-east-1.ecr.dkr`
- `com.amazonaws.us-east-1.logs`
- `com.amazonaws.us-east-1.events`
- `com.amazonaws.us-east-1.sns`
- `com.amazonaws.us-east-1.sqs`
- `com.amazonaws.us-east-1.sts`

Each interface endpoint creates an ENI in every private subnet,
attaches the shared security group `panakoes-dev-vpce-interface`, and
enables private DNS. Application code can keep calling
`secretsmanager.us-east-1.amazonaws.com` (or any AWS SDK default
endpoint) and traffic resolves to the in-VPC ENI automatically.

A single security group fronts all ten interface endpoints. It
allows inbound 443/TCP from the VPC CIDR (`10.10.0.0/16`) only and
permits all egress (the endpoint ENI replies on the established TCP
connection).

## Why these endpoints, in this order

The selection covers every AWS service the dev microservices touch
during normal operation:

| Endpoint        | Used by                                                                  |
|-----------------|--------------------------------------------------------------------------|
| s3              | Ingestion API, transcription pipeline, log archive, deployment artifacts |
| dynamodb        | Every service that writes the audit log; ingestion records; sessions    |
| secretsmanager  | Every service at startup (JWT signing key, DB URL, Stripe, Anthropic)   |
| ssm             | Future Parameter Store reads; SSM Run Command; Session Manager           |
| kms             | Decrypt for SSE-KMS S3 reads, DynamoDB encryption, Secrets Manager      |
| ecr.api         | ECR control-plane: GetAuthorizationToken, BatchGetImage                 |
| ecr.dkr         | ECR data-plane: docker pull (image layers)                              |
| logs            | CloudWatch Logs PutLogEvents (every service writes structured logs)     |
| events          | EventBridge PutEvents from ingestion / billing / event-router           |
| sns             | Notifications fanout to email / push                                    |
| sqs             | Queue-based decoupling between ingestion and transcription              |
| sts             | AssumeRole on every IAM role (every service hits STS at boot)           |

## Cost rationale

NAT Gateway data-processing is $0.045 per GB. The dev VPC currently
runs a single NAT in `us-east-1a`. Without these endpoints, every
ECR image pull, CloudWatch log shipment, Secrets Manager fetch, and
DynamoDB call from the private subnets pays that per-GB toll.

Ten interface endpoints at three AZs and $0.01/hr is roughly $220 per
month fixed cost. The break-even versus NAT data-processing is about
4.9 TB of avoided NAT egress per month, but the more interesting
math is on container pulls: a single 5 GB Whisper-large container
image pulled by 10 GPU instances per day is 1.5 TB per month of NAT
egress eliminated, and that is just one workload.

Gateway endpoints (S3, DynamoDB) are free and pay back immediately,
so they are unconditional.

For dev specifically, the security benefit (every AWS API call stays
private, never traverses the public internet) is worth the fixed
cost on its own. The portfolio value (this is the textbook
interview answer to "how do you reduce VPC egress costs and tighten
network security?") makes it a yes.

## Architecture details

### Private DNS

`private_dns_enabled = true` on each interface endpoint installs a
Route53 private hosted zone in the VPC. The zone overrides the public
AWS service hostname so that, for example,
`secretsmanager.us-east-1.amazonaws.com` resolves to the endpoint
ENI's private IP rather than the public AWS endpoint. This is the
critical bit that makes the endpoints transparent to application
code: nothing in the SDK or the boto3/aws-sdk-v2 configuration needs
to change.

The VPC must have `enable_dns_hostnames` and `enable_dns_support`
both set to true, which the `dev/network/` module already does.

### Security group

A single SG fronts all interface endpoints rather than one SG per
endpoint. This trades fine-grained per-service network isolation for
operational simplicity: all the endpoints share the same blast radius
(any client in the VPC can talk to any of them) and IAM is the layer
that enforces which service can call which API. IAM is more
expressive than network rules for this use case (least-privilege per
action and per resource ARN), so concentrating network controls at
the VPC-CIDR level keeps the SG count low without weakening the
security posture.

### Route tables for gateway endpoints

The community VPC module creates one private route table per private
subnet. Rather than adding a `private_route_table_ids` output to
`dev/network/` (which would couple the module change to this PR),
this config discovers the route tables at plan time via
`data "aws_route_table"` filtered by each `private_subnet_id`. When
the network module eventually exposes that output we can drop the
data lookup and reference the output directly.

## Apply

    cd infra/dev/vpc-endpoints
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS provider and initializes the S3
backend. The config reads the `dev/network/` state via
`terraform_remote_state`, so the network module must be applied first
(it already is in the standard rollout order).

## Consuming outputs from other configs

Most callers will not need to reference these endpoints directly:
private DNS makes them transparent to application code. The outputs
are available for cases where they help, such as endpoint-policy
attachment or CloudWatch alarms keyed by endpoint:

    data "terraform_remote_state" "vpc_endpoints" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/vpc-endpoints/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.vpc_endpoints.outputs.s3_endpoint_id
    #   data.terraform_remote_state.vpc_endpoints.outputs.interface_endpoint_ids["secretsmanager"]
    #   data.terraform_remote_state.vpc_endpoints.outputs.interface_endpoints_security_group_id

## Verifying private DNS is working

After apply, from any EC2 instance / ECS task in a private subnet:

    dig +short secretsmanager.us-east-1.amazonaws.com

The response should be a `10.10.x.x` private IP (one of the endpoint
ENIs), not a public AWS IP. If it returns a public IP, double-check
that the VPC has DNS hostnames + DNS support enabled and that the
endpoint shows `Private DNS Name: enabled` in the AWS console.

## Outputs

| Output                                  | Type   | Purpose                                                |
|-----------------------------------------|--------|--------------------------------------------------------|
| `s3_endpoint_id`                        | string | S3 gateway endpoint ID                                 |
| `dynamodb_endpoint_id`                  | string | DynamoDB gateway endpoint ID                           |
| `interface_endpoint_ids`                | map    | Service short name -> interface endpoint ID           |
| `interface_endpoint_dns_entries`        | map    | Service short name -> list of endpoint DNS entries     |
| `interface_endpoints_security_group_id` | string | SG attached to every interface endpoint ENI            |
