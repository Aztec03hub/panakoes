# Dev Environment Secrets Manager

Per-environment Terraform configuration creating the AWS Secrets
Manager secrets the Panakoes `dev` environment microservices read at
runtime. Every secret is KMS-encrypted with a dedicated dev-only CMK
and provisioned with a placeholder value; real values are written
post-apply via the AWS CLI so they never appear in Terraform state,
plan output, or commit history.

## What this creates

A single CMK aliased `alias/panakoes-dev-secrets` (rotation enabled,
7-day deletion window) encrypts the following secrets, all named
`panakoes-dev/<purpose>` so they group together in the AWS console:

| Secret name                             | Purpose                                                                |
|-----------------------------------------|------------------------------------------------------------------------|
| `panakoes-dev/jwt-signing-secret`       | HS256 secret for the auth service's slice 1 JWT signing                |
| `panakoes-dev/anthropic-api-key`        | Anthropic API key for the summarization service                        |
| `panakoes-dev/stripe-test-key`          | Stripe TEST mode secret key for the billing service                    |
| `panakoes-dev/stripe-webhook-signing-secret` | Stripe webhook signing secret for the billing service             |
| `panakoes-dev/postgres-auth-db-password`| Postgres password for the auth service's database user                 |
| `panakoes-dev/database-url`             | Full Postgres connection URL for the auth service                      |
| `panakoes-dev/ses-smtp-credentials`     | SES SMTP credentials (JSON: `username`, `password`)                    |

Every `aws_secretsmanager_secret_version` resource has
`lifecycle { ignore_changes = [secret_string] }`, so subsequent
`terraform apply` runs do not revert manual rotations.

`recovery_window_in_days = 7` on every secret matches the KMS key's
deletion window. Both windows are deliberately shorter than the
30-day default the storage and data modules use, because dev secret
material is replaceable in minutes and a long undelete window blocks
reuse of the secret name.

## Why no resource-based policy yet

The CMK and each secret are unrestricted at the resource-policy
layer; access today is gated entirely by the consuming role's IAM
policy. Task #34 wires up per-service IAM roles (auth,
summarization, billing) at which point each secret gets a tight
resource-based policy granting `secretsmanager:GetSecretValue` only
to the one role that needs it, pinned by `aws:PrincipalArn`. The
TODO sketch in `main.tf` is the template. We tighten before any
non-dev workload touches these secrets.

## Apply (NOT YET)

This module is committed but **not applied** to AWS yet. Apply
ordering blocks on the IAM consumers landing in task #34 so that the
resource-based policies can be added in the same sweep instead of as
a noisy follow-up commit. When the time comes:

    cd infra/dev/secrets
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS provider and initializes the S3
backend (the bucket created by `infra/bootstrap/`). The lock file
shipped with this module pins exact provider hashes for reproducible
init.

## Post-apply: write the real secret values

Terraform writes a placeholder (`REPLACE_ME_AFTER_APPLY` for plain
strings, an analogous JSON shape for structured secrets) to each
secret on first create, then `ignore_changes` keeps Terraform's
hands off the value forever after. The first post-apply step is to
overwrite each placeholder with a real value via the AWS CLI:

    aws secretsmanager put-secret-value \
      --region us-east-1 \
      --secret-id panakoes-dev/jwt-signing-secret \
      --secret-string "$(openssl rand -hex 32)"

    aws secretsmanager put-secret-value \
      --region us-east-1 \
      --secret-id panakoes-dev/anthropic-api-key \
      --secret-string "sk-ant-..."

    aws secretsmanager put-secret-value \
      --region us-east-1 \
      --secret-id panakoes-dev/stripe-test-key \
      --secret-string "sk_test_..."

    aws secretsmanager put-secret-value \
      --region us-east-1 \
      --secret-id panakoes-dev/stripe-webhook-signing-secret \
      --secret-string "whsec_..."

    aws secretsmanager put-secret-value \
      --region us-east-1 \
      --secret-id panakoes-dev/postgres-auth-db-password \
      --secret-string "$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"

    aws secretsmanager put-secret-value \
      --region us-east-1 \
      --secret-id panakoes-dev/database-url \
      --secret-string "postgres://auth_user:<password>@<host>:5432/auth_dev"

    aws secretsmanager put-secret-value \
      --region us-east-1 \
      --secret-id panakoes-dev/ses-smtp-credentials \
      --secret-string '{"username":"AKIA...","password":"..."}'

For routine rotation later, the same `put-secret-value` call writes
a new version while the prior version stays available under
`AWSPREVIOUS` until you `update-secret-version-stage` to retire it.

## Reading from a service

Consumers fetch the value at runtime via the AWS SDK rather than at
build time, so secrets never enter container images, logs, or
Terraform state:

    # Python (boto3)
    import boto3, json
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    raw = sm.get_secret_value(SecretId="panakoes-dev/ses-smtp-credentials")
    creds = json.loads(raw["SecretString"])

    # TypeScript (@aws-sdk/client-secrets-manager)
    import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";
    const sm = new SecretsManagerClient({ region: "us-east-1" });
    const resp = await sm.send(new GetSecretValueCommand({
      SecretId: "panakoes-dev/jwt-signing-secret",
    }));

Cache the value in process memory; do not call `GetSecretValue` on
every request. Secrets Manager request charges are $0.05 per 10,000
calls and a hot-path service can spend more than the secret itself
costs if you skip caching.

## Variables

| Variable        | Type   | Default       | Purpose                                |
|-----------------|--------|---------------|----------------------------------------|
| `aws_region`    | string | `us-east-1`   | AWS region for the resources           |
| `project_name`  | string | `panakoes`    | Used for resource naming and tagging   |
| `environment`   | string | `dev`         | Environment name; appears in the prefix|

## Outputs

| Output         | Type   | Purpose                                                 |
|----------------|--------|---------------------------------------------------------|
| `secret_arns`  | map    | Map of short secret name (e.g. `jwt-signing-secret`) to full ARN. Consume via `terraform_remote_state` for IAM policy attachment. Values are NOT exposed. |
| `kms_key_arn`  | string | ARN of the encrypting CMK; required for any consumer's `kms:Decrypt` grant. |

This module deliberately does NOT output `secret_string` for any
secret. Outputs land in Terraform state and in the consuming
config's plan output; surfacing values there would defeat the
purpose of using Secrets Manager.

## Cost expectations

- Secrets Manager: $0.40 per secret per month plus $0.05 per 10,000
  GetSecretValue calls. At seven secrets that is $2.80/month fixed.
- KMS CMK: $1/month plus per-request KMS charges (negligible at dev
  query volume).
- Total fixed cost of this module: about $3.80/month.

## Consuming outputs from other configs

Downstream services (auth service IAM role policy, billing service
IAM role policy, summarization service IAM role policy) read these
ARNs via a `terraform_remote_state` data source pointing at this
config's state:

    data "terraform_remote_state" "secrets" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/secrets/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.secrets.outputs.secret_arns["jwt-signing-secret"]
    #   data.terraform_remote_state.secrets.outputs.kms_key_arn
