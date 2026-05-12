# Cost allocation tag activation

## Purpose

AWS Cost Explorer treats a user-defined tag as a group-by dimension only after
the tag has been activated in the Billing console (Billing > Cost allocation
tags > User-defined cost allocation tags). The Terraform apply that lands
ADR-043's tags sets the tag values on every resource, but the tags remain
invisible to Cost Explorer until this one-time per-tag click happens at the
billing layer. This runbook captures the click flow.

## When to use this runbook

- A new user-defined tag was just added at the Terraform layer (e.g., the
  initial ADR-043 rollout adds `Service`, `Environment`, `Component`,
  `TenantId`).
- The `panakoes-dev-tenant-cost-rollup` DynamoDB table is empty (or full of
  `__untagged__` rows) and a recent Terraform apply just propagated the
  expected tags.
- A future ADR adds a new cost-allocation dimension to the tag set.

This runbook does NOT cover AWS-generated tags (e.g., `aws:createdBy`); those
follow a separate activation flow.

## Prerequisites

- Phil's IAM user has `aws-portal:ViewBilling` and `aws-portal:ModifyBilling`
  on the management account (the account that owns the consolidated billing
  view). For Panakoes today, the management account and the workload account
  are the same: `659225405128`.
- The Terraform apply that propagates the tags has already landed. Verify
  with the AWS Tag Editor:

  ```bash
  aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=Service,Values=auth \
    --query 'ResourceTagMappingList[].ResourceARN' \
    --output text | head -5
  ```

  If the output is empty, the tags have not propagated yet. Either the apply
  has not run or the resources do not match. Re-run `terraform apply` against
  the affected modules and try again.

## Procedure

### Step 1: Open the Billing console's tag-activation page

Phil signs into the AWS console as the IAM admin user `phil` and navigates to:

```
Billing > Cost allocation tags
```

Direct URL:
[https://us-east-1.console.aws.amazon.com/billing/home#/preferences/tags](https://us-east-1.console.aws.amazon.com/billing/home#/preferences/tags)

The page has two tabs: **AWS-generated cost allocation tags** and
**User-defined cost allocation tags**. Click **User-defined cost allocation
tags**.

### Step 2: Locate the new tags

The user-defined tags table lists every tag key that has been observed by AWS
across any tagged resource in the account, with a status column showing
**Active** or **Inactive**. The initial ADR-043 apply will surface four new
inactive entries:

- `Service`
- `Environment`
- `Component`
- `TenantId` (will appear after the first runtime-tagged resource gets
  created; not present from the ADR-043 apply alone, since no Terraform
  resource sets `TenantId`. Skip in this initial activation and revisit when
  the session-spawner service first runs)

`Project` is already **Active** from a prior activation; leave it alone.
`ManagedBy` and `Module` are out of scope for activation (they are operator
metadata, not cost-allocation dimensions); leave them inactive.

### Step 3: Activate each new tag

For each of `Service`, `Environment`, and `Component`:

1. Check the row's checkbox.
2. Click **Activate** at the top of the table.
3. Confirm in the dialog.

The status flips from **Inactive** to **Active** immediately. The backfill
across the Cost Explorer data set takes up to 24 hours.

Skip `TenantId` until the first runtime-tagged resource gets created. When
the session-spawner service ships its first per-tenant workload, return to
this runbook and activate `TenantId` then.

### Step 4: Verify activation

Re-load the tag-activation page and confirm `Service`, `Environment`, and
`Component` show **Active**.

Then run a Cost Explorer query against the new dimension to confirm it groups
correctly:

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -d 'yesterday' +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity DAILY \
  --metrics UnblendedCost \
  --group-by Type=TAG,Key=Service
```

If the query returns groups keyed `Service$auth`, `Service$cost-api`,
`Service$transcription`, etc., activation worked. If every row comes back as
`Service$` (empty value) or the query errors with `Tag key 'Service' is not
activated`, return to Step 3.

Note: the very first query after activation may still show `Service$`
(untagged) because Cost Explorer's data warehouse has not yet backfilled.
Re-run after 24 hours.

### Step 5: Confirm the cost-rollup-aggregator picks up the new dimension

The nightly Lambda runs at 09:00 UTC by default (verify in
`infra/dev/cost-rollup-aggregator/main.tf`). The first run after activation
backfills should write non-`__untagged__` rows to
`panakoes-dev-tenant-cost-rollup`.

Spot-check:

```bash
aws dynamodb scan \
  --table-name panakoes-dev-tenant-cost-rollup \
  --max-items 5 \
  --query 'Items[].{tenant:tenant_id.S,day_service:day_service.S,cents:cost_cents.N}'
```

Rows with `tenant.S` of `__untagged__` are expected during the v0.1 single-
tenant phase (no resource carries `TenantId` yet). The `day_service` column
should show real AWS service names like `2026-05-11#Amazon EC2`. That
confirms the SERVICE dimension half of the GroupBy is landing rows; the
tenant_id half stays `__untagged__` until per-tenant runtime tagging ships.

## Verification

- `Service`, `Environment`, `Component` show **Active** on the Cost
  allocation tags page.
- `aws ce get-cost-and-usage --group-by Type=TAG,Key=Service` returns at
  least one non-empty `Service$<value>` group (allow 24 hours after Step 3).
- The next nightly cost-rollup-aggregator run writes at least one row to
  `panakoes-dev-tenant-cost-rollup` with a real AWS service in the
  `day_service` composite (not just `__untagged__`).

## Rollback

Cost allocation tag activation is reversible:

1. Return to the Cost allocation tags page.
2. Check the row.
3. Click **Deactivate**.

Deactivating does NOT delete the historical groupings; existing Cost Explorer
queries that grouped by the tag continue to work for the period the tag was
active. Future queries will not group by the deactivated tag.

There is no rollback for the Terraform-side tag propagation; removing the
tags from `default_tags` blocks and re-applying would emit an in-place tag
removal on every resource, which is harmless but high-volume. Prefer
deactivating at the Billing console over removing the tags from Terraform.

## References

- ADR-043: AWS resource tagging strategy for cost allocation
- ADR-040: Service dimension for the tenant cost rollup table (the consumer
  schema that depends on these tags)
- `infra/dev/cost-rollup-aggregator/main.tf` (the nightly Lambda + schedule)
- `services/cost-rollup-aggregator/src/panakoes_cost_rollup_aggregator/aggregator.py`
  (the Cost Explorer call site)
- AWS docs: [Activating user-defined cost allocation tags](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/activating-tags.html)
- AWS docs: [Cost Explorer GroupBy semantics](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html)
