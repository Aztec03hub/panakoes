data "terraform_remote_state" "network" {
  backend = "s3"
  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/network/terraform.tfstate"
    region = "us-east-1"
  }
}
