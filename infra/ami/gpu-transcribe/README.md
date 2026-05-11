# gpu-transcribe AMI

Packer template that bakes the custom GPU AMI used by both Panakoes
transcription paths:

- **Async batch path** (AWS Batch on EC2 g4dn.xlarge Spot) running
  Whisper-large-v3 fp16.
- **Streaming path** (session-spawned EC2 g4dn.xlarge Spot via
  Spawner Lambda) running faster-whisper-large + Silero VAD.

Pre-baking model weights and the transcriber container image into the
AMI keeps streaming session warmup bounded to driver init plus
container start (target: 45 to 80 seconds). Pulling Whisper-large-v3
and the runtime image at boot time would push warmup past two minutes,
which the "connecting..." UI state cannot mask gracefully.

## Layout

```
infra/ami/gpu-transcribe/
  main.pkr.hcl                Packer template (HCL2). Source = latest
                              Deep Learning AMI GPU on Ubuntu 22.04;
                              build host = g4dn.xlarge.
  variables.pkr.hcl           Build-time inputs. Region, AMI prefix,
                              model URLs + SHA256s, ECR coordinates.
  scripts/install-models.sh   Downloads + SHA256-verifies Whisper-large-v3
                              fp16, faster-whisper-large, Silero VAD.
  scripts/install-runtime.sh  Authenticates to ECR and `docker pull`s
                              the transcriber-stream image.
```

## Prerequisites

