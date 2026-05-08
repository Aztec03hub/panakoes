variable "aws_region" {
  description = "AWS region for the dev environment Batch resources."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name used for tagging and resource naming."
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used for resource naming and tagging."
  type        = string
  default     = "panakoes"
}

variable "gpu_ami_id" {
  description = "AMI ID used by the Batch GPU compute environment. Placeholder until the GPU AMI Packer build lands; replace with the published AMI ID once the streaming/transcription AMI Terraform module ships."
  type        = string
  default     = "ami-PLACEHOLDER"

  # TODO: wire to the AMI ID emitted by the upcoming Packer + AMI
  # Terraform module. Until then, `terraform apply` is intentionally
  # blocked because Batch will reject the placeholder value at
  # CreateComputeEnvironment time.
}
