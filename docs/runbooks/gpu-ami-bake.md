# GPU AMI Bake (gpu-transcribe)

## Purpose

This runbook covers the end-to-end operator workflow for baking the Panakoes streaming + batch GPU AMI defined in `infra/ami/gpu-transcribe/`. The AMI pre-installs Whisper-large-v3 fp16 weights, faster-whisper-large CT2 weights, Silero VAD weights, and the transcriber-stream container so streaming session warmup stays bounded to driver init plus container start (~45 to 80 seconds).

It also documents the `PendingVerification` mitigation pattern (cheap pre-flight probe on a t3.micro) so the first GPU bake from a fresh AWS account does not burn 30+ minutes of Spot time staring at an EC2 launch that is blocked by AWS account verification.

## When to use this runbook

- Baking the first GPU AMI in a freshly provisioned AWS account.
- Rotating the AMI to pick up new model weights, a new transcriber-stream image tag, or a Deep Learning AMI security patch.
- Recovering from a stale AMI ID pin in `infra/dev/batch/variables.tf` (or `infra/dev/spawner-iam/`).

If the problem is "AWS Batch compute environment is stuck in INVALID with a `client error: AMI not found`", this runbook is the right entry point: it produces a fresh AMI ID and shows where to pin it.

## Prerequisites

Operator needs:

- `aws` CLI authenticated against the dev account (profile `panakoes-admin`, account `659225405128`, region `us-east-1`).
  - Verify: `AWS_PROFILE=panakoes-admin aws sts get-caller-identity` returns the `phil` IAM user ARN.
- `packer` >= 1.10 on `$PATH`.
  - Verify: `packer version`.
  - Install on Linux (no apt repo configured on Phil's WSL): download the binary from [https://developer.hashicorp.com/packer/downloads](https://developer.hashicorp.com/packer/downloads), `sudo install packer /usr/local/bin/`. (Why not the apt repo? Adding it touches `/etc/apt/sources.list.d/`, which Phil's setup scripts do not manage and which interview-defensibly should stay declarative via the devcontainer if we want it persistent. Single-binary install is the smaller blast radius.)
- GPU Spot vCPU quota approved (the L-3819A6DF "All G and VT Spot Instance Requests" service quota at >= 8 vCPU in `us-east-1`; `g4dn.xlarge` consumes 4 vCPU per instance, so 8 covers one bake plus a safety margin).
  - Verify: `AWS_PROFILE=panakoes-admin aws service-quotas get-service-quota --service-code ec2 --quota-code L-3819A6DF --region us-east-1 --query 'Quota.Value' --output text`. Output `>= 8.0`.

## Procedure

### Lane 1: First bake after fresh-account approval

The first-ever arbitrary `ec2:RunInstances` call from a brand-new AWS account can return `PendingVerification` even with quotas approved (see memory `aws_pending_verification_first_ec2_launch.md`). The verification window is "minutes to four hours". Bake the AMI directly and you risk burning 30+ minutes of Packer setup before the launch is rejected.

Mitigation: trip the verification gate on a cheap `t3.micro` on-demand instance first. The instance takes a few seconds to launch; if it succeeds, the account is verified for the region and the GPU bake will not hit `PendingVerification`. If it fails with `PendingVerification`, capture the timestamp, wait, retry.

1. **Run the probe.**

   ```bash
   AWS_PROFILE=panakoes-admin aws ec2 run-instances \
     --region us-east-1 \
     --image-id "$(AWS_PROFILE=panakoes-admin aws ec2 describe-images \
        --region us-east-1 --owners amazon \
        --filters 'Name=name,Values=al2023-ami-2023.*-x86_64' 'Name=state,Values=available' \
        --query 'reverse(sort_by(Images,&CreationDate))[0].ImageId' --output text)" \
     --instance-type t3.micro \
     --subnet-id "$(AWS_PROFILE=panakoes-admin aws ec2 describe-subnets \
        --region us-east-1 --filters Name=default-for-az,Values=true \
        --query 'Subnets[0].SubnetId' --output text)" \
     --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=panakoes},{Key=Purpose,Value=pending-verification-probe}]' \
     --instance-initiated-shutdown-behavior terminate \
     --query 'Instances[0].InstanceId' --output text
   ```

   Expected outcome: an instance ID on stdout means the gate is clear; an error containing `PendingVerification` means wait.

2. **Terminate the probe.** Probe instance has no purpose past launch validation.

   ```bash
   AWS_PROFILE=panakoes-admin aws ec2 terminate-instances --region us-east-1 --instance-ids <i-...>
   ```

   Probe cost: a few cents at most (single-digit minutes of `t3.micro`).

