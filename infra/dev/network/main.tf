locals {
  azs = ["us-east-1a", "us-east-1b", "us-east-1c"]

  # Public subnets, one per AZ. Slot 0..2 of the /16.
  public_subnets = [
    "10.10.0.0/20",
    "10.10.16.0/20",
    "10.10.32.0/20",
  ]

  # Private subnets, one per AZ. Slot 3..5 of the /16.
  private_subnets = [
    "10.10.48.0/20",
    "10.10.64.0/20",
    "10.10.80.0/20",
  ]

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "dev-network"
  }
}

# Dev VPC + subnets + NAT + IGW + flow logs, built from the community
# terraform-aws-modules/vpc/aws module. The community module is the de
# facto standard; rolling our own VPC primitives would add maintenance
# burden for no architectural benefit.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name = "${var.project_name}-${var.environment}"
  cidr = var.vpc_cidr

  azs             = local.azs
  public_subnets  = local.public_subnets
  private_subnets = local.private_subnets

  enable_dns_hostnames = true
  enable_dns_support   = true

  # NAT Gateway removed 2026-05-14: ECS tasks moved to public subnets
  # with assign_public_ip = true. Saves $32.40/month vs. approximately
  # $14.60/month for public IPs on the running tasks.
  enable_nat_gateway     = false
  single_nat_gateway     = false
  one_nat_gateway_per_az = false

  # Lock down the default security group so no resource accidentally
  # ends up with broad ingress / egress. Services that need network
  # access bring their own security groups.
  manage_default_security_group  = true
  default_security_group_ingress = []
  default_security_group_egress  = []

  # VPC Flow Logs to S3 (log-archive bucket). Shipping flow logs to S3
  # is roughly 2x cheaper than CloudWatch Logs ingestion at our volume
  # and keeps the data Athena-queryable for long-tail forensics.
  # Tier-1 cost cut, 2026-05-18.
  enable_flow_log                   = true
  flow_log_traffic_type             = "ALL"
  flow_log_destination_type         = "s3"
  flow_log_destination_arn          = data.terraform_remote_state.storage.outputs.log_archive_bucket_arn
  flow_log_max_aggregation_interval = 60

  tags = local.common_tags
}
