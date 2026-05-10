# infra/dev/cost-anomaly-monitor

Provisions AWS Cost Anomaly Detection for the Panakoes `dev` environment so the cost-api `GET /api/v1/cost/anomalies` endpoint returns real anomalies instead of `[]`.

## What this provisions

- `aws_ce_anomaly_subscription.email` (`panakoes-dev-service-anomaly-subscription`): IMMEDIATE-frequency subscription that emails the address in `var.alert_email` whenever a monitored anomaly's total impact is `>= $5 USD`.

We attach our notification subscription to AWS's default `Default-Services-Monitor` (DIMENSIONAL on `SERVICE`, auto-provisioned by AWS on every new account) instead of creating a parallel one. The default account quota for DIMENSIONAL monitors is 1, so provisioning our own collides with AWS's default and fails apply with `ValidationException: Limit exceeded on dimensional spend monitor creation`. Refactor 2026-05-09 per memory `aws_default_anomaly_monitor_collision.md`. The default monitor's ARN is hardcoded as a `local` in `main.tf` because the AWS provider (`~> 6.0`) does not ship an `aws_ce_anomaly_monitors` data source for portable lookup; if Panakoes ever runs in a second AWS account, swap the local for that account's default monitor ARN.

## Apply

```bash
cd infra/dev/cost-anomaly-monitor
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

Or via the repo helper:

```bash
scripts/tf.sh plan cost-anomaly-monitor
scripts/tf.sh apply cost-anomaly-monitor
```

This module is standalone; it is intentionally NOT wired into any orchestrating module list.

## Post-apply manual step (required)

After the first apply, AWS sends a confirmation email to the subscriber address (default `phil@lafayettelabs.com`). The subject line looks like "AWS Notification - Subscription Confirmation". Open that email and click the confirmation link.

Until the link is clicked the subscription is in a pending state and AWS will NOT deliver anomaly alerts. cost-api's endpoint will still return real anomalies once CE accumulates enough baseline data (typically 10+ days of usage) because the `GetAnomalies` API does not depend on the email-side confirmation; the confirmation only gates email delivery.

## Cost

- **Cost Anomaly Detection itself: $0/month.** AWS does not bill for monitors, subscriptions, or `GetAnomalies` calls.
- **Email delivery: ~pennies/month at zero traffic.** Cost Anomaly Subscriptions deliver via SNS under the hood; SNS email is $2.00 per 100,000 notifications. A dev env that receives a handful of alerts per month is functionally free.

## How to extend

- **Narrower targeting:** add a second `aws_ce_anomaly_monitor` with `monitor_type = "CUSTOM"` and a `monitor_specification` JSON document that filters to a single `LINKED_ACCOUNT`, a single `SERVICE`, or a tag-based `COST_ALLOCATION_TAG_KEY` (e.g., one monitor per tenant). Subscribe it via a second `aws_ce_anomaly_subscription` (or add its ARN to the existing subscription's `monitor_arn_list`).
- **Slack/webhook delivery:** swap the `subscriber` block from `EMAIL` to `SNS`, point at an SNS topic, and bridge the topic to ChatBot / Lambda / EventBridge. Useful once the operator response surface moves out of Phil's personal inbox.
- **Tighter threshold for prod:** raise the `threshold_expression` value (e.g., `100` USD) and switch `frequency` to `DAILY` to batch alerts; production spend is higher so a $5 floor is too sensitive.
