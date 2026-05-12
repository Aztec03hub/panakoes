# infra/dev/budgets

Provisions a multi-threshold AWS Budgets cost-guardrail layer for the Panakoes `dev` account. Pairs with `infra/dev/cost-anomaly-monitor` (anomaly detection on top of historical baseline) to cover both fixed-cap overruns and statistical anomalies.

## What this provisions

- `aws_sns_topic.budget_alerts` (`panakoes-dev-budget-alerts`): fan-out hub for every budget threshold. SNS topic policy scopes `SNS:Publish` to `budgets.amazonaws.com` with `aws:SourceAccount` + `aws:SourceArn` conditions on the Panakoes account-id.
- `aws_sns_topic_subscription.budget_alerts_email`: email subscription on the topic, pointed at `var.alert_email` (defaults to `phil@lafayettelabs.com`).
- `aws_cloudwatch_metric_alarm.budget_100pct_actual` (`panakoes-dev-budget-100pct-actual`): trips on `AWS/SNS:NumberOfMessagesPublished >= 1` against the budget-alerts topic. Coarse signal; for per-threshold fidelity, swap to a Lambda subscriber that parses the SNS body and emits per-budget custom metrics.
- `aws_budgets_budget.account_monthly` (`panakoes-dev-account-monthly`): $100/mo account-wide. Four notifications: 50% ACTUAL (email), 80% ACTUAL (email + SNS), 80% FORECASTED (email + SNS proactive), 100% ACTUAL (email + SNS, drives the CW alarm).
- `aws_budgets_budget.ec2_monthly` (`panakoes-dev-ec2-monthly`): $35/mo on `Service = Amazon Elastic Compute Cloud - Compute`. Covers GPU Spot (transcribe-worker fan-out), NAT Gateway hours, t3 probes.
- `aws_budgets_budget.aurora_monthly` (`panakoes-dev-aurora-monthly`): $15/mo on `Service = Amazon Relational Database Service`. auth-db Aurora Serverless v2 cluster.
- `aws_budgets_budget.bedrock_monthly` (`panakoes-dev-bedrock-monthly`): $25/mo on `Service = Amazon Bedrock`. Summarization (Claude Haiku 4.5) + deep summary (Claude Sonnet 4.6) call passthrough.
- `aws_budgets_budget.cloudfront_s3_monthly` (`panakoes-dev-cloudfront-s3-monthly`): $5/mo on `Service IN (Amazon CloudFront, Amazon Simple Storage Service)`. Static SPA + asset buckets.
- `aws_budgets_budget.project_tag_monthly` (`panakoes-dev-project-tag-monthly`): $100/mo filtered on `TagKeyValue = user:Project$panakoes`. Forward-compatible with future staging / prod environments sharing this account.

Each service-specific budget fires two ACTUAL notifications (80%, 100%); email at both, SNS topic added at 100% so a per-service overrun still drives the CloudWatch alarm.

## SERVICE dimension values

AWS Budgets requires the EXACT Cost Explorer SERVICE dimension string. Verified against `aws ce get-dimension-values --dimension SERVICE` on 2026-05-11 against AWS account `659225405128`:

| Logical bucket | Canonical SERVICE string |
|---|---|
| EC2 | `Amazon Elastic Compute Cloud - Compute` |
| Aurora / RDS | `Amazon Relational Database Service` |
| Bedrock | `Amazon Bedrock` |
| CloudFront | `Amazon CloudFront` |
| S3 | `Amazon Simple Storage Service` |

Bedrock and CloudFront did not yet show in this account's CE dimension list (zero historic spend) but the canonical AWS-published strings are stable account-wide; Budgets accepts them ahead of first spend and starts evaluating once usage lands.

## Apply

```bash
cd infra/dev/budgets
terraform init
terraform plan -lock-timeout=2m -out=tfplan
terraform apply tfplan
```

Or via the repo helper:

```bash
scripts/tf.sh plan budgets
scripts/tf.sh apply budgets
```

This module is standalone; it is intentionally NOT wired into any orchestrating module list.

## Post-apply manual steps (required)

### 1. Confirm the SNS email subscription

On first apply, AWS sends an `AWS Notification - Subscription Confirmation` email to `phil@lafayettelabs.com` (forwards via Cloudflare Email Routing to `plafaydev@gmail.com`). Open that email and click the confirmation link.

Until the link is clicked, the subscription stays in `PendingConfirmation` state and AWS will NOT deliver SNS-side budget notifications. The direct EMAIL subscribers on each budget (separate from the SNS subscription) deliver independently and do not require confirmation.

