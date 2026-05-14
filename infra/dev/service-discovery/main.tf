resource "aws_service_discovery_private_dns_namespace" "this" {
  name = "panakoes-dev.local"
  vpc  = data.terraform_remote_state.network.outputs.vpc_id

  description = "ECS Service Connect namespace for panakoes-dev cluster. Services register as <service>.panakoes-dev.local:<port>."
}
