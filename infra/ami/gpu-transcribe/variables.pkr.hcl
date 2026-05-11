# variables.pkr.hcl
#
# All inputs to the gpu-transcribe Packer build are var-driven so the
# template can be reused across regions, model versions, and ECR
# accounts without source edits. No URLs, accounts, or checksums are
# hardcoded.
#
# At build time, supply values via:
#   - environment variables (PKR_VAR_<name>)
#   - a `*.pkrvars.hcl` file passed with `-var-file=...`
#   - inline `-var name=value` flags
#
# See README.md for the recommended invocation.

variable "region" {
  type        = string
  description = "AWS region in which to build the AMI."
  default     = "us-east-1"
}

variable "ami_name_prefix" {
  type        = string
  description = "Prefix prepended to the AMI name. Final name is `<prefix>-<unix-timestamp>`."
  default     = "panakoes-gpu-transcribe"
}

variable "spot_instance_types" {
  type        = list(string)
  description = "Ordered preference list of EC2 instance types for the Spot build host. Must all have CUDA-capable GPUs so model load + tooling check can run during provisioning. Packer attempts each in order until a Spot capacity request fulfils; this matters because Spot capacity for any single G-family type fluctuates by AZ minute-to-minute. Default keeps g4dn.xlarge as the preferred bid (matches runtime instance type, cheapest in family) with g4dn.2xlarge as a fallback for AZ-level capacity shortages."
  default     = ["g4dn.xlarge", "g4dn.2xlarge"]
}

variable "ssh_username" {
  type        = string
  description = "SSH username on the source AMI. Deep Learning AMI ships with `ubuntu`."
  default     = "ubuntu"
}

variable "root_volume_size_gb" {
  type        = number
  description = "Root EBS volume size in GiB. Whisper-large-v3 fp16 weights plus faster-whisper plus container layers fit comfortably under 100 GiB."
  default     = 100
}

variable "root_volume_type" {
  type        = string
  description = "EBS volume type for the AMI root device."
  default     = "gp3"
}

variable "environment" {
  type        = string
  description = "Environment tag. Default `dev`; bump to `prod` for production-promoted AMIs."
  default     = "dev"
}

# ---------------------------------------------------------------------------
# Source AMI selection
# ---------------------------------------------------------------------------
#
# We base on the AWS Deep Learning AMI GPU PyTorch (Ubuntu 22.04). It already
# ships NVIDIA drivers, CUDA, cuDNN, NCCL, and Docker, which keeps our
# provisioner work focused on application bits (model weights + runtime).
#
# Resolved at build time via the `most_recent = true` data source filter in
# main.pkr.hcl. The filter pattern is overridable so we can pin a specific
# Deep Learning AMI release if reproducibility is required for an audit run.

variable "source_ami_owner" {
  type        = string
  description = "AWS account that owns the source AMI. `898082745236` is the official Deep Learning AMI publisher."
  default     = "898082745236"
}

variable "source_ami_name_filter" {
  type        = string
  description = "Name pattern for the source AMI. Default selects the latest Deep Learning Base GPU AMI on Ubuntu 22.04."
  default     = "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*"
}

# ---------------------------------------------------------------------------
# Whisper-large-v3 fp16 model weights (batch path)
# ---------------------------------------------------------------------------

variable "whisper_large_v3_url" {
  type        = string
  description = "URL to download the Whisper-large-v3 fp16 weights from. Typically a public Hugging Face mirror or an internal S3 object."
}

variable "whisper_large_v3_sha256" {
  type        = string
  description = "Expected SHA256 of the Whisper-large-v3 fp16 weights file. Verified during install."
}

variable "whisper_install_path" {
  type        = string
  description = "Absolute path on the AMI where the Whisper-large-v3 weights file is installed."
  default     = "/opt/whisper/models/large-v3.pt"
}

# ---------------------------------------------------------------------------
# faster-whisper-large model weights (streaming path)
# ---------------------------------------------------------------------------

variable "faster_whisper_url" {
  type        = string
  description = "URL to download the faster-whisper-large CTranslate2 weights tarball. Typically a public Hugging Face mirror or an internal S3 object."
}

variable "faster_whisper_sha256" {
  type        = string
  description = "Expected SHA256 of the faster-whisper-large weights tarball. Verified during install."
}

variable "faster_whisper_install_dir" {
  type        = string
  description = "Absolute directory on the AMI where the faster-whisper-large weights are unpacked."
  default     = "/opt/whisper/models/faster-whisper-large"
}

# ---------------------------------------------------------------------------
# Silero VAD weights (streaming path)
# ---------------------------------------------------------------------------

variable "silero_vad_url" {
  type        = string
  description = "URL to download the Silero VAD model weights from. Typically the official Silero GitHub release asset."
}

variable "silero_vad_sha256" {
  type        = string
  description = "Expected SHA256 of the Silero VAD model file. Verified during install."
}

variable "silero_vad_install_path" {
  type        = string
  description = "Absolute path on the AMI where the Silero VAD weights are installed."
  default     = "/opt/whisper/models/silero-vad.jit"
}

# ---------------------------------------------------------------------------
# Panakoes transcriber runtime container
# ---------------------------------------------------------------------------
#
# The transcriber-stream image lives in the per-environment ECR repository
# (see infra/dev/ecr). At AMI bake time we `docker pull` the image so it sits
# in /var/lib/docker, eliminating the cold-image-pull leg of session warmup.
# When the image does not yet exist (early scaffolding, fresh accounts) the
# install script logs the failure and continues; the AMI still bakes.

variable "ecr_account_id" {
  type        = string
  description = "AWS account ID that hosts the transcriber-stream ECR repository."
}

variable "ecr_image_tag" {
  type        = string
  description = "Tag of the transcriber-stream image to bake into the AMI."
  default     = "latest"
}

variable "ecr_repository_name" {
  type        = string
  description = "ECR repository name for the streaming transcriber container image."
  default     = "panakoes-dev-transcriber-stream"
}
