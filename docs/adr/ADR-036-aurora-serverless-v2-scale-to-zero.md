# ADR-036: Aurora Serverless v2 scale-to-zero for the dev tier

## Status

Accepted. Implemented in PR #183 (shipped 2026-05-10).

## Context

Better-Auth requires a SQL backend; Panakoes uses PostgreSQL via Drizzle. The dev environment runs cost-conscious (target: ~$0/mo while idle, since dev sits unused for the majority of any given week). Aurora Serverless v2 was the natural pick for a managed, AWS-native Postgres that can scale up under real load while staying cheap when nothing is happening, except for one historic limitation: until November 2024, `min_capacity` had a hard floor of 0.5 ACU, which meant the cluster was always-on and billed at roughly $43/month even with zero connections.

In November 2024 AWS shipped true scale-to-zero for Aurora Serverless v2 (see [AWS announcement](https://aws.amazon.com/about-aws/whats-new/2024/11/amazon-aurora-serverless-v2-scaling-zero-capacity/) and [Aurora User Guide: auto-pause](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html)). With `min_capacity = 0` and `seconds_until_auto_pause` configured, the cluster pauses to 0 ACU after the configured inactivity window and pays $0/hour while paused. The cold-start back to a usable connection takes ~15 seconds per AWS, after which the cluster scales normally.

Constraints to satisfy:

- Engine version: scale-to-zero requires PostgreSQL 16.3+ (we pin 16.4).
- `seconds_until_auto_pause` valid range: 300 to 86400 seconds.
- Cold-start latency on the first connection after a pause is non-zero; the auth path holding a user is not the right place to absorb a 15-second wait, but the dev-only auth path is acceptable.
- Per-environment override: production cannot accept the cold-start, so the variable must allow a non-zero floor in prod stacks.

## Decision

`infra/dev/data` (and any future dev-tier Aurora cluster) provisions Aurora Serverless v2 with:

```hcl
serverlessv2_scaling_configuration {
  min_capacity             = 0
  max_capacity             = 1.0
  seconds_until_auto_pause = 300  # 5 minutes idle -> pause
}
```

Production stacks override `var.min_capacity_acu = 0.5` (or higher when sustained-load profiles demand it) and can set `seconds_until_auto_pause = null` to disable auto-pause entirely. The variable is plumbed at the module surface so dev and prod stacks share the same module body.

Engine version is pinned to PostgreSQL 16.4 (the lowest 16.x line that satisfies the 16.3+ floor without chasing point releases on every minor bump).

## Consequences

**Positive.**

- Dev cluster bill drops from ~$43/month to effectively $0/month while idle. Storage charges and backup snapshots remain (small, single-digit dollars).
- The same module powers dev and prod with a single variable flip; no parallel module fork.
- AWS-native posture preserved. The auth-token path stays inside our AWS account, with no third-party SaaS sitting between Better-Auth and the database. That keeps compliance review (SOC 2 path, future BAA scenarios) simpler than a Neon-style hosted-Postgres alternative would.
- Aurora's automatic scaling story is unchanged for production: scale-up on real load behaves identically to the always-on configuration.

**Negative.**

- ~15-second cold-start latency on the first connection after a pause. Acceptable for dev (one Phil session per few days, occasional smoke test). Not acceptable for production with real users in the auth path; that is exactly why the override variable exists.
- The auto-pause window introduces a new operational surprise: a developer running migrations after a 10-minute coffee break sees a slow first connection. Documented in `infra/dev/data/README.md`; not a recurring confusion in practice.
- A long-tail risk that AWS revises the cold-start performance envelope; the 15-second figure is AWS's published number, not a hard SLA. If real-world cold-starts grow, prod's non-zero floor remains the safety valve.

## Alternatives considered

**Aurora Serverless v2 with `min_capacity = 0.5` (legacy default).** Rejected: $43/mo idle bill on a cluster that sits unused most days. The scale-to-zero feature exists precisely to retire this trade-off; not adopting it would be choosing the strictly worse option.

**RDS `db.t4g.micro`, always on.** Rejected: ~$12-15/mo idle bill, no cold start. Cheaper than legacy Aurora Serverless v2, more expensive than scale-to-zero. Single-instance posture loses the multi-AZ + storage-auto-scaling story that Aurora provides for free; we would have to migrate to Aurora later anyway when production hardens. Solving the bill problem twice (now with t4g.micro, then again with the migration) is more work than solving it once.

**Neon serverless Postgres.** Rejected for v0.1: third-party SaaS in the auth-token path. Neon's cold-start is faster (~3-5 seconds) and the free tier is generous, but the AWS-account-isolation posture is lost. Better-Auth's compliance story is easier to defend when the database is in our own AWS account behind our own KMS keys. Reconsider only if Aurora's scale-to-zero behaviour proves operationally unacceptable.

**DynamoDB.** Rejected: no native Better-Auth adapter. Building one is in-scope long-term but not for v0.1, and the relational schema Better-Auth expects (users, sessions, accounts, verification tokens with foreign-key joins) is not a natural fit for DynamoDB's access patterns.

**Turso (libSQL/SQLite at edge).** Rejected: single-writer constraint at the libSQL layer is incompatible with the multi-writer scenarios Better-Auth's session table will see at scale. Edge replication is interesting; the writer constraint is the deal-breaker.

**CockroachDB serverless.** Rejected: Postgres-wire compatible, scale-to-zero exists, but the future of CockroachDB serverless is uncertain post-Cisco acquisition. Not a substrate to bet a foundational dependency on.

## References

- PR #183, the scale-to-zero fix shipped 2026-05-10.
- AWS announcement: https://aws.amazon.com/about-aws/whats-new/2024/11/amazon-aurora-serverless-v2-scaling-zero-capacity/
- AWS docs: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html
- AWS Database Blog: https://aws.amazon.com/blogs/database/introducing-scaling-to-0-capacity-with-amazon-aurora-serverless-v2/
- `infra/dev/data/main.tf` (the canonical scale-to-zero configuration).
- Engine version requirement: PostgreSQL 16.3 minimum; we pin 16.4.
- `seconds_until_auto_pause` valid range: 300 to 86400 seconds.
