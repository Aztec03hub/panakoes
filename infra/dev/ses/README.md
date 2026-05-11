# Dev Environment SES

Per-environment Terraform configuration that provisions Amazon SES for the
Panakoes `dev` environment: a verified domain identity for
`lafayettelabs.com`, an email identity for the primary sender, a
configuration set with CloudWatch event publishing, and a dedicated IAM
user whose access key derives the SMTP credentials consumed by the
notification service.

## What this creates

| Resource | Name | Purpose |
|---|---|---|
| `aws_sesv2_email_identity.domain` | `lafayettelabs.com` | DKIM-verified domain; allows any `*@lafayettelabs.com` sender |
| `aws_sesv2_email_identity.primary_sender` | `phil@lafayettelabs.com` | Verified recipient for sandbox-mode smoke tests |
| `aws_sesv2_configuration_set.this` | `panakoes-dev` | Central event publishing + reputation tracking |
| `aws_sesv2_configuration_set_event_destination.cloudwatch` | `panakoes-dev-cloudwatch` | Publishes SEND/DELIVERY/BOUNCE/COMPLAINT/OPEN/CLICK events to CloudWatch metrics |
| `aws_iam_user.ses_smtp` | `panakoes-dev-ses-smtp` | Holds the access key whose secret is converted to an SMTP password |
| `aws_iam_user_policy.ses_send` | `panakoes-dev-ses-send` | Scoped `ses:SendRawEmail` / `ses:SendEmail` on the two identity ARNs + the configuration set ARN, conditioned on `ses:FromAddress` |
| `aws_iam_access_key.ses_smtp` | (generated) | Access key id + secret; SMTP password derived via HMAC-SHA256 |

The IAM policy uses explicit Resource ARNs (no wildcards) and an
additional `ses:FromAddress` condition so a credential leak cannot send
from a different domain. SES remains in account-level sandbox mode for
dev; production exit happens via a quota-increase ticket, not in
Terraform.

## Apply

    cd infra/dev/ses
    AWS_PROFILE=panakoes-admin terraform init
    AWS_PROFILE=panakoes-admin terraform plan
    AWS_PROFILE=panakoes-admin terraform apply

## Post-apply DNS

The domain identity is `PENDING` until three DKIM CNAMEs land in
Cloudflare DNS for `lafayettelabs.com`. Read the ready-to-paste record
list from Terraform:

    terraform output -json dkim_cname_records | jq .

Phil adds each record at Cloudflare (DNS-only, not proxied; TTL Auto).
Verification flips to `SUCCESS` within minutes. Check with:

    aws sesv2 get-email-identity --email-identity lafayettelabs.com \
      --query VerifiedForSendingStatus

The email identity for `phil@lafayettelabs.com` does NOT require DNS;
AWS emails a confirmation link to that address on first apply and the
recipient clicks it.

## Post-apply SMTP credential population

SES SMTP credentials are derived from the IAM user's access key. The
helper script `scripts/ses_smtp_password.py` performs the conversion
(HMAC-SHA256 of `"SendRawEmail"` with the secret access key, plus the
AWS version-prefix byte 0x04, base64-encoded). The full procedure lives
in [`docs/runbooks/ses-bootstrap.md`](../../../docs/runbooks/ses-bootstrap.md);
the short form is:

    terraform output -raw smtp_access_key_id
    terraform output -raw smtp_secret_access_key
    # feed the secret to the helper, then write directly into Secrets Manager:
    # see runbook for the full one-liner

The result is written to `panakoes-dev/ses-smtp-credentials` (the
existing placeholder secret managed by `infra/dev/secrets/`) via `aws
secretsmanager put-secret-value`. No SMTP credential material is ever
echoed to commit history, logs, or terminal scrollback.

## State

- Backend: `s3://panakoes-tf-state-b291597a/dev/ses/terraform.tfstate`
- KMS key: `arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af`
- Lock: S3 native (`use_lockfile = true`)

## Production exit

Sandbox lift requires opening a support case ("Request production
access") with: expected volume, bounce / complaint handling story
(SNS topic on the configuration set's BOUNCE / COMPLAINT events), and
unsubscribe mechanics. Until the case is approved sends to
non-verified recipients return `MessageRejected`. The runbook tracks
the checklist; this module stays sandbox-only.
