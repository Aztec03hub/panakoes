# ADR-044: Container Insights cost vs observability tradeoff in dev

## Status

Accepted (2026-05-18). Implemented in PR #363.

## Context

PR #344 enabled ECS Container Insights on the dev ECS cluster to power the
real-metrics view of the health-aggregator dashboard. Container Insights
publishes a fixed set of paid CloudWatch metrics per task and per service, and
the bill is per-metric per-month regardless of whether anything queries them.

The post-Wave-1 cost audit on 2026-05-18 attributed approximately $44 per month
of gross AWS charges to 233 paid Container Insights metrics on a cluster running
roughly a dozen long-lived services. That single line item was around 30 percent
of the dev tier's monthly bill, by far the largest single contributor on a
month with no real user traffic. Dev usage is Phil-only, intermittent, and
already covered by the same ECS DescribeServices / DescribeTasks API the
health-aggregator uses for its basic CPU, memory, desired-count, and
running-count rollups, so the per-service detail page does not actually need the
233 fine-grained metrics until production traffic gives them signal.

Constraints to satisfy:

- Cost discipline: dev sits idle most of the time; $44 per month for a feature
  with no active operator viewing the metrics is not justifiable.
- Observability: the health-aggregator dashboard must keep working for the
  basic per-service view (running counts, desired counts, recent task failures).
  Disabling Container Insights cannot break the dashboard.
- Reversibility: production will eventually re-enable Container Insights once
  steady-state metric volume and observability needs are known; the toggle must
  be a single Terraform variable, not a structural change.
- Per-service filtering: AWS does not support per-service Container Insights
  gating; the only switches are cluster-level on or off.

## Decision

Disable Container Insights at the dev ECS cluster level via the Terraform
`container_insights` setting on `aws_ecs_cluster`. Production stacks override
the variable to re-enable the feature once production traffic shape and
observability requirements are known. The health-aggregator continues to
consume ECS DescribeServices and DescribeTasks for the basic per-service view;
the 233-metric detail view is dark in dev and lights up again when production
re-enables.

## Consequences

**Positive.**

- Gross dev bill drops by roughly $44 per month, around 30 percent of the
  post-Wave-1 dev total. The cost line that dominated the audit is gone.
- The toggle is a single Terraform variable in the ECS cluster module, so
  production can re-enable without code churn.
- The health-aggregator dashboard's basic view is unaffected: ECS
  DescribeServices and DescribeTasks still return running counts, desired
  counts, and last-task failure reasons. Operators see the data they need at
  the dev tier.

**Negative.**

- The per-service detail page in the admin SPA loses the 233-metric Container
  Insights view in dev. Operators investigating a dev anomaly fall back to
  service-level CloudWatch metrics, task-level logs, or X-Ray traces, all of
  which remain available.
- A regression in production observability that depends on Container Insights
  metrics will not be caught in dev because dev no longer publishes those
  metrics. Mitigation: production keeps the feature on, and any dashboards or
  alarms that require the 233-metric set are explicitly marked
  "production-only" in the runbook.
- Re-enabling later costs the same $44 per month per cluster; the decision to
  re-enable in production should land alongside a documented observability
  requirement that justifies it.

## Alternatives considered

**Keep Container Insights enabled in dev.** Rejected: $44 per month for
metrics no one queries is unjustified at dev traffic volumes. The
cost-discipline rule on the dev tier is that every line item must earn its
keep; this one did not.

**Use a third-party APM in place of Container Insights.** Rejected: adds a new
vendor surface (Datadog, New Relic, Honeycomb), a new API token to manage, and
a new SaaS bill, all for a feature that production may want from CloudWatch
anyway. The v0.1 posture is AWS-native first.

**Narrow Container Insights to only critical services via per-service
filters.** Rejected: AWS does not support per-service Container Insights
gating. The only knobs are cluster-level on or off. We could move
non-critical services to a separate ECS cluster with insights off, but the
operational overhead of running two clusters dwarfs the $44 per month savings.

**Drop the real-metrics view from the admin SPA entirely.** Rejected: the
basic view is cheap (free ECS API calls) and useful. Removing it would lose
real value to chase a savings that disabling-only-the-paid-tier already
delivers.

## References

- PR #363 (the disable Container Insights change, shipped 2026-05-18).
- PR #344 (the original enable that this ADR partially walks back).
- `infra/dev/ecs/cluster.tf` (the `container_insights` Terraform setting).
- `services/health-aggregator/` (the consumer that uses ECS API, not Container
  Insights metrics, for the basic view).
- AWS docs: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Container-Insights.html
