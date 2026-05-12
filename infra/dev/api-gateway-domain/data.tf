# ---------------------------------------------------------------------------
# API Gateway module remote state
#
# Provides the HTTP API ID and the stage name the mapping attaches to.
# Required when `enable_domain_mapping = true`; harmless during the
# cert-only first apply because Terraform reads remote state lazily.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "api_gateway" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/api-gateway/terraform.tfstate"
    region = "us-east-1"
  }
}
