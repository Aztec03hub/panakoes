output "vpc_id" {
  description = "ID of the dev VPC."
  value       = module.vpc.vpc_id
}

output "vpc_cidr_block" {
  description = "CIDR block of the dev VPC."
  value       = module.vpc.vpc_cidr_block
}

output "public_subnet_ids" {
  description = "IDs of the public subnets, ordered by AZ index (us-east-1a, us-east-1b, us-east-1c)."
  value       = module.vpc.public_subnets
}

output "private_subnet_ids" {
  description = "IDs of the private subnets, ordered by AZ index (us-east-1a, us-east-1b, us-east-1c). Most workloads (ECS tasks, RDS, Lambda) attach here."
  value       = module.vpc.private_subnets
}

output "availability_zones" {
  description = "AZs the VPC spans, in the same order as the public/private subnet ID lists."
  value       = module.vpc.azs
}

output "default_security_group_id" {
  description = "ID of the VPC's default security group, locked down to no inbound and no outbound rules. Resources should attach their own security groups rather than relying on this one."
  value       = module.vpc.default_security_group_id
}

output "nat_gateway_public_ip" {
  description = "Public Elastic IP of the NAT gateway. Null after NAT Gateway removal (2026-05-14)."
  value       = length(module.vpc.nat_public_ips) > 0 ? module.vpc.nat_public_ips[0] : null
}

output "internet_gateway_id" {
  description = "ID of the VPC's Internet Gateway."
  value       = module.vpc.igw_id
}
