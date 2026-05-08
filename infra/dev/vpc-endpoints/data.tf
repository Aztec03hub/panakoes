# Network module remote state: VPC ID, CIDR, and the private subnets
# we attach interface endpoints to. The `dev/network/` module already
# exists and outputs these via terraform_remote_state.
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/network/terraform.tfstate"
    region = "us-east-1"
  }
}

# Discover the route tables associated with each private subnet so we
# can attach the S3 and DynamoDB gateway endpoints to all of them.
# `dev/network/` does not currently expose `private_route_table_ids`
# as an output, so we look them up at plan time by filtering on the
# subnet associations. The community VPC module creates one route
# table per private subnet (single NAT mode still uses three private
# RTs, all routing 0.0.0.0/0 at the same NAT). When the network module
# adds a `private_route_table_ids` output we can replace this lookup
# with the direct reference.
data "aws_route_table" "private" {
  for_each = toset(data.terraform_remote_state.network.outputs.private_subnet_ids)

  subnet_id = each.value
}
