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
  description = "AMI ID for the AWS Batch GPU compute environment. The default is AWS's stock Deep Learning Base GPU AMI (Ubuntu 22.04, NVIDIA drivers + CUDA + Docker pre-installed) so the batch resources land in Terraform state without needing the bespoke Whisper-baked AMI yet. The bespoke AMI from infra/ami/gpu-transcribe/ Packer build replaces this default in a one-line follow-up PR after the AWS GPU vCPU quota approves and the bake completes; spot launches will fail with this stock AMI because Whisper weights and the transcriber container are not pre-baked, but the resource definitions and compute environment validate cleanly."
  type        = string
  default     = "ami-091f07e77f51e6b42"
}
