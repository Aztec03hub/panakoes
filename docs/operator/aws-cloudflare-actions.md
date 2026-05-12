# AWS + Cloudflare Operator Actions

**Audience:** Phil. The owner of the AWS account + Cloudflare account that Panakoes deploys into.

**Purpose:** every action that costs money, requires a console click, requires a real credential, or otherwise can't be safely automated by Claude or by CI is captured here. Each item has a concrete command, an expected output, a cost estimate (where applicable), and a rollback path.

**Working agreement:**
- Claude does not run `terraform apply`, `aws *`, or any console action that costs money or mutates non-local state. Claude writes the modules, drafts the commands, and verifies state after you run them.
- This document is the source of truth for "what's waiting on Phil." Every item here gets a status indicator: `[ ]` = not done, `[~]` = in progress, `[x]` = done. Update as we go.

**Total estimated dev-environment cost when everything in this guide is applied:** ~$30-50/month (NAT gateway dominates at ~$32, AWS Backup at $0-3, GuardDuty at $3-5, Aurora Serverless v2 minimum at $0 when paused, CloudFront + S3 + DynamoDB are essentially free at this volume). See section by section.

---

## Section A. Already-done items (confirm only)

These were done in earlier sessions per memory + task list. Confirm each with the verify command.

### A.1. AWS account bootstrap (root MFA + Budgets + CloudTrail + admin IAM user)

`[x]` per task #50.

**Verify:**
```bash
aws sts get-caller-identity --profile panakoes-admin
aws budgets describe-budgets --account-id $(aws sts get-caller-identity --query Account --output text) --profile panakoes-admin
aws cloudtrail describe-trails --profile panakoes-admin
```

If any of these fail, Section A.1 is NOT actually done; see `docs/runbooks/dev-troubleshooting.md` for re-bootstrap.

### A.2. Terraform remote state backend (S3 + KMS, S3-native lockfile)

`[x]` per task #32. State bucket is `panakoes-tf-state-b291597a`, KMS key is `dce57db1-ea8c-46dd-b60a-c8de022860af`. State locking uses S3 conditional writes (`use_lockfile = true`, Terraform 1.10+); the legacy `panakoes-tf-lock` DynamoDB table was retired 2026-05-09 (issue #153). Every module's backend block references the bucket + key + KMS arn.

**Verify:**
```bash
aws s3 ls s3://panakoes-tf-state-b291597a --profile panakoes-admin
```

Should list per-module `terraform.tfstate` files.

### A.3. GitHub Actions OIDC federation

`[x]` per task #31. The IAM role `panakoes-github-actions` is configured to trust GitHub's OIDC issuer for the `Aztec03hub/panakoes` repo.

**Verify:**
```bash
aws iam get-role --role-name panakoes-github-actions --profile panakoes-admin --query 'Role.AssumeRolePolicyDocument.Statement[0].Condition'
```

Output should include `token.actions.githubusercontent.com`.

### A.4. Domains registered

`[x]` `panakoes.com` and `lafayettelabs.com` registered at Cloudflare per tasks #15 and #28.

`[x]` `lafayettelabs.com` already deployed to Cloudflare Pages per task #49.

### A.5. AUTO_UPDATE_PAT GitHub fine-grained token

`[x]` Created during 2026-05-08 session per ADR-028. Stored as `secrets.AUTO_UPDATE_PAT` in the repo.

**Note for renewal:** GitHub fine-grained PATs default to 90 days. Token issued on 2026-05-08 expires around **2026-08-06**. See Section H.1 for renewal procedure.

---

## Section B. AWS Activate Founders application (submit)

**Status:** `[ ]` Application content is drafted at `docs/aws_activate_application.md`. Submission has not happened yet.

**Why this matters first:** Activate Founders gives $1,000 in AWS credits for early-stage startups. Submitting before applying any chargeable Terraform reduces your out-of-pocket dev cost to roughly zero for the first 6-12 months. **Do this before Section C.**

