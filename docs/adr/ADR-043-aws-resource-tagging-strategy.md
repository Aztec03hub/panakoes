# ADR-043: AWS resource tagging strategy for cost allocation

## Status

Accepted (2026-05-11).

## Context

The `panakoes-dev-tenant-cost-rollup` DynamoDB table is empty in dev even though
the nightly `cost-rollup-aggregator` Lambda is running successfully. The Lambda
calls Cost Explorer with `GroupBy = [{TAG: tenant_id}, {DIMENSION: SERVICE}]`
(ADR-040). Cost Explorer returns one untagged group containing every dollar of
spend because no Panakoes AWS resource currently carries a `tenant_id` tag, and
no resource carries a `Service` tag either. The admin SPA's `/cost/by-tenant`
page renders empty as a result.

Multi-tenant isolation is not on the v0.1 roadmap (each user is one tenant for
now), so a `tenant_id` tag on long-lived infra resources would be meaningless.
The per-service breakdown is the immediate value: operators need to see which
Panakoes microservice is burning which dollars before they need per-tenant
attribution.

Phil already activated the `Project` tag at the Billing console
(Billing > Cost allocation tags). Activation is a one-click-per-tag step that
must happen at the billing layer before Cost Explorer treats a tag as a
group-by dimension. Without activation, the tag exists on the resource but
Cost Explorer ignores it.

The 21 Terraform modules in `infra/dev/` already declare a uniform
`default_tags` block (`Project`, `Environment`, `ManagedBy`, `Module`) at the
provider level. AWS merges `default_tags` with per-resource `tags` blocks at
apply time, so adding a tag at the provider level propagates to every resource
the module creates without per-resource edits.

## Decision

### Four mandatory tags on every Panakoes AWS resource

| Tag | Source | Example values |
|---|---|---|
| `Project` | Constant `panakoes` | `panakoes` |
| `Environment` | `var.environment` | `dev`, `prod` |
| `Service` | The consuming microservice or `platform` for shared infra | `auth`, `cost-api`, `admin-api`, `transcription`, `frontend`, `api-gateway`, `platform` |
| `Component` | Coarse category | `network`, `compute`, `data`, `storage`, `observability`, `security` |

`Service` is the primary cost-allocation dimension. The cost-rollup-aggregator
already groups by `SERVICE` (the Cost Explorer built-in DIMENSION, which maps
to AWS service names like `Amazon EC2`). The new `Service` tag adds a Panakoes
microservice dimension on top of the AWS service dimension, so an operator
seeing $40 of `Amazon EC2` spend can see whether it is `transcription`'s GPU
batch fleet or `platform`'s NAT Gateway.

`Component` is the coarse category dimension. Useful for the question "how
much of dev is data plane vs control plane?" without forcing the operator to
sum per-service totals.

`ManagedBy` and `Module` were already present and stay. `ManagedBy` distinguishes
Terraform-managed resources from any future hand-rolled ones; `Module`
distinguishes which Terraform module owns a resource for blast-radius queries.

### Optional `TenantId` tag for per-tenant workloads

For resources created at runtime by a tenant-scoped workload (streaming session
EC2 instance, transcription job's S3 output prefix, per-tenant CloudWatch log
stream), the workload's own code sets `TenantId = <tenant_id>` when calling the
AWS API. This is not a Terraform tag because Terraform-managed resources are
shared infrastructure; they are not per-tenant. The session-spawner service
(when it ships) is the primary `TenantId` tag emitter.

### Implementation: provider-level default_tags

Each module's `providers.tf` adds `Service` and `Component` to its existing
`default_tags` block:

```hcl
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "<module-name>"
      Service     = "<service-or-platform>"
      Component   = "<coarse-category>"
    }
  }
}
```

The 21 modules under `infra/dev/` (everything except `network`, which is on a
separate broken-upstream-pin remediation track) get this treatment in one PR.
AWS provider merges `default_tags` + per-resource `tags` so the per-resource
tag blocks (`tags = local.common_tags`, `tags = merge(local.common_tags, ...)`)
inherit the new dimensions automatically. Adding a tag to an existing resource
is an in-place AWS API update; `terraform plan` shows only tag additions with
no resource adds or destroys.

### Activation at the Billing console (operator step)

Cost Explorer treats a tag as a group-by dimension only after the tag is
activated in the Billing console. Phil already did this for `Project`; the new
tags require the same click flow for `Service`, `Environment`, `Component`,
and `TenantId`. The procedure is captured in
`docs/runbooks/cost-allocation-tag-activation.md`.

