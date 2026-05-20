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
  description = "AMI ID for the AWS Batch GPU compute environment. Default `ami-0b729f3f75a1074c4` is the AWS ECS-Optimized GPU AMI (amzn2-ami-ecs-gpu-hvm-2.0.20260514) which ships the ECS agent + NVIDIA driver + CUDA + Docker pre-installed. REQUIRED because AWS Batch places jobs via ECS; the host AMI must have the ECS agent registering with the Batch-internal ECS cluster. The earlier bespoke `gpu-transcribe` AMI (ami-0dee04ee5042c94cf) was based on the AWS Deep Learning Base GPU AMI which has NVIDIA drivers but NO ECS agent; jobs sat indefinitely in RUNNABLE because no ECS instance ever registered. Whisper-large-v3 fp16 weights download on first container run instead of being AMI-baked; the optimization to pre-bake weights at `/opt/whisper/models/large-v3.pt` remains additive (transcriber-batch's load_model honors the path when the file exists). Look up future AMI updates: `aws ssm get-parameter --name /aws/service/ecs/optimized-ami/amazon-linux-2/gpu/recommended`."
  type        = string
  default     = "ami-0b729f3f75a1074c4"
}
