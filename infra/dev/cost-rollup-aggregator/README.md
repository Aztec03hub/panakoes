# infra/dev/cost-rollup-aggregator

Provisions the nightly Lambda + EventBridge Scheduler rule that populates `panakoes-dev-tenant-cost-rollup` from AWS Cost Explorer per-tenant data. Without this module the cost-api `GET /api/v1/cost/by-tenant` route returns empty rows even when CE has spend data.

## What this provisions

- `aws_lambda_function.aggregator` (`panakoes-dev-cost-rollup-aggregator`): container-image Lambda, 256 MB, 5-minute timeout, reserved concurrency 1.
- `aws_iam_role.aggregator` + inline policy: least-privilege execution role granting only `ce:GetCostAndUsage`, `ce:GetDimensionValues` (CE has no resource-level authorization, so resources MUST be `*`), `dynamodb:PutItem` on the rollup table ARN only, and CloudWatch Logs write to the function's own log group.
- `aws_scheduler_schedule.nightly` (`panakoes-dev-cost-rollup-nightly`): EventBridge Scheduler rule firing daily at 02:00 UTC. Uses the modern `aws_scheduler_schedule` resource (not the legacy `aws_cloudwatch_event_rule` cron) per AWS guidance for new workloads.
- `aws_iam_role.scheduler` + invoke policy: separate role the Scheduler assumes to invoke the Lambda; includes the `aws:SourceAccount` confused-deputy guard.
- `aws_cloudwatch_log_group.aggregator` (`/aws/lambda/panakoes-dev-cost-rollup-aggregator`): KMS-encrypted (with the `infra/dev/observability/` logs CMK) and 30-day retention to match the locked decision in CLAUDE.md.

## Apply

This module reads four remote states (`admin-state`, `ecr`, `observability`, plus its own backend). All three upstream modules must already be applied.

```bash
cd infra/dev/cost-rollup-aggregator
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

The first apply is **blocked** until the operator has built and pushed a container image to the ECR repository (see "First-apply bootstrap" below). Lambda validates the `image_uri` at create time; with no tagged image, the function create call fails.

## First-apply bootstrap

The Lambda's `image_uri` resolves to `<ecr-repo-url>:latest`, and Lambda validates that the image exists at function-create time. So the apply order is:

1. `cd infra/dev/ecr && terraform plan && terraform apply` (provisions the new `panakoes-dev-cost-rollup-aggregator` repository).
2. From the repo root, build and push the container image:

   ```bash
   AWS_REGION=us-east-1
   ACCOUNT_ID=659225405128
   REPO=panakoes-dev-cost-rollup-aggregator
   ECR_URL=${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

   aws ecr get-login-password --region ${AWS_REGION} \
     | docker login --username AWS --password-stdin ${ECR_URL}

   docker build \
     -f services/cost-rollup-aggregator/Dockerfile \
     -t ${REPO}:latest .

   docker tag ${REPO}:latest ${ECR_URL}/${REPO}:latest
   docker push ${ECR_URL}/${REPO}:latest
   ```

3. `cd infra/dev/cost-rollup-aggregator && terraform plan && terraform apply` (now the Lambda creates cleanly).

## Subsequent deploys

The Lambda's `image_uri` is lifecycle-ignored on `:latest`, so a fresh `docker push` deploys without a re-apply. The next nightly Scheduler tick picks up the new image. For an immediate roll-out:

```bash
aws lambda update-function-code \
  --function-name panakoes-dev-cost-rollup-aggregator \
  --image-uri <ecr-url>/panakoes-dev-cost-rollup-aggregator:latest
```

## Manual replay (specific day)

The handler honors `event["day"]` as a YYYY-MM-DD override:

```bash
aws lambda invoke \
  --function-name panakoes-dev-cost-rollup-aggregator \
  --payload '{"day":"2026-05-08"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/aggregator-out.json
cat /tmp/aggregator-out.json
```

Re-running the same day overwrites the existing rollup row; `put_item` is an upsert.

## Cost

- **Lambda invocations:** ~30/month at 256 MB and ~3 seconds per run is well under the always-free tier (1M requests + 400K GB-seconds/month).
- **Cost Explorer API:** `$0.01 per request`. 30 nightly calls is `$0.30/month`.
- **CloudWatch Logs:** sub-cent/month at this volume.
- **EventBridge Scheduler:** free for the first 14M invocations/month.
- **DynamoDB:** PAY_PER_REQUEST writes against `panakoes-dev-tenant-cost-rollup` are pennies/month at the expected row count.

Total: under `$1/month` at dev scale.

## Operator follow-up

Per-tenant tagging on the actual AWS resources (`Project`, `Environment`, `tenant_id` on every billable resource) is a separate piece of work and is NOT created by this module. Until that lands, every dollar of dev-environment spend lands in the `__untagged__` bucket the aggregator emits. The aggregator still runs and the rollup table still populates; the by-tenant breakdown shows one row labeled `__untagged__` with the full daily cost. Once the tagging policy is in place the same aggregator decomposes spend by tenant without any code change.