Activation takes ~24 hours to backfill across the Cost Explorer data set, so
the cost-rollup-aggregator's next nightly run after the apply + activation
landing will be the first run that emits non-`__untagged__` rows.

### Cost-rollup-aggregator alignment

The aggregator already calls `ce.get_cost_and_usage` with
`GroupBy = [{TAG: tenant_id}, {DIMENSION: SERVICE}]` (ADR-040). The `Service`
tag introduced here is the Panakoes microservice dimension, distinct from the
Cost Explorer `SERVICE` dimension (which is the AWS service name). No Lambda
code change is needed for the by-service view: the existing aggregator already
splits by AWS service, and the admin SPA renders that breakdown.

The by-tenant view will remain empty until tenant-scoped workloads start
emitting `TenantId` tags at runtime. That is a future PR scoped to the
session-spawner service.

## Consequences

**Positive.**

- Cost Explorer immediately gains a `Service` group-by once tags propagate and
  the activation lands. The admin SPA's by-service page becomes non-empty.
- The `Component` dimension lets operators answer coarse-grained questions
  without summing across the per-service breakdown.
- Provider-level `default_tags` is the canonical Terraform pattern for
  cross-cutting tags. Resource authors do not have to remember to set them;
  the provider merges them at apply time.
- The strategy is forward-compatible with multi-tenancy. When per-tenant
  workloads start spinning up runtime resources, those resources will already
  inherit `Project`, `Environment`, `Service`, `Component` from the spawning
  service's IAM role boundary, and the workload code adds `TenantId` on top.
- All 21 modules touched in one PR keeps the diff reviewable and avoids the
  rolling-tags-across-22-PRs anti-pattern.

**Negative.**

- The `Service` tag value is hand-assigned per module. A module that
  legitimately serves multiple Panakoes services (e.g., `iam`, which mints
  roles for every service) gets `Service = platform`, which slightly
  understates the cost attributable to individual services. Acceptable because
  shared-infra modules tend to cost pennies (an IAM role is free; the costed
  resources are in the consumer module).
- Billing-side activation is a manual operator step. The runbook captures the
  click flow, but there is no Terraform resource for tag activation
  (`aws_ce_cost_allocation_tag` exists in AWS provider 6.x, but is documented
  for `cost_allocation_tags` resources; the runbook prefers the console for
  clarity in the first pass).
- The activation backfill window is ~24 hours. The aggregator's nightly run
  after activation lands is the first one with non-`__untagged__` rows.
- `Component` is a coarse category with some judgment calls. `auth-db` is
  `data` (it stores user state) but could be argued as `compute` (it runs an
  Aurora instance). The rule of thumb: the dimension primarily costed is what
  determines the component. Aurora Serverless v2 cost is dominated by ACU and
  storage, so `data` is correct.

## Alternatives considered

**Per-resource `tags` blocks for `Service` and `Component`.** Rejected. With
~200 resource blocks across the 21 modules, the diff would be 200+ touch
points; review fatigue. `default_tags` covers every resource the module
declares with one block.

**A single `cost-allocation` module that owns all tags.** Rejected. Tags live
on the resources they describe; centralizing them creates a module that has
to be coupled to every other module's resource graph. Not idiomatic Terraform.

**Skip the `Component` dimension and rely on `Service` alone.** Considered.
`Component` is a small addition that buys the "data plane vs control plane"
coarse cut for free. Keep it.

**Wait for multi-tenancy to ship before adding any cost tags.** Rejected. The
by-service view is valuable today (the admin SPA already renders the page),
and per-service attribution is the question Phil is asked first in any cost
review. Tenant attribution can layer on top later.

## References

- `infra/dev/*/providers.tf` (the 21 modules with the new tags)
- `docs/runbooks/cost-allocation-tag-activation.md` (the Billing console
  click flow)
- `services/cost-rollup-aggregator/src/panakoes_cost_rollup_aggregator/aggregator.py`
  (the CE call site; no code change needed for the by-service view)
- ADR-031 (cost-api read-through cache)
- ADR-040 (tenant cost rollup service dimension; the by-tenant route that
  consumes the rolled-up data)
- AWS docs: [User-Defined Cost Allocation Tags](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/custom-tags.html)
- AWS docs: [AWS provider `default_tags`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs#default_tags)
