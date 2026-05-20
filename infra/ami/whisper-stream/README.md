# whisper-stream AMI

Packer template that bakes the dedicated GPU AMI for the Panakoes
streaming-transcription path (session-spawned EC2 g4dn.xlarge Spot
launched by the gpu-spawner service).

This AMI is the streaming-path-specific successor to
[`infra/ami/gpu-transcribe/`](../gpu-transcribe/). Design v7 split the
two transcription paths:

- **Async batch path** (AWS Batch on EC2 g4dn.xlarge Spot): keeps using
  the `gpu-transcribe` AMI (Whisper-large-v3 PT weights).
- **Streaming path** (session-spawned EC2 g4dn.xlarge Spot): uses THIS
  AMI (faster-whisper-large-v2 CTranslate2 weights only).

## What this AMI ships

| Path on AMI | Source | Why |
|---|---|---|
| `/opt/whisper/models/large-v2-ct2/` | `Systran/faster-whisper-large-v2` on HuggingFace | CTranslate2 weights for sub-second incremental inference |
| `/opt/whisper/warmup-1s.wav` | Synthesized at bake time via `ffmpeg` (1-second 16 kHz mono silence) | Loads the model graph onto the GPU before the first real frame arrives |

Both paths are checked by the transcriber-stream container's startup
assertion in
[`services/transcriber-stream/src/panakoes_transcriber_stream/main.py`](../../../services/transcriber-stream/src/panakoes_transcriber_stream/main.py).
If the AMI bake is missing either path the container fails fast with an
`ami-asset-missing` WebSocket error code, not a multi-minute silent
HuggingFace download.

## What this AMI does NOT ship

Intentional deletions from the `gpu-transcribe` template:

- **Whisper-large-v3 PT weights.** Used only by the Batch path; not
  needed here.
- **Silero VAD JIT weights.** Vendored INSIDE the transcriber-stream
  container image at
  `services/transcriber-stream/src/panakoes_transcriber_stream/vendor/whisperlivekit/silero_vad_models/silero_vad_16k_op15.onnx`,
  so the host AMI does not carry a separate copy.
- **Pre-pulled transcriber-stream container image.** At AMI bake time
  the right ECS task-definition image tag is unknown; a stale pre-pull
  just bloats the AMI without saving warmup time once the gpu-spawner's
  RunInstances passes a fresh tag through the task def. The instance
  pulls from ECR at boot.

## Layout

```
infra/ami/whisper-stream/
  main.pkr.hcl                Packer template (HCL2). Source = latest
                              Deep Learning AMI GPU on Ubuntu 22.04;
                              build host = g4dn.xlarge Spot.
  variables.pkr.hcl           Build-time inputs. Region, AMI prefix,
                              model path + variant + HF repo.
  scripts/install-models.sh   Installs huggingface-hub, downloads CT2
                              weights, synthesizes warmup WAV, verifies
                              and locks the bake to read-only.
```

## Prerequisites

- **Packer** >= 1.10. Install:
  - Linux: download the binary from
    [https://developer.hashicorp.com/packer/downloads](https://developer.hashicorp.com/packer/downloads)
    and place it on `$PATH`.
- **AWS credentials** with `ec2:RunInstances`, `ec2:CreateImage`,
  `ec2:CreateTags`, and EBS snapshot permissions. The expected dev
  invocation is `AWS_PROFILE=panakoes-admin packer build .`.
- **Build budget**: each successful bake costs roughly **$0.10 to $0.30**.
  A g4dn.xlarge Spot instance runs ~$0.15 to $0.20/hour in us-east-1;
  a typical bake takes 30 to 60 minutes (HuggingFace download ~3 GB,
  sanity check, snapshot + register AMI).

## Build inputs

All build inputs have sensible defaults; the AMI bakes with no required
`-var` flags. See `variables.pkr.hcl` for the full list. Overridable knobs:

- `model_size`: defaults to `large-v2` (per design v7 architect MUST-02).
  Override only when intentionally pinning a different model variant;
  this must move in lockstep with the container's `MODEL_SIZE` env-var
  default.
- `hf_model_repo`: defaults to `Systran/faster-whisper-large-v2`.
- `model_cache_dir`: defaults to `/opt/whisper/models`. Must match the
  container's `MODEL_CACHE_DIR` env-var default.
- `warmup_clip_path`: defaults to `/opt/whisper/warmup-1s.wav`. Must
  match the container's hardcoded `warmup_clip_path` in `config.py`.

The path defaults are intentionally identical to what the container
asserts at startup. Changing any of them requires a synchronized PR to
`services/transcriber-stream/`.

## Build commands

Run from this directory:

```bash
# 1. Format check (every file, recursive). Required before commit.
packer fmt -check -recursive .

# 2. Plugin install. Reads required_plugins from main.pkr.hcl.
AWS_PROFILE=panakoes-admin packer init .

# 3. Validate template + var resolution. Cheap; no AWS calls.
AWS_PROFILE=panakoes-admin packer validate .

# 4. Build (launches a g4dn.xlarge Spot, runs provisioners, snapshots,
#    registers the AMI, terminates the build instance). ~30-45 min, ~$0.20.
AWS_PROFILE=panakoes-admin packer build .
```

After a successful build, Packer prints the new AMI ID. The AMI is
tagged `Project=panakoes`, `Environment=<env>`, `ManagedBy=packer`,
`AmiPurpose=whisper-stream`, `BakedAt=<UTC ISO timestamp>`.

## Rotation

The AMI bakes a frozen model snapshot. To rotate:

1. Update `model_size` / `hf_model_repo` (or pin a specific HF
   revision in `install-models.sh`).
2. Re-run `packer build .`.
3. Update `streaming_gpu_ami_id` in `infra/dev/ecs/variables.tf` to
   point at the new AMI ID (separate Terraform PR).
4. Old AMIs and their snapshots remain in the account until manually
   deregistered. Cleanup is a follow-up issue; for now,
   `aws ec2 deregister-image` + `aws ec2 delete-snapshot` per stale AMI.

## Tagging policy

Every AMI and underlying snapshot carries:

| Tag | Value | Why |
|---|---|---|
| `Project` | `panakoes` | Cost-allocation grouping |
| `Environment` | `dev` (default), bumpable | Filter dev vs prod AMIs |
| `ManagedBy` | `packer` | Distinguish from manually built AMIs |
| `AmiPurpose` | `whisper-stream` | Distinguish from `gpu-transcribe` |
| `BakedAt` | `YYYY-MM-DDThh-mm-ssZ` | Audit trail; cross-references CloudTrail |

## Architecture notes

- **Why bake, not user-data?** A user-data approach would push driver
  init, model weight download (~3 GB CT2), and graph warmup into every
  cold-start. Streaming session warmup target is 45 to 80 seconds; the
  user-data approach pushes past 3 minutes.
- **Why g4dn.xlarge for the build host?** The runtime instance type is
  also g4dn.xlarge. Building on the same family lets the sanity check
  exercise actual driver/library compatibility instead of trusting that
  "it should work."
- **Why encrypt the boot volume?** The AMI carries no secrets, but
  `encrypt_boot = true` is a cheap defense-in-depth knob. Production
  AMIs should override the default `aws/ebs` KMS key with a dedicated
  CMK.
- **Why huggingface-cli instead of direct URL + SHA256?** The CT2 model
  is a directory of files, not a single tarball. HF model repos are
  content-addressed by revision SHA, which is the integrity anchor we
  care about; pin via revision when reproducibility matters.