**Steps:**

1. Sign in to your AWS account and go to https://aws.amazon.com/activate/.
2. Click "Apply for Founders" (or whichever tier matches; LaFayette Labs LLC + open-source project + technical founder qualifies for Founders).
3. The application asks for:
   - Company name → "LaFayette Labs LLC"
   - Stage / funding → use the wording from `docs/aws_activate_application.md` Section 1.
   - Product description → paste from Section 2 of that doc.
   - Architecture → paste from Section 3.
   - Why AWS → paste from Section 4.
   - LinkedIn / website → `https://lafayettelabs.com` (deployed) and your personal LinkedIn.
4. Submit. Approvals usually arrive in 7-14 days. Credits land in the AWS account automatically.

**Verify after submission:**

Approval email arrives at `plafaydev@gmail.com`. Once credits show in the AWS Billing console under "Credits," you're set.

**Do this NOW, before any `terraform apply` in Section C.**

---

## Section C. Terraform apply walkthrough (dev environment)

The infra modules are all written and committed. The order below respects cross-module dependencies (a module's `terraform_remote_state` data sources only resolve once the upstream module's state exists in S3). For the live apply state, see `docs/STATUS.md` Section 4 (the source of truth for "what's deployed right now"); the table here is the apply-order reference, not the status board.

**Applied to dev environment as of 2026-05-09:**

`bootstrap`, `global`, `network`, `data`, `admin-state`, `storage`, `secrets` (placeholders), `ecr`, `iam`, `observability`, `events`, `waf`, `frontend`. Module `api-gateway` is in a partial-applied state per memory `aws_api_gateway_partial_apply.md`. All others (`security`, `auth-db`, `vpc-endpoints`, `backup`, `step-functions`, `batch`, `cost-anomaly-monitor`) are coded but not yet applied; cost discipline + dependencies on services-not-yet-deployed are the gating reasons.

