output "s3_endpoint_id" {
  description = "ID of the S3 gateway VPC endpoint."
  value       = aws_vpc_endpoint.s3.id
}

output "dynamodb_endpoint_id" {
  description = "ID of the DynamoDB gateway VPC endpoint."
  value       = aws_vpc_endpoint.dynamodb.id
}

output "interface_endpoint_ids" {
  description = "Map of interface VPC endpoint short service name (`secretsmanager`, `ecr.api`, etc) to endpoint ID. Use to attach endpoint policies or to wire CloudWatch alarms keyed by endpoint."
  value       = { for k, v in aws_vpc_endpoint.interface : k => v.id }
}

output "interface_endpoint_dns_entries" {
  description = "Map of interface VPC endpoint short service name to the list of DNS entries the endpoint exposes. Useful for debugging private-DNS resolution."
  value       = { for k, v in aws_vpc_endpoint.interface : k => v.dns_entry }
}

output "interface_endpoints_security_group_id" {
  description = "ID of the security group attached to every interface endpoint ENI. Allows 443/TCP from the VPC CIDR."
  value       = aws_security_group.interface_endpoints.id
}
