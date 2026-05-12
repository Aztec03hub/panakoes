# SES Bootstrap

## Purpose

Bring a fresh Panakoes environment from "no SES" to "the notification
service can send a transactional email." Covers domain identity
verification (DKIM via Cloudflare DNS), sandbox-mode recipient
verification, IAM-user-based SMTP credential derivation, AWS Secrets
Manager population, smoke send, and the sandbox-to-production exit
checklist.

## When to use this runbook

- Standing up the notification service in a new environment for the
  first time.
- Rotating SES SMTP credentials (re-run sections 4 and 5 only).
- The SES domain identity dropped to `FAILED` and DKIM needs to be
  re-issued.
- Lifting SES out of sandbox mode in preparation for production
  customer email.

## Prerequisites

- AWS CLI configured with the `panakoes-admin` profile pointing at
  account `659225405128`. Verify:

      aws --profile panakoes-admin sts get-caller-identity

- Terraform 1.7+ on the local machine. Verify:

      terraform version

- Cloudflare DNS edit access for the sender domain
  (`lafayettelabs.com`). Phil holds these credentials; this runbook
  prints the records to paste and does NOT attempt programmatic
  Cloudflare writes.

- The `infra/dev/secrets/` module has been applied; the placeholder
  secret `panakoes-dev/ses-smtp-credentials` exists. Verify:

      aws --profile panakoes-admin --region us-east-1 secretsmanager describe-secret \
        --secret-id panakoes-dev/ses-smtp-credentials

## Procedure

### 1. Apply the SES Terraform module

    cd infra/dev/ses
    AWS_PROFILE=panakoes-admin terraform init
    AWS_PROFILE=panakoes-admin terraform plan -out=tfplan
    AWS_PROFILE=panakoes-admin terraform apply tfplan
    rm tfplan

The plan adds seven resources: domain identity, email identity,
configuration set, CloudWatch event destination, IAM user, IAM user
policy, IAM access key. No surprises beyond those.

### 2. Publish DKIM CNAMEs in Cloudflare

Read the ready-to-paste record list:

    cd infra/dev/ses
    terraform output -json dkim_cname_records | jq .

Each entry has `name`, `type` (`CNAME`), `value`, `proxy` (`DNS only`),
and `ttl` (`Auto`). In the Cloudflare dashboard for `lafayettelabs.com`,
under DNS / Records, add three records that mirror the JSON exactly.
Set the proxy status to "DNS only" (grey cloud), not "Proxied"; SES
DKIM validation traverses the actual CNAME chain and breaks if
Cloudflare proxies the records.

Wait one to five minutes for propagation. Confirm verification flipped:

    aws --profile panakoes-admin --region us-east-1 sesv2 get-email-identity \
      --email-identity lafayettelabs.com \
      --query '{Verified:VerifiedForSendingStatus,DkimStatus:DkimAttributes.Status}'

Expected: `Verified: true`, `DkimStatus: SUCCESS`.

### 3. Confirm the email-identity verification link

While the domain identity authenticates via DKIM, the sandbox-mode
recipient identity (`phil@lafayettelabs.com`) authenticates via an
emailed confirmation link. AWS sent it on the `terraform apply` in
step 1; open the message titled "Amazon Web Services - Email Address
Verification Request" and click the link. Confirm:

    aws --profile panakoes-admin --region us-east-1 sesv2 get-email-identity \
      --email-identity phil@lafayettelabs.com \
      --query VerifiedForSendingStatus

Expected: `true`.

### 4. Derive SMTP credentials and write to Secrets Manager

SES SMTP authenticates with a username (= IAM access key id) and a
password (= base64 of a single version byte 0x04 followed by an
HMAC-SHA256 chain over the literal string `SendRawEmail`, using a
SigV4-style derived key with date `11111111`, region `us-east-1`,
service `ses`, terminal `aws4_request`). The conversion is offline; no
AWS API call required to compute the password.

Run, from `infra/dev/ses`:

    AWS_PROFILE=panakoes-admin python3 - <<'PY'
    import json, hmac, hashlib, base64, subprocess
    def tf_out(n):
        return subprocess.check_output(['terraform','output','-raw',n]).decode().strip()
    access_key_id = tf_out('smtp_access_key_id')
    secret = tf_out('smtp_secret_access_key')
    def sign(k, m):
        return hmac.new(k, m.encode(), hashlib.sha256).digest()
    sig = sign(('AWS4'+secret).encode(), '11111111')
    sig = sign(sig, 'us-east-1')
    sig = sign(sig, 'ses')
    sig = sign(sig, 'aws4_request')
    sig = sign(sig, 'SendRawEmail')
    password = base64.b64encode(bytes([0x04]) + sig).decode()
    payload = json.dumps({
        'username': access_key_id,
        'password': password,
        'host': 'email-smtp.us-east-1.amazonaws.com',
        'port': 587,
    })
    subprocess.run([
        'aws','--profile','panakoes-admin','--region','us-east-1',
        'secretsmanager','put-secret-value',
        '--secret-id','panakoes-dev/ses-smtp-credentials',
        '--secret-string', payload,
    ], check=True, stdout=subprocess.DEVNULL)
    print('OK')
    PY

