variable "aws_region" {
  description = "AWS region for the dev environment Batch resources."
  type        = string
  default     = "us-east-1"
}

variable "transcriber_batch_image_tag" {
  description = "Container image tag (in the panakoes-dev-transcriber-batch ECR repo) the Batch job definition runs. ECR repo is immutable-tagged; do NOT pass `latest` unless you also delete + re-tag the existing :latest. Default `whisper-ingestion-20260520-023411` is the first self-contained Whisper-large-v3 fp16 image (bundles openai-whisper + torch+cu124, supports TARGET_MODE=ingestion). Update on each container rebuild."
  type        = string
  default     = "whisper-ingestion-20260520-023411"
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
  description = "AMI ID for the AWS Batch GPU compute environment. Points at the bespoke gpu-transcribe AMI baked by infra/ami/gpu-transcribe/ (Packer). The AMI pre-loads Whisper-large-v3 fp16 weights, faster-whisper-large CT2 weights, Silero VAD, the NVIDIA driver + CUDA + Docker stack, and the transcriber-stream container so streaming session warmup is bounded to driver init + container start. Rotate via the docs/runbooks/gpu-ami-bake.md procedure when refreshing model weights or the Deep Learning AMI source. First successful bake (2026-05-11) is ami-0dee04ee5042c94cf, tagged Project=panakoes / Component=gpu-transcribe / Environment=dev."
  type        = string
  default     = "ami-0dee04ee5042c94cf"
}