### Lane 2: Stage model artifacts

The bake requires three model artifacts (URL + SHA256 each). Two have stable public URLs; one is custom-assembled and lives in `s3://panakoes-dev-log-archive-<suffix>/ami-bake-artifacts/`.

1. **Whisper-large-v3 fp16 weights.** OpenAI publishes the canonical URL on their CDN. The sha256 is encoded in the URL path.

   - URL: `https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt`
   - SHA256: `e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb`

2. **Silero VAD weights.** Pinned to the v4.0 release tag on the official `snakers4/silero-vad` GitHub repo.

   - URL: `https://raw.githubusercontent.com/snakers4/silero-vad/v4.0/files/silero_vad.jit`
   - SHA256: `082e21870cf7722b0c7fa5228eaed579efb6870df81192b79bed3f7bac2f738a`

3. **faster-whisper-large CT2 tarball.** Custom-assembled (CT2-converted `Systran/faster-whisper-large-v3` weights), staged in S3.

   - S3 key: `s3://panakoes-dev-log-archive-6b47ae54/ami-bake-artifacts/faster-whisper-large-v3.tar.gz`
   - Generate presigned URL (24-hour TTL covers the longest plausible bake retry loop):

     ```bash
     AWS_PROFILE=panakoes-admin aws s3 presign \
       s3://panakoes-dev-log-archive-6b47ae54/ami-bake-artifacts/faster-whisper-large-v3.tar.gz \
       --expires-in 86400 --region us-east-1
     ```

   - SHA256: compute once and pin in `dev.auto.pkrvars.hcl`; the canonical tarball is immutable across bakes until the operator chooses to refresh. To recompute (e.g., after a tarball refresh), the cheapest path is to stream the S3 object straight into `sha256sum` so no disk write happens locally:

     ```bash
     AWS_PROFILE=panakoes-admin aws s3 cp \
       s3://panakoes-dev-log-archive-6b47ae54/ami-bake-artifacts/faster-whisper-large-v3.tar.gz - \
       --region us-east-1 | sha256sum
     ```

     S3 ETags on multipart uploads are NOT SHA256 digests; do not substitute the ETag for the SHA. (This bites operators who try to skip the recompute. The script verifies via `openssl dgst -sha256`; an ETag mismatch fails the bake at the SHA verification step.)

4. **Write `infra/ami/gpu-transcribe/dev.auto.pkrvars.hcl`** with the values gathered above plus the dev account ID. This file is `*.pkrvars.hcl`-gitignored (see `.gitignore`).

   ```hcl
   whisper_large_v3_url    = "https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt"
   whisper_large_v3_sha256 = "e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb"
   faster_whisper_url      = "<24-hour-presigned-URL from step 3>"
   faster_whisper_sha256   = "<SHA256 from step 3>"
   silero_vad_url          = "https://raw.githubusercontent.com/snakers4/silero-vad/v4.0/files/silero_vad.jit"
   silero_vad_sha256       = "082e21870cf7722b0c7fa5228eaed579efb6870df81192b79bed3f7bac2f738a"
   ecr_account_id          = "659225405128"
   ```

### Lane 3: Run the Packer build

1. **Init plugins** (downloads the `hashicorp/amazon` plugin; idempotent).

   ```bash
   cd infra/ami/gpu-transcribe
   AWS_PROFILE=panakoes-admin packer init .
   ```

2. **Validate.** Cheap; no AWS calls.

   ```bash
   AWS_PROFILE=panakoes-admin packer validate .
   ```

3. **Build.** Launches a single `g4dn.xlarge` on-demand instance, runs the provisioners, snapshots, registers the AMI, terminates the build instance.

   ```bash
   AWS_PROFILE=panakoes-admin packer build .
   ```

   Expected duration: 30 to 60 minutes (driver-aware Deep Learning AMI source + 3 GB Whisper weights + 1.5 GB faster-whisper tarball + container image pre-pull + snapshot register).

   Expected cost: $0.50 to $2 in build-instance time (~$0.526/hour for `g4dn.xlarge` on-demand) plus $0.05/GB-month for the resulting ~20 GB snapshot.