The heredoc writes nothing to disk and prints nothing sensitive. The
secret material moves directly from `terraform output -raw` into the
`put-secret-value` argv. Do NOT save the payload to a file, do NOT
`echo` either field, and do NOT pipe through `tee`.

If you need to rotate later, run `terraform apply -replace=aws_iam_access_key.ses_smtp`
to mint a fresh access key, then repeat this step. The old access key
is destroyed by Terraform, so any in-flight sends fail fast rather
than silently using stale credentials.

### 5. Smoke send

The notification service consumes the secret via its boto3 / SDK
default credential chain (the production code path goes through the
SES API directly; SMTP is only the credential-shape contract). Issue
a one-off send through whichever path is convenient:

Option A (SDK path, easiest):

    aws --profile panakoes-admin --region us-east-1 sesv2 send-email \
      --from-email-address phil@lafayettelabs.com \
      --destination ToAddresses=phil@lafayettelabs.com \
      --content 'Simple={Subject={Data="Panakoes notification service smoke test"},Body={Text={Data="If you see this, SES bootstrap is complete."}}}' \
      --configuration-set-name panakoes-dev

Option B (SMTP path, exercises the credentials):

    python3 - <<'PY'
    import json, smtplib, subprocess
    from email.message import EmailMessage
    raw = subprocess.check_output(['aws','--profile','panakoes-admin','--region','us-east-1',
        'secretsmanager','get-secret-value','--secret-id','panakoes-dev/ses-smtp-credentials',
        '--query','SecretString','--output','text']).decode()
    c = json.loads(raw)
    msg = EmailMessage()
    msg['From'] = 'phil@lafayettelabs.com'
    msg['To'] = 'phil@lafayettelabs.com'
    msg['Subject'] = 'Panakoes notification service smoke test'
    msg.set_content('If you see this, SES bootstrap is complete.')
    with smtplib.SMTP(c['host'], c['port']) as s:
        s.starttls()
        s.login(c['username'], c['password'])
        s.send_message(msg)
    print('sent')
    PY

### 6. Verify delivery

Inbox check is necessary but not sufficient; also confirm the send
registered against the configuration set:

    aws --profile panakoes-admin --region us-east-1 ses get-send-statistics \
      --query 'SendDataPoints[-1]'

`DeliveryAttempts` should have ticked up by one with no `Bounces` or
`Complaints`.

### 7. Actual dev-environment first-bootstrap outcome (2026-05-11)

Recorded here so a future operator can compare expected output to
what reality delivered the first time this runbook ran end-to-end:

- Email identity `phil@lafayettelabs.com`: `VerificationStatus: Success`
  after Phil clicked the AWS verification email.
- Domain identity `lafayettelabs.com`: `VerificationStatus: Pending`
  at bootstrap close. Domain verification does NOT block the smoke
  send because SES sandbox permits verified-email -> verified-email
  sends regardless of domain status. Domain verification stays pending
  until the records in step 2 (DKIM) AND the records below (domain
  identity TXT) land in Cloudflare DNS.
- Smoke send via `aws ses send-email` (SDK path, option A from step 5):
  succeeded with `MessageId` `0100019e18b8bc0c-94e16e86-a23b-458a-86b4-10a552c4219d-000000`.
- `aws ses get-send-statistics` ticked `DeliveryAttempts: 1` with
  zero `Bounces` / `Complaints` / `Rejects` against the
  `panakoes-dev` configuration set.
- Sandbox quota at bootstrap close: `Max24HourSend: 200`,
  `MaxSendRate: 1/sec`, `SentLast24Hours: 0` (incremented after the
  send).

### 8. Cloudflare DNS records for domain identity verification

Step 2 covers the three DKIM CNAMEs. The domain identity itself
also requires a verification TXT record. Add ALL FOUR records to the
`lafayettelabs.com` zone in the Cloudflare dashboard (DNS / Records),
all proxy-status "DNS only" (grey cloud), all TTL "Auto".

Domain-identity verification TXT (one record):

| Name | Type | Content |
|---|---|---|
| `_amazonses.lafayettelabs.com` | TXT | `PIdxjSiCAsfvNlLv3fcyxDFTLGbD8g4107hrXYwBa1s=` |

DKIM CNAMEs (three records, value is the AWS-side DKIM endpoint):

| Name | Type | Content |
|---|---|---|
| `vaohnthjvqhxa5yltnb3zwitdxzy7zi2._domainkey.lafayettelabs.com` | CNAME | `vaohnthjvqhxa5yltnb3zwitdxzy7zi2.dkim.amazonses.com` |
| `a4ggxzwagaapmfv3v5y4wash3av24u64._domainkey.lafayettelabs.com` | CNAME | `a4ggxzwagaapmfv3v5y4wash3av24u64.dkim.amazonses.com` |
| `3h5c7lu5u6xm4tzvgps7aq3xp7uiiiip._domainkey.lafayettelabs.com` | CNAME | `3h5c7lu5u6xm4tzvgps7aq3xp7uiiiip.dkim.amazonses.com` |

