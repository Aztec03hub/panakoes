locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "dev-vpc-endpoints"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  vpc_id             = data.terraform_remote_state.network.outputs.vpc_id
  vpc_cidr           = data.terraform_remote_state.network.outputs.vpc_cidr_block
  private_subnet_ids = data.terraform_remote_state.network.outputs.private_subnet_ids
  private_route_table_ids = distinct([
    for subnet_id, rt in data.aws_route_table.private : rt.id
  ])

  # Interface endpoint service short names. The full service name is
  # `com.amazonaws.<region>.<short>`. Order is alphabetical for
  # readability; for_each produces an unordered map regardless.
  interface_services = [
    "batch",
    "ec2",
    "ecr.api",
    "ecr.dkr",
    "ecs",
    "elasticloadbalancing",
    "events",
    "kms",
    "lambda",
    "logs",
    "monitoring",
    "secretsmanager",
    "ses",
    "sns",
    "sqs",
    "ssm",
    "sts",
  ]
}

# ---------------------------------------------------------------------------
# Security group for interface endpoints
#
# Interface endpoints attach an ENI per AZ; the SG controls who can
# talk to those ENIs. We allow 443/TCP from the VPC CIDR only, so any
# resource inside `10.10.0.0/16` (ECS tasks, Lambdas, EC2 instances)
# can reach the endpoint, but nothing outside the VPC can. Egress is
# unrestricted, matching AWS guidance: the endpoint ENI talks back to
# the calling client over the established TCP connection, and locking
# down egress on the endpoint side adds no security and risks breaking
# legitimate traffic.
# ---------------------------------------------------------------------------
resource "aws_security_group" "interface_endpoints" {
  name        = "${local.name_prefix}-vpce-interface"
  description = "Allow HTTPS from VPC CIDR to interface VPC endpoints"
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-vpce-interface"
  })
}

resource "aws_vpc_security_group_ingress_rule" "interface_endpoints_https" {
  security_group_id = aws_security_group.interface_endpoints.id

  description = "HTTPS from VPC CIDR to interface endpoint ENIs"
  cidr_ipv4   = local.vpc_cidr
  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443

  tags = local.common_tags
}

resource "aws_vpc_security_group_egress_rule" "interface_endpoints_all" {
  security_group_id = aws_security_group.interface_endpoints.id

  description = "Allow all egress from interface endpoint ENIs"
  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "-1"

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Gateway endpoints (free)
#
# S3 and DynamoDB are the only AWS services that offer gateway
# endpoints. They install a route in each private route table that
# directs the service's prefix list to the endpoint, bypassing the NAT
# entirely. No per-hour charge, no data-processing charge: pure cost
# savings the moment any S3 or DynamoDB traffic flows from the VPC.
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = local.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = local.private_route_table_ids

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-vpce-s3"
  })
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = local.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = local.private_route_table_ids

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-vpce-dynamodb"
  })
}

# ---------------------------------------------------------------------------
# Interface endpoints (paid, $0.01/hr per AZ per endpoint)
#
# Each one creates an ENI in every private subnet, assigns a private
# IP from that subnet's range, and registers a private DNS zone that
# overrides the public AWS service hostname inside the VPC. With
# private_dns_enabled = true, code calling
# `secretsmanager.us-east-1.amazonaws.com` resolves to the endpoint
# ENI rather than the public AWS endpoint, so the request never hits
# the NAT.
#
# The for_each pattern keeps the list of endpoints data-driven; adding
# a service is a one-line change in `local.interface_services`.
# ---------------------------------------------------------------------------
resource "aws_vpc_endpoint" "interface" {
  for_each = toset(local.interface_services)

  vpc_id              = local.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.private_subnet_ids
  security_group_ids  = [aws_security_group.interface_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-vpce-${replace(each.value, ".", "-")}"
  })
}