**Frontend module outputs (recorded after PR #160 v2 logs fix landed clean):**

- CloudFront distribution id: `E42AJI7SB5K1N`
- CloudFront domain name: `dmaopcm3hnxog.cloudfront.net` (use this for the Section F CNAME)
- Origin S3 bucket: `panakoes-dev-frontend-9d80ace6`
- Logs S3 bucket: `panakoes-dev-frontend-logs-ef03950e` (CWL Delivery v2 sink per ADR-034)
- KMS alias: `alias/panakoes-dev-frontend`

**Pre-flight (do once):**

```bash
cd ~/projects/panakoes
which terraform   # verify >= 1.10 (use_lockfile S3 backend support)
aws sts get-caller-identity --profile panakoes-admin   # verify you're hitting the right account
```

If those don't work, fix before proceeding.

**Apply pattern for every module:**

```bash
cd infra/dev/<module>
terraform init     # first time only per module
terraform plan -out=tfplan
# READ THE PLAN. Confirm resource counts and types match the module README.
terraform apply tfplan
rm tfplan
```

Always `terraform plan` before `apply`. Never `apply` blind.

### Apply order (dependency-respecting)

| # | Module | Cost/mo | Why first | Notes |
|---|---|---:|---|---|
| 1 | `infra/dev/network` | ~$32 | Every other module attaches to this VPC | NAT Gateway is the cost driver. Single-AZ NAT in dev (intentional). |
| 2 | `infra/dev/data` | ~$0 | DynamoDB tables (PAY_PER_REQUEST + free-tier-friendly) | ingestion, audit-log, streaming-sessions |
| 3 | `infra/dev/admin-state` | ~$0 | DynamoDB tables for Tier 2/3 admin dashboard | cost-cache, tenant-cost-rollup, lifecycle-state, alert-state |
| 4 | `infra/dev/storage` | ~$0-1 | S3 buckets (audio-uploads, transcripts, log-archive) | KMS keys add ~$1/key/month; 3 buckets = $3 |
| 5 | `infra/dev/secrets` | ~$0.40 | Secrets Manager secrets with placeholder values | $0.40/secret/month × 9 = ~$3.60. **You populate real values in Section D.** |
| 6 | `infra/dev/ecr` | ~$0 | 11 ECR repos for service container images | Storage is per-GB but you have nothing yet. |
| 7 | `infra/dev/iam` | ~$0 | Per-service task roles + execution roles | IAM is free. |
| 8 | `infra/dev/observability` | ~$0-1 | CloudWatch log groups, S3 archive lifecycle | CloudWatch ingestion is per-GB; dev volume is tiny. |
| 9 | `infra/dev/events` | ~$0 | EventBridge custom bus + SNS topic + SQS queues | $1/million events; dev volume is nothing. |
| 10 | `infra/dev/security` | ~$8-10 | GuardDuty + Config + Security Hub | Free tier covers Config for 30 days; GuardDuty is ~$3-5/mo at this volume. |
| 11 | `infra/dev/auth-db` | ~$0-43 | Aurora Serverless v2 (auto-pause on idle) | **0.5 ACU minimum × $0.12/ACU-hour = $43/mo if always-on.** Verify auto-pause is configured in the module before applying. |
| 12 | `infra/dev/vpc-endpoints` | ~$7 | VPC endpoints for S3/DynamoDB/KMS/Secrets Manager | $0.01/hour/endpoint × 7 endpoints = $51/mo? Verify this; might be expensive. **CHECK BEFORE APPLYING.** |
| 13 | `infra/dev/waf` | ~$5 | AWS WAF on the public CloudFront distribution | $5/web ACL/mo + $1/rule. |
| 14 | `infra/dev/backup` | ~$0-3 | AWS Backup vault + plan | Snapshots are per-GB; dev volume is small. |
| 15 | `infra/dev/api-gateway` | ~$0 | API Gateway HTTP API | $1/million requests; free until production traffic. |
| 16 | `infra/dev/step-functions` | ~$0 | STANDARD workflow for long-audio chunking | $25/million state transitions; dev volume is nothing. |
| 17 | `infra/dev/batch` | ~$0 | AWS Batch compute environment + job queue | Compute itself is per-Spot-instance-hour; resource definitions are free. |
| 18 | `infra/dev/frontend` | ~$0-1 | CloudFront + S3 origin for the SvelteKit admin app | CloudFront is $0.085/GB egress; dev traffic is nothing. |

**Estimated steady-state dev cost: $50-95/month** depending on Aurora Serverless idle behavior and VPC endpoints.

**RED FLAGS BEFORE YOU APPLY:**

- Section C.11 (Aurora Serverless v2): if auto-pause is NOT configured, this is $43/mo even idle. Read the module README before applying.
- Section C.12 (VPC endpoints): verify endpoint count. If you see 7+ Interface endpoints, that's $51/mo. You can defer this module until production.
- Section C.13 (WAF) + C.14 (Backup) + C.10 (security): all genuinely useful in production but not strictly required for dev. **Defer C.13, C.14 until you need them.**

**Recommended dev-only minimum stack (cheapest viable):** modules 1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 17, 18. Skip 10, 11, 12, 13, 14 until needed. That puts you at roughly $35-40/mo.

### Per-module READMEs

Each module's README has a one-line description plus the apply command. Spot-check these before running:

```bash
for m in infra/dev/*/; do
  echo "=== $m ==="
  head -5 "$m/README.md" 2>/dev/null
done
```

### Rollback

Every module is `terraform destroy`-clean. If you mess something up:

```bash
cd infra/dev/<module>
terraform destroy
```

DynamoDB tables have `deletion_protection_enabled = true`, so destroy will fail on those - flip the flag, re-apply, then destroy. Documented in each module's README.

---

## Section D. Populate AWS Secrets Manager values

The `infra/dev/secrets` module creates 9 secret resources with **placeholder** strings (per `lifecycle { ignore_changes = [secret_string] }`). You write the real values via CLI; subsequent `terraform apply`s do not revert them.

**Secrets to populate:**

| Secret name | What to put in | Source |
|---|---|---|
| `panakoes-dev/jwt-signing-secret` | A 64+ char random string | Generate: `openssl rand -hex 32` |
| `panakoes-dev/anthropic-api-key` | Your Anthropic API key | https://console.anthropic.com/ → API keys → create |
| `panakoes-dev/groq-api-key` | Your Groq API key | https://console.groq.com/keys → Create API Key. Required by the transcribe-worker Lambda + ingestion-api Groq backend (ADR-009 default). |
| `panakoes-dev/openai-api-key` | Your OpenAI API key | https://platform.openai.com/api-keys → Create new secret key. Reserved for the planned OpenAI Whisper transcriber backend; not yet consumed. |
| `panakoes-dev/stripe-test-key` | Stripe test secret key | https://dashboard.stripe.com/test/apikeys → Secret key |
| `panakoes-dev/stripe-webhook-signing-secret` | Stripe webhook signing secret | Created when you configure the webhook endpoint in Stripe (Section F.3) |
| `panakoes-dev/postgres-auth-db-password` | A strong random password | Generate: `openssl rand -base64 32` |
| `panakoes-dev/database-url` | Constructed: `postgres://panakoes:<password>@<auth-db-endpoint>:5432/panakoes` | Endpoint comes from `terraform output` of `infra/dev/auth-db` after it applies |
| `panakoes-dev/ses-smtp-credentials` | SES SMTP user + password | https://console.aws.amazon.com/ses/ → SMTP settings → Create SMTP credentials |

**Command pattern:**

```bash
aws secretsmanager put-secret-value \
  --secret-id panakoes-dev/jwt-signing-secret \
  --secret-string "$(openssl rand -hex 32)" \
  --profile panakoes-admin
```

For multi-value secrets (database-url after Aurora applies), generate the value first, then feed it in.

**Verify (do NOT echo the actual secret to terminal in production):**

```bash
aws secretsmanager get-secret-value --secret-id panakoes-dev/jwt-signing-secret --profile panakoes-admin --query 'SecretString' --output text | wc -c
```

Expect 65 (64 hex chars + trailing newline). If you see ~10 chars, that's the placeholder; the real value didn't write.

---

## Section E. Build + push container images to ECR

The `infra/dev/ecr` module creates 11 repositories. The services exist as code but their images haven't been built/pushed.

**Per-service pattern:**

```bash
SERVICE=auth   # or ingestion-api, query-api, etc.
ACCOUNT=$(aws sts get-caller-identity --query Account --output text --profile panakoes-admin)
REGION=us-east-1

aws ecr get-login-password --region $REGION --profile panakoes-admin | \
  docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

cd services/$SERVICE
docker build -t panakoes-dev-$SERVICE:latest .
docker tag panakoes-dev-$SERVICE:latest "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/panakoes-dev-$SERVICE:latest"
docker push "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/panakoes-dev-$SERVICE:latest"
```

**Services to build (in order, build the libs/template first if any service depends on them locally):**

| Service | Dockerfile location | Notes |
|---|---|---|
| auth | services/auth/Dockerfile | TypeScript, Hono, Better-Auth |
| ingestion-api | services/ingestion-api/Dockerfile | FastAPI |
| query-api | services/query-api/Dockerfile | FastAPI |
| summarization | services/summarization/Dockerfile | FastAPI, calls Anthropic |
| notification | services/notification/Dockerfile | FastAPI |
| session-manager | services/session-manager/Dockerfile | FastAPI |
| billing | services/billing/Dockerfile | Stripe webhook handler |
| event-router | services/event-router/Dockerfile | Lambda container image |
| gpu-spawner | services/gpu-spawner/Dockerfile | Lambda |
| cost-api | services/cost-api/Dockerfile | FastAPI (Tier 2 admin) |
| admin-api | services/admin-api/Dockerfile | FastAPI (Tier 3 admin) |
| transcriber-batch | (deferred - not yet implemented) | Skip for now |
| transcriber-stream | (deferred - runs on the GPU AMI, not in ECR) | Skip for now |

**Cost:** ECR storage is $0.10/GB/month. Each image is ~200-400MB. 11 images × 300MB = 3.3GB = ~$0.33/month. Trivial.

**Verify:**

```bash
aws ecr describe-images --repository-name panakoes-dev-auth --profile panakoes-admin --query 'imageDetails[].imageTags'
```

Expect `["latest"]` (or whatever tag you pushed).

---

## Section F. Cloudflare DNS configuration

Once `infra/dev/frontend` (CloudFront + S3) and `infra/dev/api-gateway` (HTTP API) are applied, you need to point your domains at them via Cloudflare DNS.

### F.1. Get the AWS-side hostnames

```bash
cd infra/dev/frontend && terraform output cloudfront_domain_name
cd infra/dev/api-gateway && terraform output api_gateway_endpoint
```

You'll get something like:
- CloudFront: `d1234abcd.cloudfront.net`
- API Gateway: `abc1234.execute-api.us-east-1.amazonaws.com`

### F.2. Add Cloudflare DNS records

Sign in to Cloudflare → `panakoes.com` → DNS → Records → Add record.

| Type | Name | Target | Proxy | TTL |
|---|---|---|---|---|
| CNAME | `admin` | `d1234abcd.cloudfront.net` | Off (DNS only) | Auto |
| CNAME | `api` | `abc1234.execute-api.us-east-1.amazonaws.com` | Off | Auto |

Why proxy off: CloudFront / API Gateway already handle TLS termination + edge caching. Cloudflare's proxy on top would double-cache and break some headers.

### F.3. Stripe webhook endpoint (if billing is wired)

Once `api.panakoes.com` resolves:
1. Go to https://dashboard.stripe.com/test/webhooks → Add endpoint.
2. Endpoint URL: `https://api.panakoes.com/billing/webhook`.
3. Events to listen for: at minimum `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_succeeded`, `invoice.payment_failed`.
4. Stripe shows the signing secret. Copy it into `panakoes-dev/stripe-webhook-signing-secret` (Section D).

### F.4. Verify

```bash
dig admin.panakoes.com +short
dig api.panakoes.com +short
```

Both should resolve to the AWS-side hostnames.

---

## Section G. Cost Explorer + Budget verification

### G.1. Enable Cost Explorer

Cost Explorer is free for read-only queries through the console but charges $0.01 per programmatic API request beyond 1,000/month.

The `cost-api` service hits Cost Explorer programmatically (read-through cache mitigates volume). Enable it once via the console:

1. Sign in to AWS Console → Billing → Cost Explorer.
2. Click "Enable Cost Explorer." First-time enablement takes 24 hours to populate data.
3. Once enabled, the IAM permissions in `infra/dev/iam` (the cost-api task role) are sufficient.

**Verify:**
```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-04-01,End=2026-05-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --profile panakoes-admin
```

If this returns data, cost-api is unblocked.

### G.2. Verify Budgets alarm routes to your real email

The bootstrap session created a Budgets alarm. Confirm the email address is yours and the threshold is what you want.

```bash
aws budgets describe-budgets --account-id $(aws sts get-caller-identity --query Account --output text --profile panakoes-admin) --profile panakoes-admin
aws budgets describe-subscribers-for-notification --account-id $(aws sts get-caller-identity --query Account --output text --profile panakoes-admin) --budget-name <your-budget-name> --notification '{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE","NotificationState":"OK"}' --profile panakoes-admin
```

Subscriber should be `plafaydev@gmail.com` (or your preferred address). If not, update via the console.

---

## Section H. Maintenance reminders

### H.1. AUTO_UPDATE_PAT 90-day rotation

The fine-grained GitHub PAT used by `auto-update-prs.yml` expires around **2026-08-06** (90 days from 2026-05-08). When it expires, every cascade-rebase job fails and PRs stack up BEHIND status.

**Calendar reminder:** add a recurring 80-day calendar event titled "Rotate AUTO_UPDATE_PAT" with this checklist in the body:

1. Go to https://github.com/settings/personal-access-tokens.
2. Find `panakoes-auto-update-prs`. Click Regenerate.
3. Set expiration to 90 days from today.
4. Same scopes as before: `Contents: read+write`, `Pull requests: read+write`, `Workflows: read+write`. Repository scope: `Aztec03hub/panakoes` only.
5. Copy the new token.
6. Update the repo secret: `gh secret set AUTO_UPDATE_PAT --repo Aztec03hub/panakoes` (paste the token).
7. Verify with a test PR: open a no-op PR, confirm `auto-update-prs` workflow fires and rebases peer PRs.

If the token expires before rotation: `auto-update-prs` jobs error with 401. Cascade halts but is recoverable - rotate the token and re-run failed workflows.

### H.2. Anthropic API key rotation

Recommended every 6 months or whenever a team member with key access leaves. Procedure mirrors AUTO_UPDATE_PAT:

1. Generate new key at https://console.anthropic.com/ → API keys.
2. Update `panakoes-dev/anthropic-api-key` via Section D's command pattern.
3. Trigger the summarization service to reload (restart task).
4. Revoke the old key only after confirming the new one works.

### H.3. AWS access key audit

Quarterly: confirm no long-lived AWS access keys exist in the account (the design uses OIDC federation only).

```bash
aws iam list-users --profile panakoes-admin --query 'Users[].UserName' --output text | \
  xargs -n1 -I{} aws iam list-access-keys --user-name {} --profile panakoes-admin --query 'AccessKeyMetadata[].AccessKeyId' --output text
```

Should return empty (or only your personal admin key, which is the bootstrap key).

### H.4. CloudWatch log retention

CloudWatch log groups in `infra/dev/observability` are configured for 30-day retention. Confirm quarterly that they haven't drifted (someone set retention to "Never expire" by accident is the canonical failure mode).

```bash
aws logs describe-log-groups --profile panakoes-admin --query 'logGroups[?retentionInDays==null]'
```

Empty array = good. Any group listed = retention drifted.

---

## Section I. Production readiness gate (later)

Before flipping any service to production, work through this checklist:

- `[ ]` Stripe live keys populated (replace `panakoes-dev/stripe-test-key` with `panakoes-prod/stripe-live-key` in a separate `prod` environment workspace).
- `[ ]` SES out of sandbox (https://console.aws.amazon.com/ses/ → request production access).
- `[ ]` Domain verified for SES (DKIM TXT records added at Cloudflare).
- `[ ]` Production AWS account separate from dev (cross-account billing rollup, isolated IAM).
- `[ ]` Multi-AZ NAT in production (current dev uses single-AZ for cost).
- `[ ]` Aurora Serverless minimum ACU bumped from 0.5 to whatever production traffic dictates.
- `[ ]` GuardDuty findings reviewed (no high-severity open findings).
- `[ ]` AWS Config rules pass.
- `[ ]` Penetration test (tabletop minimum).

This is a checklist for later. Not blocking the dev-environment work above.

---

## How to use this document

1. Open this file in a separate terminal/editor while we work.
2. We pick a Section item.
3. I (Claude) confirm the exact commands and any module-README context you need.
4. You execute on your machine.
5. You paste the output back to me.
6. I verify, mark the item `[x]`, commit the update, move on.

Iterate until we hit the bottom. Updates to this document land via normal PRs.
