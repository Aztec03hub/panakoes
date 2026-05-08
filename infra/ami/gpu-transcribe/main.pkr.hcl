# main.pkr.hcl
#
# Packer template for the Panakoes streaming-transcription GPU AMI.
#
# Source:
#   Latest AWS Deep Learning Base GPU AMI on Ubuntu 22.04 (NVIDIA drivers,
#   CUDA, cuDNN, NCCL, Docker pre-installed). Looked up via the
#   `amazon-ami` data source with `most_recent = true`.
#
# Build host:
#   g4dn.xlarge by default (one T4 GPU, 16 GB VRAM, 4 vCPU). Matches the
#   EC2 instance type we run in production so any CUDA-tooling check or
#   model-load smoke test the provisioners run is exercised on the same
#   hardware family the AMI will boot on.
#
# What goes into the AMI:
#   - Whisper-large-v3 fp16 weights at /opt/whisper/models/large-v3.pt
#     (used by the AWS Batch async-transcription path).
#   - faster-whisper-large CTranslate2 weights at
#     /opt/whisper/models/faster-whisper-large/ (used by the streaming
#     path; faster than vanilla whisper).
#   - Silero VAD weights at /opt/whisper/models/silero-vad.jit (used by
#     the streaming path to gate Whisper inference on speech only).
#   - The panakoes-dev-transcriber-stream container image, pre-pulled
#     into /var/lib/docker so session warmup skips the ECR pull leg.
#
# Tags:
#   Project, Environment, BakedAt, ManagedBy, AmiPurpose are applied to
#   both the AMI and the underlying snapshot. See README for rotation
#   procedure.

packer {
  required_version = ">= 1.10.0"

  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = ">= 1.3.0"
    }
  }
}

# `local` blocks compute derived values once at parse time. `bake_timestamp`
# stamps the AMI name and the BakedAt tag with the same UTC instant so they
# can be cross-referenced in the AWS console without ambiguity.
locals {
  bake_timestamp = formatdate("YYYY-MM-DD'T'hh-mm-ss'Z'", timestamp())
  ami_name       = "${var.ami_name_prefix}-${local.bake_timestamp}"
  ecr_image_uri  = "${var.ecr_account_id}.dkr.ecr.${var.region}.amazonaws.com/${var.ecr_repository_name}:${var.ecr_image_tag}"

  common_tags = {
    Project     = "panakoes"
    Environment = var.environment
    ManagedBy   = "packer"
    AmiPurpose  = "gpu-transcribe"
    BakedAt     = local.bake_timestamp
  }
}

# Resolve the source AMI dynamically. `most_recent = true` plus an owner
# filter and a name pattern means we automatically pick up Deep Learning
# AMI security patches without source edits. To pin a specific release for
# an audit-grade rebuild, override `source_ami_name_filter` with the exact
# AMI name on the command line.
data "amazon-ami" "deep_learning_gpu" {
  filters = {
    name                = var.source_ami_name_filter
    architecture        = "x86_64"
    root-device-type    = "ebs"
    virtualization-type = "hvm"
  }

  owners      = [var.source_ami_owner]
  most_recent = true
  region      = var.region
}

source "amazon-ebs" "gpu_transcribe" {
  region        = var.region
  instance_type = var.instance_type
  ssh_username  = var.ssh_username

  source_ami = data.amazon-ami.deep_learning_gpu.id

  ami_name        = local.ami_name
  ami_description = "Panakoes streaming + batch GPU transcription AMI. Whisper-large-v3 fp16 + faster-whisper-large + Silero VAD pre-baked. Built ${local.bake_timestamp}."

  # Larger root volume than the Deep Learning AMI default. Whisper-large-v3
  # weights are ~3 GB and the transcriber container layers add a few GB more
  # on top of the CUDA toolkit baked into the parent AMI.
  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = var.root_volume_size_gb
    volume_type           = var.root_volume_type
    delete_on_termination = true
    encrypted             = true
  }

  # Encrypt the resulting AMI snapshot at rest. The default AWS-managed
  # `aws/ebs` KMS key is fine for dev; production AMIs should override
  # with `kms_key_id` once a dedicated CMK is provisioned.
  encrypt_boot = true

  tags            = local.common_tags
  snapshot_tags   = local.common_tags
  run_tags        = local.common_tags
  run_volume_tags = local.common_tags
}

build {
  name    = "panakoes-gpu-transcribe"
  sources = ["source.amazon-ebs.gpu_transcribe"]

  # Step 1: model weights. Downloads Whisper-large-v3 fp16, faster-whisper,
  # and Silero VAD; verifies SHA256 against build vars; installs to the
  # canonical paths under /opt/whisper/models.
  provisioner "shell" {
    script = "${path.root}/scripts/install-models.sh"

    environment_vars = [
      "WHISPER_LARGE_V3_URL=${var.whisper_large_v3_url}",
      "WHISPER_LARGE_V3_SHA256=${var.whisper_large_v3_sha256}",
      "WHISPER_INSTALL_PATH=${var.whisper_install_path}",
      "FASTER_WHISPER_URL=${var.faster_whisper_url}",
      "FASTER_WHISPER_SHA256=${var.faster_whisper_sha256}",
      "FASTER_WHISPER_INSTALL_DIR=${var.faster_whisper_install_dir}",
      "SILERO_VAD_URL=${var.silero_vad_url}",
      "SILERO_VAD_SHA256=${var.silero_vad_sha256}",
      "SILERO_VAD_INSTALL_PATH=${var.silero_vad_install_path}",
    ]
  }

  # Step 2: pull the placeholder transcriber container image so session
  # warmup skips the ECR pull leg in production.
  provisioner "shell" {
    script = "${path.root}/scripts/install-runtime.sh"

    environment_vars = [
      "ECR_ACCOUNT_ID=${var.ecr_account_id}",
      "ECR_IMAGE_URI=${local.ecr_image_uri}",
      "AWS_REGION=${var.region}",
    ]
  }

  # Step 3: CUDA tooling sanity check. Confirms the provisioner instance
  # has a working GPU and that nvidia-smi reports a CUDA runtime version.
  # If this fails the build aborts; a broken AMI is worse than no AMI.
  provisioner "shell" {
    inline = [
      "set -euo pipefail",
      "echo 'Checking nvidia-smi availability...'",
      "nvidia-smi",
      "echo 'Checking CUDA runtime...'",
      "nvcc --version || echo 'nvcc not in PATH; relying on container CUDA runtime'",
      "echo 'CUDA tooling check complete.'",
    ]
  }
}