To re-derive these at any point (the TXT verification token persists
across re-runs unless the identity is destroyed):

    aws --profile panakoes-admin --region us-east-1 ses verify-domain-identity \
      --domain lafayettelabs.com
    aws --profile panakoes-admin --region us-east-1 ses get-identity-dkim-attributes \
      --identities lafayettelabs.com

After the records land (1-5 minutes propagation), confirm:

    aws --profile panakoes-admin --region us-east-1 ses get-identity-verification-attributes \
      --identities lafayettelabs.com \
      --query 'VerificationAttributes."lafayettelabs.com".VerificationStatus'
    aws --profile panakoes-admin --region us-east-1 ses get-identity-dkim-attributes \
      --identities lafayettelabs.com \
      --query 'DkimAttributes."lafayettelabs.com".DkimVerificationStatus'

Both should return `"Success"`. Once they do, SES will sign outbound
mail from `@lafayettelabs.com` senders, which is required for the
sandbox-to-production exit case (step "Production exit checklist"
below).

## Verification

- `aws sesv2 get-email-identity --email-identity lafayettelabs.com` returns `VerifiedForSendingStatus: true`.
- `aws sesv2 get-email-identity --email-identity phil@lafayettelabs.com` returns `VerifiedForSendingStatus: true`.
- `aws secretsmanager describe-secret --secret-id panakoes-dev/ses-smtp-credentials` shows a `LastChangedDate` newer than the Terraform creation timestamp (i.e., the placeholder was overwritten).
- A test message arrives at the recipient inbox within one minute.
- `aws ses get-send-statistics` shows the new send.

## Rollback

- The Terraform module is `terraform destroy`-able. Destroying tears
  down the configuration set, both identities, the IAM user, the
  access key, and the policy.
- The `panakoes-dev/ses-smtp-credentials` secret persists across
  destroy (it lives in `infra/dev/secrets/`, not this module).
  To restore the placeholder, write the original JSON shape:

      aws --profile panakoes-admin --region us-east-1 secretsmanager put-secret-value \
        --secret-id panakoes-dev/ses-smtp-credentials \
        --secret-string '{"username":"REPLACE_ME_AFTER_APPLY","password":"REPLACE_ME_AFTER_APPLY","host":"email-smtp.us-east-1.amazonaws.com","port":587}'

- The DKIM CNAMEs in Cloudflare can stay or be removed; with the
  identity destroyed they resolve to nothing AWS validates against.

## Production exit checklist

Sandbox mode caps the account at 200 sends per day and rejects any
recipient that has not opted in via identity verification. Lift it
once the operator is ready to send to real customers:

1. Confirm DKIM, SPF, and DMARC are aligned on `lafayettelabs.com`.
   SES handles DKIM via the CNAMEs from step 2. SPF: add
   `v=spf1 include:amazonses.com ~all` if no SPF record exists, or
   merge `include:amazonses.com` into the existing one. DMARC: add
   `_dmarc.lafayettelabs.com` TXT `v=DMARC1; p=quarantine; rua=mailto:phil@lafayettelabs.com`
   (start at `p=none`, tighten to `quarantine`, then `reject`).
2. Wire a bounce / complaint feedback loop. Create an SNS topic, add
   it as a SES configuration-set event destination for `BOUNCE` and
   `COMPLAINT`, subscribe a Lambda or SQS queue that removes the
   hard-bouncing address from any internal send list. SES will
   auto-pause the account if bounce rate exceeds 5% or complaint rate
   exceeds 0.1% over a rolling window.
3. Publish a working unsubscribe mechanism for non-transactional
   email, and document for AWS how transactional sends are
   distinguished (the case reviewer asks).
4. Open a quota-increase case from the SES console: "Request
   production access." Provide expected daily volume, the bounce /
   complaint handling story, the unsubscribe mechanism, and a sample
   of the email body. AWS typically approves dev-tier portfolios in
   under 24 hours.
5. After approval, the `Max24HourSend` quota rises and the
   verified-recipient restriction disappears. No Terraform change
   required; sandbox status is account-wide, not in the module.

## References

- `infra/dev/ses/README.md`: what each Terraform resource does.
- `infra/dev/secrets/README.md`: where the SMTP credentials secret
  lives and how it interacts with `lifecycle.ignore_changes`.
- AWS docs: [SES SMTP credentials](https://docs.aws.amazon.com/ses/latest/dg/smtp-credentials.html),
  [SES sending authorization](https://docs.aws.amazon.com/ses/latest/dg/sending-authorization.html),
  [SES production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html).
- Memory: `aws_secrets_panakoes_dev`, `aws_account_panakoes`, `phil_contact_info`.
