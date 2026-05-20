# variables.pkr.hcl
#
# All inputs to the whisper-stream Packer build are var-driven so the
# template can be reused across regions, model variants, and HF
# repositories without source edits. No URLs, accounts, or checksums
# are hardcoded.
#
# At build time, supply values via:
#   - environment variables (PKR_VAR_<name>)
#   - a `*.pkrvars.hcl` file passed with `-var-file=...`
#   - inline `-var name=value` flags
#
# Unlike gpu-transcribe, this template uses `huggingface-cli download`
# rather than direct URL + SHA256 verification. Rationale: the
# Systran/faster-whisper-large-v2 repo is a directory with multiple
# files (model.bin, config.json, tokenizer.json, vocabulary.txt) whose
# layout the HF CLI knows; a tarball-and-SHA approach would either
# require us to maintain a derived tarball in S3 (extra ops burden) or
# would force per-file URL + SHA pinning (verbose). HF model repos are
# content-addressed by revision SHA, which is the integrity anchor we
# care about; pin via `hf_model_revision` if a specific revision is
# required for reproducibility.

variable "region" {
  type        = string
  description = "AWS region in which to build the AMI."
  default     = "us-east-1"
}

variable "ami_name_prefix" {
  type        = string
  description = "Prefix prepended to the AMI name. Final name is `<prefix>-<bake-timestamp>`."
  default     = "panakoes-whisper-stream"
}

variable "spot_instance_types" {
  type        = list(string)
  description = "Ordered preference list of EC2 instance types for the Spot build host. Must all have CUDA-capable GPUs so the model-load sanity check can run during provisioning. Default keeps g4dn.xlarge as the preferred bid (matches runtime instance type, cheapest in family) with g4dn.2xlarge as a fallback for AZ-level capacity shortages."
  default     = ["g4dn.xlarge", "g4dn.2xlarge"]
}

variable "ssh_username" {
  type        = string
  description = "SSH username on the source AMI. Deep Learning AMI ships with `ubuntu`."
  default     = "ubuntu"
}

variable "root_volume_size_gb" {
  type        = number
  description = "Root EBS volume size in GiB. The Deep Learning Base GPU AMI source snapshot is ~75 GiB on its own (driver + CUDA + cuDNN + Docker), so the launch_block_device_mappings volume must be at least that large or Spot fleet creation fails with `Volume of size NGB is smaller than snapshot`. faster-whisper-large-v2 CT2 weights add ~3 GB on top; 100 GiB matches the gpu-transcribe template's choice and gives comfortable margin for future Deep Learning AMI growth."
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
# provisioner work focused on application bits (model weights + warmup clip).

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
# Model bake (streaming path)
# ---------------------------------------------------------------------------
#
# The container's startup assertion in services/transcriber-stream/main.py
# checks for two paths:
#   1. f"{MODEL_CACHE_DIR}/{MODEL_SIZE}-ct2"  (the CT2 model directory)
#   2. "/opt/whisper/warmup-1s.wav"           (the warmup clip)
#
# The variables below MUST keep these paths in sync with the container's
# config defaults (MODEL_CACHE_DIR=/opt/whisper/models, MODEL_SIZE=large-v2)
# or the AMI bake and the container's assertion will disagree at boot.

variable "model_cache_dir" {
  type        = string
  description = "Absolute parent directory under which the CT2 model directory is placed. Matches the container's MODEL_CACHE_DIR env-var default. Combined with `model_size`, the final install path is `$${model_cache_dir}/$${model_size}-ct2`."
  default     = "/opt/whisper/models"
}

variable "model_size" {
  type        = string
  description = "faster-whisper model variant. MUST match the container's MODEL_SIZE env-var default. Architect MUST-02: large-v2 (default) hallucinates less on silence than large-v3; LocalAgreement-2 mitigates but v2 is the safer default for live partials."
  default     = "large-v2"
}

variable "warmup_clip_path" {
  type        = string
  description = "Absolute path on the AMI where the 1-second silent 16 kHz mono PCM WAV warmup clip is generated. Matches the container's hardcoded `warmup_clip_path` property in config.py. Used by upstream's `warmup_asr()` to load the model graph onto the GPU before the first real frame arrives."
  default     = "/opt/whisper/warmup-1s.wav"
}

variable "hf_model_repo" {
  type        = string
  description = "HuggingFace model repository to download via `huggingface-cli download`. Default `Systran/faster-whisper-large-v2` is the canonical CTranslate2 conversion of the OpenAI Whisper-large-v2 PT weights. Override only when pinning a fork or a different model variant."
  default     = "Systran/faster-whisper-large-v2"
}
