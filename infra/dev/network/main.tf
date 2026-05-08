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
  version = "~> 5.21"

  name = "${var.project_name}-${var.environment}"
  cidr = var.vpc_cidr

  azs             = local.azs
  public_subnets  = local.public_subnets
  private_subnets = local.private_subnets

  enable_dns_hostnames = true
  enable_dns_support   = true

  # Single NAT in us-east-1a is a deliberate cost choice for dev. With
  # one NAT per AZ we'd pay ~$96/mo for HA we do not need at this
  # stage. Production should flip single_nat_gateway = false and
  # one_nat_gateway_per_az = true.
  enable_nat_gateway     = true
  single_nat_gateway     = true
  one_nat_gateway_per_az = false

  # Lock down the default security group so no resource accidentally
  # ends up with broad ingress / egress. Services that need network
  # access bring their own security groups.
  manage_default_security_group  = true
  default_security_group_ingress = []
  default_security_group_egress  = []

  # VPC Flow Logs to CloudWatch Logs. CloudWatch is cheaper than S3
  # for our current low log volume; revisit if traffic grows enough
  # that S3 + Athena becomes the better cost story.
  enable_flow_log                                 = true
  create_flow_log_cloudwatch_log_group            = true
  create_flow_log_cloudwatch_iam_role             = true
  flow_log_traffic_type                           = "ALL"
  flow_log_destination_type                       = "cloud-watch-logs"
  flow_log_cloudwatch_log_group_retention_in_days = 30
  flow_log_max_aggregation_interval               = 60

  tags = local.common_tags
}