4. **Extract the AMI ID** from Packer's final manifest line. Packer prints `==> Builds finished. The artifacts of successful builds are: panakoes-gpu-transcribe: AMIs were created: us-east-1: ami-XXXXXXXX`. Capture this ID.

   **First-bake gotcha (2026-05-11):** if Packer exits with `ResourceNotReady: failed waiting for successful resource state` after the `Creating AMI ...` line, the bake may have succeeded asynchronously. The `amazon-ebs` plugin defaults to ~15 minutes of waiting on the snapshot copy; first-time snapshots on a brand-new account routinely take 40 to 60 minutes. Check before re-running: `aws ec2 describe-images --image-ids <ami-id-from-Packer-log> --query 'Images[0].State'`. If it returns `pending` or `available`, the AMI exists; do NOT re-run Packer. Tag it manually (`Project=panakoes Component=gpu-transcribe Environment=dev BakedAt=<date>`) since Packer never reached the tagging step. The `aws_polling` block now in `main.pkr.hcl` (`delay_seconds = 30`, `max_attempts = 240`, ~2 hours of patience) prevents this on subsequent bakes. First successful dev bake landed as `ami-0dee04ee5042c94cf` via this exact recovery path.

### Lane 4: Pin the AMI ID downstream

1. **Edit `infra/dev/batch/variables.tf`.** Change the `gpu_ami_id` default to the new AMI ID. Update the description to reflect the new bake date. Current pin (2026-05-11): `ami-0dee04ee5042c94cf`.
2. **Run `terraform fmt`** in the module directory.
3. **Run `terraform validate`** to confirm the change is syntactically clean.
4. **PR + merge** through the standard `gh pr create --fill && gh pr merge --squash --auto --delete-branch` flow.
5. **Set the spawner Lambda env var.** The `gpu-spawner` service reads `GPU_AMI_ID` from the environment (per `services/gpu-spawner/src/panakoes_gpu_spawner/config.py`). This is parameter-driven, not Terraform-managed in the current spawner module; updating it is currently a manual `aws lambda update-function-configuration` once the Lambda exists.

## Verification

After the bake completes:

```bash
AWS_PROFILE=panakoes-admin aws ec2 describe-images \
  --region us-east-1 --owners self \
  --filters 'Name=tag:Project,Values=panakoes' 'Name=tag:AmiPurpose,Values=gpu-transcribe' \
  --query 'sort_by(Images,&CreationDate)[-1].{ImageId:ImageId,Name:Name,State:State,BakedAt:Tags[?Key==`BakedAt`].Value|[0]}' \
  --output table
```

Expected: a row with the new AMI ID, state `available`, and a `BakedAt` tag matching the bake timestamp.

After Terraform pin merges:

```bash
cd infra/dev/batch
AWS_PROFILE=panakoes-admin terraform plan
```

Expected: plan reports a single `aws_batch_compute_environment.gpu_transcribe` (or similar) replacement / in-place update that swaps the AMI ID, no other drift.

## Rollback

To roll back the AMI pin to the previous AMI ID:

1. `git revert <commit-sha>` against the `infra/dev/batch/variables.tf` change.
2. PR + merge the revert.
3. `terraform apply` against `infra/dev/batch` to restore the previous compute environment AMI.

To deregister the new AMI entirely (e.g., it was bad and you do not want to keep paying for the snapshot):

```bash
# 1. Find the snapshot IDs backing the AMI.
AWS_PROFILE=panakoes-admin aws ec2 describe-images \
  --region us-east-1 --image-ids <ami-id> \
  --query 'Images[0].BlockDeviceMappings[].Ebs.SnapshotId' --output text

# 2. Deregister the AMI.
AWS_PROFILE=panakoes-admin aws ec2 deregister-image --region us-east-1 --image-id <ami-id>

# 3. Delete each snapshot the AMI referenced.
AWS_PROFILE=panakoes-admin aws ec2 delete-snapshot --region us-east-1 --snapshot-id <snap-id>
```

Snapshots survive AMI deregistration unless explicitly deleted; forgetting step 3 leaves the EBS snapshot charge ($0.05/GB-month) accruing indefinitely.

## References

- ADR-010 (PLANNING.md): g4dn.xlarge Spot for streaming transcription.
- ADR-035 (`docs/adr/ADR-035-new-aws-account-friction-mitigations.md`): catalogued first-deploy gates including `PendingVerification`.
- `infra/ami/gpu-transcribe/README.md`: Packer template authoring details.
- `infra/dev/batch/README.md`: how the AMI ID is consumed by AWS Batch.
- `services/gpu-spawner/README.md`: how the AMI ID is consumed by the spawner Lambda.
- Memory entries: `aws_pending_verification_first_ec2_launch`, `aws_ecs_fargate_first_deploy_checklist`, `feedback_panakoes_lessons`.
