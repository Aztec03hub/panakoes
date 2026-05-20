# main.pkr.hcl
#
# Packer template for the Panakoes streaming-transcription "whisper-stream" AMI.
#
# This template is the streaming-path-specific successor to
# `infra/ami/gpu-transcribe/`. The original `gpu-transcribe` template baked
# THREE artifacts (Whisper-large-v3 PT, faster-whisper CT2, Silero VAD JIT)
# plus a pre-pulled container image, because it was a single AMI shared by
# both the AWS Batch async path and the streaming session path.
#
# Design v7 split the two paths:
#   - Async batch path: keeps using `gpu-transcribe` AMI (Whisper-large-v3 PT).
#   - Streaming path:   uses THIS AMI (faster-whisper-large-v2 CT2 only).
#
# What this AMI ships:
#   - faster-whisper-large-v2 CTranslate2 weights at
#     /opt/whisper/models/large-v2-ct2/ (model.bin + config.json +
#     tokenizer.json + vocabulary.txt). Path matches the container's
#     startup assertion `f"{MODEL_CACHE_DIR}/{MODEL_SIZE}-ct2"` with
#     env-var defaults `MODEL_CACHE_DIR=/opt/whisper/models` and
#     `MODEL_SIZE=large-v2`.
#   - A 1-second silent 16 kHz mono PCM WAV at /opt/whisper/warmup-1s.wav
#     used by `warmup_asr()` to load the model graph onto the GPU before
#     the first real frame arrives.
#
# What this AMI does NOT ship (intentional deletions from gpu-transcribe):
#   - Whisper-large-v3 PT weights. Used only by the Batch path.
#   - Silero VAD JIT weights. Vendored INSIDE the transcriber-stream
#     container image at
#     `services/transcriber-stream/.../vendor/whisperlivekit/silero_vad_models/`,
#     so the host AMI does not carry a separate copy.
#   - Pre-pulled transcriber-stream container image. At bake time the right
#     ECS task-definition image tag is unknown; a stale pre-pull just bloats
#     the AMI without saving warmup time once the gpu-spawner's RunInstances
#     passes a fresh tag. The instance pulls from ECR at boot.
#
# Why large-v2 instead of large-v3:
#   Design v7 architect-review MUST-02 (and adversarial-review confirmation):
#   large-v3 hallucinates noticeably more on silence than v2. LocalAgreement-2
#   mitigates but v2 is the safer default for live partials.
#
# Why CTranslate2 instead of PyTorch:
#   The streaming path uses faster-whisper (CTranslate2-backed) for sub-second
#   incremental inference; the Batch path uses the original Whisper PT weights
#   for one-shot file transcription. Different format, different artifact.
#
# Source AMI:
#   Same as gpu-transcribe: latest AWS Deep Learning Base GPU AMI on
#   Ubuntu 22.04 (NVIDIA drivers + CUDA + cuDNN + NCCL + Docker pre-installed).
#
# Build host:
#   g4dn.xlarge Spot (matches runtime instance type). Fallback list keeps the
#   build resilient to per-AZ Spot capacity dips.

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

  common_tags = {
    Project     = "panakoes"
    Environment = var.environment
    ManagedBy   = "packer"
    AmiPurpose  = "whisper-stream"
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

source "amazon-ebs" "whisper_stream" {
  region       = var.region
  ssh_username = var.ssh_username

  # Build on Spot rather than On-Demand. The dev AWS account has a 0-vCPU
  # On-Demand G/VT quota (the default for fresh accounts) and an 8-vCPU
  # Spot G/VT quota (the one we explicitly requested + were approved for).
  # `spot_instance_types` is a Packer-native list rather than a single
  # `instance_type` so the build host can transparently fall back to a
  # cheaper-or-more-available type within the same family if the primary
  # is unavailable in the chosen AZ at bake time.
  spot_instance_types = var.spot_instance_types
  spot_price          = "auto"

  source_ami = data.amazon-ami.deep_learning_gpu.id

  ami_name        = local.ami_name
  ami_description = "Panakoes streaming-transcription GPU AMI. faster-whisper-large-v2 CTranslate2 weights at /opt/whisper/models/large-v2-ct2/ plus 1s warmup clip at /opt/whisper/warmup-1s.wav. Built ${local.bake_timestamp}."

  # Smaller root volume than gpu-transcribe. faster-whisper-large-v2 weights
  # are ~3 GB (model.bin alone is ~3 GB float16) and the warmup WAV is a
  # rounding error; 50 GiB is comfortable margin over the parent AMI baseline.
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

  # Wait longer than Packer's default for the AMI snapshot to finish.
  # The default `aws_polling` block is roughly 60 attempts at 15-second
  # intervals (15 minutes total). A 50 GiB root volume on a fresh AWS
  # account can take 40+ minutes to snapshot end-to-end the first time
  # the snapshot service spins up in that account, so the default times
  # out mid-snapshot. 240 attempts at 30-second intervals = 2 hours of
  # headroom. Same value as gpu-transcribe, kept as the canonical safety
  # margin.
  aws_polling {
    delay_seconds = 30
    max_attempts  = 240
  }

  tags            = local.common_tags
  snapshot_tags   = local.common_tags
  run_tags        = local.common_tags
  run_volume_tags = local.common_tags
}

build {
  name    = "panakoes-whisper-stream"
  sources = ["source.amazon-ebs.whisper_stream"]

  # Step 1: faster-whisper-large-v2 CTranslate2 weights + warmup clip.
  # The install script uses `huggingface-cli download` to fetch
  # Systran/faster-whisper-large-v2 into /opt/whisper/models/large-v2-ct2/,
  # then ffmpeg synthesizes a 1-second 16 kHz mono silence clip at
  # /opt/whisper/warmup-1s.wav.
  provisioner "shell" {
    script = "${path.root}/scripts/install-models.sh"

    environment_vars = [
      "MODEL_CACHE_DIR=${var.model_cache_dir}",
      "MODEL_SIZE=${var.model_size}",
      "WARMUP_CLIP_PATH=${var.warmup_clip_path}",
      "HF_MODEL_REPO=${var.hf_model_repo}",
    ]
  }

  # Step 2: CUDA tooling sanity check. Confirms the provisioner instance
  # has a working GPU and that nvidia-smi reports a CUDA runtime version.
  # If this fails the build aborts; a broken AMI is worse than no AMI.
  #
  # `inline_shebang` is set explicitly to `/bin/bash -e` because the default
  # Packer inline shebang is `/bin/sh -e`; on Ubuntu, `/bin/sh` is dash, and
  # dash does not implement `set -o pipefail`. Without the explicit bash
  # shebang the first script line (`set -euo pipefail`) errors with
  # `Illegal option -o pipefail` and the whole provisioner exits non-zero.
  # Same fix as gpu-transcribe (learned during the 2026-05-11 first dev bake).
  provisioner "shell" {
    inline_shebang = "/bin/bash -e"
    inline = [
      "set -euo pipefail",
      "echo 'Checking nvidia-smi availability...'",
      "nvidia-smi",
      "echo 'Checking CUDA runtime...'",
      "nvcc --version || echo 'nvcc not in PATH; relying on container CUDA runtime'",
      "echo 'Verifying baked artifacts...'",
      "test -d /opt/whisper/models/${var.model_size}-ct2",
      "test -f /opt/whisper/models/${var.model_size}-ct2/model.bin",
      "test -f /opt/whisper/models/${var.model_size}-ct2/config.json",
      "test -f /opt/whisper/models/${var.model_size}-ct2/tokenizer.json",
      "test -f ${var.warmup_clip_path}",
      "echo 'whisper-stream AMI sanity check complete.'",
    ]
  }
}