Verify subscription state:

```bash
AWS_PROFILE=panakoes-admin aws sns list-subscriptions-by-topic \
  --topic-arn "$(terraform output -raw sns_topic_arn)"
```

`SubscriptionArn` will be the literal string `PendingConfirmation` until confirmed; afterwards it becomes a real ARN.

### 2. Activate the `Project` cost-allocation tag

The `project_tag_monthly` budget filters on `user:Project$panakoes`, but user-defined tags only feed Cost Explorer / Budgets after they are activated as cost-allocation tags. One-time activation per account:

1. Sign in to the AWS Billing console.
2. Navigate to `Cost allocation tags` (Billing & Cost Management).
3. Filter for `Project` under `User-defined cost allocation tags`.
4. Select the row and click `Activate`.

Status flips to `Active` immediately; historical-data backfill can take up to 24 hours, but going-forward spend tags within minutes.

Until activation lands, the `project_tag_monthly` budget evaluates against $0 spend and will never trip an alert. This is expected and benign.

### 3. (Optional) Decommission the AWS Console default budget

This account currently has a legacy `My Monthly Cost Budget` ($100/mo) created via the AWS Console during bootstrap. It is NOT managed by Terraform. After this module is applied and the email subscription is confirmed, delete the console budget to avoid duplicate notifications:

```bash
AWS_PROFILE=panakoes-admin aws budgets delete-budget \
  --account-id 659225405128 \
  --budget-name "My Monthly Cost Budget"
```

Decision rationale: importing the console budget into Terraform was rejected because its notifications are wired to a different email convention and its name is non-canonical for the project. Clean replacement is simpler than import-then-rename.

## Cost

- **AWS Budgets itself:** First 2 budgets per AWS account are free; budgets 3+ are $0.02 per budget per day = ~$0.60/mo each. This module provisions 6 budgets (1 account + 4 service + 1 tag), so the steady-state cost is ~$2.40/mo (4 budgets above the free tier x $0.60).
- **SNS topic + email subscription:** $0/mo at idle; SNS email is $2.00 per 100,000 notifications. A dev env that fires single-digit alerts per month is functionally free.
- **CloudWatch alarm:** $0.10/mo per standard-resolution alarm. One alarm here = $0.10/mo.

Total module cost: roughly $2.50/mo. For interview talk-track, this is the canonical "cost-of-cost-control" tradeoff (Budgets is one of the few AWS services that bills for itself).

## How to extend

- **Slack / PagerDuty fan-out:** add a second `aws_sns_topic_subscription` against `aws_sns_topic.budget_alerts` with protocol `https` and the webhook endpoint, or wire AWS ChatBot to the topic.
- **Per-tenant budgets:** clone `project_tag_monthly` with `cost_filter { name = "TagKeyValue"; values = ["user:Tenant$<tenant-id>"] }`. Requires activating the `Tenant` tag as a cost-allocation tag (same process as `Project`).
- **Per-environment split inside the same account:** add `cost_filter { name = "TagKeyValue"; values = ["user:Environment$<env>"] }` to the project-tag budget, or clone it per environment.
- **Quarterly / annual budgets:** clone any of the above with `time_unit = "QUARTERLY"` or `"ANNUALLY"`.
- **Per-threshold CloudWatch alarms:** replace the coarse `NumberOfMessagesPublished` alarm with a Lambda subscriber on the SNS topic that parses the budget notification JSON (budget name, threshold, notification type) and emits a custom metric per budget. Alarms then attach per-budget instead of a single shared one.

## Interview-defensible reasoning

**Why Budgets AND Cost Anomaly Detection?** They cover different failure modes. Budgets are deterministic ceilings tied to specific spend (per-service, per-tag); Cost Anomaly Detection is statistical and catches relative spikes inside a normal absolute spend range (e.g. ECS spend triples but stays under $20). The Panakoes setup pairs both: the cost-anomaly-monitor module handles "weird relative" and this module handles "absolutely too much".

**Why an SNS topic in front of email?** Decouples the notification channel from the budget resource. Adding Slack later means adding a subscriber, not modifying 6 budget resources. Same pattern as standard AWS observability fan-out (SNS = alert bus; subscribers = channels).

**Why scope the topic policy with `aws:SourceAccount` + `aws:SourceArn`?** Defends against the AWS service-confused-deputy pattern. Without those conditions, any AWS account's Budgets service could publish to our topic. The two conditions together require the publisher to be our own account's Budgets service, on a Budgets ARN we own. Standard AWS security guidance for service-principal-trusted topics and roles.