- **Packer** >= 1.10. Install:
  - macOS: `brew tap hashicorp/tap && brew install hashicorp/tap/packer`
  - Linux: download the binary from
    [https://developer.hashicorp.com/packer/downloads](https://developer.hashicorp.com/packer/downloads)
    and place it on `$PATH`.
- **AWS credentials** with `ec2:RunInstances`, `ec2:CreateImage`,
  `ec2:CreateTags`, EBS snapshot permissions, plus `ecr:GetAuthorizationToken`
  and `ecr:BatchGetImage` against the transcriber-stream repository ARN.
  The simplest local setup is `aws sso login` against an admin profile.
- **Build budget**: each successful bake costs roughly **$0.10 to $0.30**.
  A g4dn.xlarge Spot instance runs ~$0.15 to $0.20/hour in us-east-1; a
  typical bake takes 30 to 60 minutes (download weights + container layers,
  run sanity check, snapshot + register AMI). Failed bakes still cost the
  partial-hour instance time. The Packer template launches Spot (NOT
  On-Demand) because fresh AWS accounts ship with a 0-vCPU On-Demand
  G/VT quota by default; the Spot G/VT quota is the one we explicitly
  requested + were approved for (8 vCPU, sufficient for one bake plus
  margin).

## Build inputs

All build inputs are var-driven; nothing is hardcoded. Values can come
from environment variables, a `*.pkrvars.hcl` file passed with
`-var-file`, or inline `-var` flags.

The following variables are **required** (no default):

| Variable | Purpose |
|---|---|
| `whisper_large_v3_url` | Source URL for Whisper-large-v3 fp16 weights |
| `whisper_large_v3_sha256` | Expected SHA256 of the Whisper file |
| `faster_whisper_url` | Source URL for the faster-whisper-large CT2 tarball |
| `faster_whisper_sha256` | Expected SHA256 of the faster-whisper tarball |
| `silero_vad_url` | Source URL for the Silero VAD weights |
| `silero_vad_sha256` | Expected SHA256 of the Silero VAD file |
| `ecr_account_id` | Account hosting the transcriber-stream ECR repo |

The remaining inputs (`region`, `ami_name_prefix`, `instance_type`,
`source_ami_*`, `*_install_path`, `ecr_image_tag`, `ecr_repository_name`,
`environment`, `root_volume_*`) have sensible defaults; see
`variables.pkr.hcl`.

The recommended pattern is a local untracked file:

```bash
# infra/ami/gpu-transcribe/dev.auto.pkrvars.hcl  (gitignored as *.pkrvars.hcl)
whisper_large_v3_url    = "https://..."
whisper_large_v3_sha256 = "..."
faster_whisper_url      = "https://..."
faster_whisper_sha256   = "..."
silero_vad_url          = "https://..."
silero_vad_sha256       = "..."
ecr_account_id          = "123456789012"
```

`*.auto.pkrvars.hcl` files are auto-loaded by `packer build`. Treat
this file as untracked: do **not** `git add` it. Repo-root `.gitignore`
matches `infra/ami/**/*.pkrvars.hcl` and `infra/ami/**/*.auto.pkrvars.hcl`
so accidental adds are blocked at the source-control layer (landed
alongside the artifact-hosting convention below). Even when the values
themselves are not secret (SHA256 digests of public weights), the URLs
can be presigned S3 URLs whose query-string signature IS sensitive, and
pinning any of them in source obscures the build's supply chain and
bypasses the var-driven design.

## Artifact hosting

Model artifacts that are directly fetchable from a stable public URL
stay on their canonical host: Whisper-large-v3 lives on OpenAI's CDN,
Silero VAD lives on GitHub raw. Both are fine to fetch directly during
the bake; their URLs are stable across rebuilds.

Anything that is NOT directly fetchable from a stable public URL (a
faster-whisper-large CT2 tarball assembled locally, a fine-tuned model
weight, a private container layer) gets hosted in the dev environment's
log-archive bucket under the `ami-bake-artifacts/` prefix:

```
s3://panakoes-dev-log-archive-<suffix>/ami-bake-artifacts/
```

(The `<suffix>` is the random suffix emitted by the `infra/dev/storage`
Terraform module; `terraform output` against that module returns the
current bucket name. The bucket is reused for AMI-bake artifacts because
it already has the right encryption + versioning + lifecycle posture
for opaque blobs Phil owns.)

Per-bake operator workflow:

1. `aws s3 cp <local-artifact> s3://panakoes-dev-log-archive-<suffix>/ami-bake-artifacts/<filename>`
2. `aws s3 presign s3://panakoes-dev-log-archive-<suffix>/ami-bake-artifacts/<filename> --expires-in 86400`
   (24-hour presigned URL; the bake itself takes 10 to 20 minutes, so
   this leaves wide margin for retries.)
3. Paste the presigned URL into the per-bake `dev.pkrvars.hcl` (or
   `dev.auto.pkrvars.hcl`) as the appropriate `*_url` variable.
4. Compute and paste the matching `*_sha256` SHA256 digest.

The Packer build's `scripts/install-models.sh` runs `curl -L`
(follow-redirects) and verifies SHA256 after download, so presigned URLs
work transparently and the SHA256 anchors integrity even though the URL
signature changes per bake. The signature expiring after 24 hours is a
safe failure mode: the bake either completes or fails fast with a 403.

Lifecycle: the underlying S3 object lives until manually deleted (no
auto-expiration on the `ami-bake-artifacts/` prefix); the presigned URL
expires harmlessly. Rotating an artifact = `aws s3 cp` a new object,
generate a new presigned URL, paste, rebuild.

## Build commands

Run from this directory.

```bash
# 1. Format check (every file, recursive). Required before commit.
packer fmt -check -recursive .

# 2. Plugin install. Reads required_plugins from main.pkr.hcl.
packer init .

# 3. Validate template + var resolution. Cheap; no AWS calls.
packer validate .

# 4. Build (launches a g4dn.xlarge, runs provisioners, snapshots, registers
#    the AMI, terminates the build instance). Costs ~$0.20-0.50.
packer build .
```

After a successful build, Packer prints the new AMI ID. The AMI is tagged
`Project=panakoes`, `Environment=<env>`, `ManagedBy=packer`,
`AmiPurpose=gpu-transcribe`, `BakedAt=<UTC ISO timestamp>`.

## Rotation

The AMI bakes a frozen set of model weights and a frozen container image.
To rotate any of those:

1. Update the `_url` and `_sha256` vars (or the ECR `ecr_image_tag`).
2. Re-run `packer build .`.
3. Update the gpu-spawner Lambda's `AMI_ID` parameter to point at the new
   AMI ID (Terraform variable, separate PR).
4. Old AMIs and their snapshots remain in the account until manually
   deregistered. Cleanup workflow lands in a follow-up issue; for now,
   `aws ec2 deregister-image` + `aws ec2 delete-snapshot` per stale AMI.

## Tagging policy

Every AMI and underlying snapshot carries:

| Tag | Value | Why |
|---|---|---|
| `Project` | `panakoes` | Cost-allocation grouping |
| `Environment` | `dev` (default), bumpable | Filter dev vs prod AMIs |
| `ManagedBy` | `packer` | Distinguish from manually built AMIs |
| `AmiPurpose` | `gpu-transcribe` | Distinguish from future AMIs (e.g., CPU base) |
| `BakedAt` | `YYYY-MM-DDThh-mm-ssZ` | Audit trail; cross-references CloudTrail |

The Packer build also stamps `run_tags` and `run_volume_tags` so the
ephemeral builder instance and its EBS volume carry the same metadata
during the bake; this matters if a build aborts and leaves orphan
resources for cleanup.

## Architecture notes

- **Why bake, not user-data?** A user-data approach would push driver init,
  container pull, model weight download (~3 GB Whisper + ~1.5 GB
  faster-whisper) into every cold-start. Streaming session warmup target
  is 45 to 80 seconds; user-data approach pushes us past 3 minutes.
- **Why g4dn.xlarge for the build host?** The runtime instance type is
  also g4dn.xlarge (per ADR-010). Building on the same family lets the
  CUDA tooling check exercise actual driver/library compatibility instead
  of trusting that "it should work."
- **Why encrypt the boot volume?** The AMI carries no secrets, but
  `encrypt_boot = true` is a cheap defense-in-depth knob. Production AMIs
  should override the default `aws/ebs` KMS key with a dedicated CMK.
- **Why pre-pull the container?** Streaming session warmup pulls
  transcriber-stream from ECR on every cold start unless the image is
  baked in. Pre-pulling shaves 5 to 30 seconds off warmup. The image and
  the model weights together are the only reason this AMI exists; without
  them we would just use the unmodified Deep Learning AMI.
