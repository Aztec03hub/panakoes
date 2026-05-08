# Dev Environment Storage

Per-environment Terraform configuration creating the S3 buckets the
Panakoes `dev` environment writes to and reads from. All three buckets
share the same hardening pattern: dedicated CMK, versioning enabled,
public access fully blocked, TLS-only bucket policy. Lifecycle rules
differ per bucket and reflect each bucket's access pattern.

## What this creates

- `panakoes-dev-audio-uploads-<suffix>`: raw audio uploaded by clients
  via the Ingestion API. CORS enabled for browser-based PUT to
  pre-signed URLs from the public-facing domains.
- `panakoes-dev-transcripts-<suffix>`: transcript JSON output from the
  transcription Lambda. No CORS; only Lambda writes, only the API
  reads.
- `panakoes-dev-log-archive-<suffix>`: long-term log archive feeding
  Athena queries (per ADR-016). 7-year retention for compliance.

Each bucket gets a dedicated KMS CMK aliased
`alias/panakoes-dev-<bucket>` so that key access can be scoped per
bucket without granting blast-radius access to the others. All keys
have rotation enabled and a 30-day deletion window.

## Apply

    cd infra/dev/storage
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS and random providers, then
initializes the S3 backend (the bucket created by `infra/bootstrap/`).

## Lifecycle rationale

| Bucket         | Current versions                                        | Noncurrent versions             |
|----------------|---------------------------------------------------------|---------------------------------|
| audio-uploads  | Kept (archived audio moves to a separate bucket later)  | IA at 30d, expire at 90d        |
| transcripts    | IA at 60d, GLACIER_IR at 180d, never expire             | (default; not yet cost-tuned)   |
| log-archive    | IA at 30d, GLACIER_IR at 90d, DEEP_ARCHIVE at 180d, expire at 7y | (default)                |

Audio uploads are the hot path; current versions stay in STANDARD
because the transcription pipeline reads them within minutes.
Transcripts are small JSON, kept indefinitely, but rarely re-read once
surfaced in the API. The log archive optimizes for compliance: cold
storage as fast as Athena access patterns allow, then hard expiry at
the 7-year mark.

## Consuming outputs from other configs

Downstream services (Ingestion API, transcription Lambda, log-export
job) read these buckets' names and KMS key ARNs via a
`terraform_remote_state` data source pointing at this config's state:

    data "terraform_remote_state" "storage" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/storage/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.storage.outputs.audio_uploads_bucket_name
    #   data.terraform_remote_state.storage.outputs.transcripts_bucket_arn
    #   data.terraform_remote_state.storage.outputs.log_archive_kms_key_arn

## Cost expectations

- S3 storage at typical dev volumes is pennies per month; STANDARD is
  $0.023/GB-mo and the cold tiers drop an order of magnitude or more.
- KMS CMKs are $1/month each, so $3/month just for the three keys.
  This is the dominant fixed cost of this config; everything else
  scales with usage.
- KMS request charges apply per encrypt / decrypt (about $0.03 per
  10,000 requests). Bucket-key enabled on the SSE config means S3
  amortizes encryption requests at the bucket level rather than
  per-object, which keeps KMS request volume bounded even under heavy
  upload bursts.

## Outputs

| Output                          | Type   | Purpose                                      |
|---------------------------------|--------|----------------------------------------------|
| `audio_uploads_bucket_name`     | string | Name of the audio-uploads bucket             |
| `audio_uploads_bucket_arn`      | string | ARN of the audio-uploads bucket              |
| `audio_uploads_kms_key_arn`     | string | ARN of the audio-uploads CMK                 |
| `transcripts_bucket_name`       | string | Name of the transcripts bucket               |
| `transcripts_bucket_arn`        | string | ARN of the transcripts bucket                |
| `transcripts_kms_key_arn`       | string | ARN of the transcripts CMK                   |
| `log_archive_bucket_name`       | string | Name of the log-archive bucket               |
| `log_archive_bucket_arn`        | string | ARN of the log-archive bucket                |
| `log_archive_kms_key_arn`       | string | ARN of the log-archive CMK                   |
