# Cost Analysis 2026-05-19: where the $90/mo gross is going + cuts without credits

**Date:** 2026-05-19 (data: last 30 days, 2026-04-19 to 2026-05-19, region us-east-1, AWS account 659225405128)
**Net cost:** $0 (Activate Founders credits absorb the full gross)
**Gross cost (Usage record-type only, last 30 days):** ~$87.60

> Author note: this is a v1 skeleton built from a live AWS Cost Explorer pull on 2026-05-19. Numbers are accurate to the last 30 days; recommendations are concrete but pending an apply-side decision for each. The "cuts" section is ordered by potential savings, not implementation cost.

## TL;DR

| Service | Last 30d gross | % of total | Lever |
|---|---|---|---|
| Amazon VPC | $39.65 | 45% | Audit + prune Interface Endpoints; single-AZ for dev |
| Elastic Load Balancing | $10.87 + $8.82 LCU = ~$19.69 | 22% | Listener consolidation; LCU optimization |
| ECS / Fargate | $9.52 | 11% | Already on Fargate Spot (PR #369); idle-scale-to-zero candidates |
| CloudWatch | $8.00 | 9% | Already on 7d retention (PR #369); metric publish audit |
| EC2 - Other | $6.23 | 7% | Investigate (NAT? PrivateLink? cross-AZ data?) |
| KMS | $5.78 | 7% | Wave 2 KMS T2-T7 consolidation pending (~-$5/mo) |
| RDS | $3.14 | 4% | Auth-db; serverless v2 scale-to-zero (small absolute benefit) |
| WAF | $2.28 | 3% | Web ACL; baseline floor for security |
| Misc (Secrets Manager, R53, CE, S3, ECR, etc.) | ~$2.40 | 3% | Mostly fixed floor |

**Projected cuts without credits, if all top-3 levers shipped:** ~$32-40/mo (gross from ~$88 down to ~$48-56/mo).

## Section 1: VPC ($39.65/mo, 45% of gross) - DRILLED 2026-05-19

### What it is (verified live)
| Sub-component | Amount | Live state |
|---|---|---|
| VPC Interface Endpoint hours | $35.94 | **0 endpoints live**; deleted by Phil 2026-05-14 per CloudTrail. Cost is full-month proration; rolls off to $0 next cycle. |
| Public IPv4 InUse | $3.71 | Prorated; **true steady-state is ~$25/mo** with 7 ECS task ENIs each carrying a public IP at $0.005/hr * 720h. |
| Public IPv4 Idle | <$0.01 | Negligible. |

### Drill findings (2026-05-19)
- `aws ec2 describe-vpc-endpoints` returns `{"VpcEndpoints": []}`. Live state has ZERO endpoints. The $35.94 is leftover billing from the ~25 days endpoints existed earlier this window (CloudTrail confirms Phil deleted them 2026-05-14T20:09Z).
- `aws ec2 describe-network-interfaces --filter Name=association.public-ip,Values='*'` returns 7 ENIs, each attached to an ECS Fargate task in a public subnet. This is the architectural tradeoff from PR #346 (ECS public subnets + NAT removal): each task's ENI gets a public IP at $0.005/hr.
- The 7-IP steady-state cost is ~$25/mo. Less than a NAT Gateway ($32/mo + data) so the design is the local optimum.

### Why the projection was wrong
The original cost-analysis assumed the $39.65 was steady-state and that pruning VPC endpoints would save -$15-25/mo. Reality: the endpoints were already gone (Phil's 2026-05-14 sweep); the $35.94 was billing window leftover. AND the $3.71 was prorated low because the public-subnet ECS tasks weren't running 24x7 for the full window.

### Net VPC cost next cycle
- Endpoints: $35.94 -> $0 (gone)
- Public IPv4: $3.71 -> ~$25 (true steady-state)
- **Net VPC line next cycle: ~$25/mo** (was projected ~$0, reality is +$25 from the ECS public-subnet design tradeoff)

### How to cut further (deferred)

1. **IPv6-only ECS tasks (Fargate platform 1.4+):** assign IPv6 instead of IPv4. AWS does not charge for IPv6. Requires VPC IPv6 enablement + ALB IPv6 listener + container image registry support. Not a small change. Potential -$25/mo gross. Defer.

2. **Reduce running ECS task count via scale-to-zero** (see Section 3): each scaled-to-zero service saves ~$1/mo from the IPv4 fee in addition to the Fargate compute saving.

3. **Pre-bake-in-CI + restore-from-cached-blobs** would let tasks run on private subnets with no NAT + no VPC endpoints + no public IP. Massive architectural change. Out of scope.

**Estimated additional savings (next billing cycle):** $0 deliberate cut; -$5/mo organic if ECS scale-to-zero ships (per Section 3).

## Section 2: ELB ($19.69/mo, 22% of gross) - DRILLED 2026-05-19

### What it is (verified)
| Sub-component | Amount | Notes |
|---|---|---|
| LoadBalancerUsage (ALB-hours) | $10.87 | Fixed hourly rate (~$0.0225/hr * 720h = ~$16/mo at full month; the $10.87 is prorated to W1-T2's apply date partway through the window) |
| LCUUsage | $8.82 | Bursty earlier in window; current state is near-zero per `cloudwatch:ConsumedLCUs` |

### Why it exists
W1-T5 (PR #362) removed 11 internal NLBs and consolidated services onto a shared ALB. The remaining ALB carries all internal + external traffic via 8 target groups.

### Drill findings (2026-05-19)
- **ConsumedLCUs over the last 7 days: avg 7.6e-07 LCUs/day max 1.4e-04** (basically zero). The $8.82 LCU bill came from bursty traffic earlier in the 30-day window (~first 23 days, before the recent quiescence).
- **8 target groups, 0 listener rules** on the main listener (everything routes via default rule).
- Steady-state LCU usage is dominated by no traffic; will drop organically next billing cycle.

### How to cut
1. **No active LCU optimization needed** - already near-zero usage. The $8.82 will drop to <$2/mo next month organically as the high-LCU early-window days roll off.
2. **LoadBalancerUsage is fixed-floor** for the ALB; ~$16/mo at full month (one ALB, always-on). Reducing this would mean replacing ALB with API Gateway HTTP API for ALL external routes, which is a bigger architectural change (API GW HTTP API: $1/M requests + $0/hr; ALB: $0.0225/hr regardless + $0.008/LCU-hour). For dev's near-zero request volume, API GW would be ~$0/mo gross, saving the full $16/mo. But: every external service's contract would shift, and we'd lose the ALB's mTLS / sticky-session / WAF-attach capabilities. Not worth the architectural churn for $16/mo.

**Verified savings (organic):** ~-$5-7/mo gross next billing cycle as the LCU bursty days roll off. **Architectural-cut savings (deferred):** ~$16/mo gross if ALB swapped for API GW HTTP API; deferred as bigger change than the math warrants.

## Section 3: ECS / Fargate ($9.52/mo, 11%)

### What it is
Fargate task-hours for the 11+ panakoes-dev services.

### Why it exists
Already on FARGATE_SPOT per PR #369 (~70% discount on Fargate vCPU/memory rates).

### How to cut
1. **Idle-scale-to-zero candidates:** the 4 dev services scaled to `desired_count = 0` (billing / gpu-spawner / ingestion-api / query-api) save 4x task-hours each. The remaining 7-8 services run 24x7. Are all of them actually called daily in dev? Candidates for further scale-to-zero (per service): health-aggregator (dev metric collection, can be on-demand), cost-api (read once daily by admin SPA), audit-log-reader (event-driven). Each scale-to-zero saves ~$1/mo.
2. **EventBridge wake-up:** for services that are needed occasionally, EventBridge-on-schedule starts them; service auto-shuts after N min idle.

**Estimated savings on ECS:** $3-6/mo gross.

## Section 4: CloudWatch ($8.00/mo, 9%) - DRILLED 2026-05-19

### What it is (verified)
| Sub-component | Amount | Notes |
|---|---|---|
| CW:MetricMonitorUsage | $7.99 | Custom-metric publishing; covers `ECS/ContainerInsights` (lingering names) + `panakoes/dev` (8 active metrics) |
| CW:AlarmMonitorUsage | $0.00 | No active alarms |
| Log ingestion / vended logs / data processing | $0.00 | Already on 7d retention per PR #369 |

### Why it exists
Container Insights was disabled by PR #363, saving -$44/mo immediately. But CloudWatch keeps metric NAMES alive for 15 months by default (data retention); the BILLING for `MetricMonitorUsage` decays over the next ~2 weeks as the metric is "active in the billing window." That trailing-edge billing is the $5-6 of the $7.99.

### Drill findings (2026-05-19)
- **2 custom namespaces**: `ECS/ContainerInsights` (233 metric names, all with 0 datapoints last 24h) + `panakoes/dev` (8 metric names, all `<svc>.errors_total`, actively-publishing).
- ContainerInsights publish has been OFF since PR #363; the 233 namespace-billing-window will fade over ~2 weeks.
- Steady-state CloudWatch = ~$2.40/mo (8 active custom metrics * $0.30/metric/month).

### How to cut
1. **No action needed.** ContainerInsights billing-window fade is organic; CloudWatch will drop $7.99/mo to ~$2.40/mo over the next 2 weeks. **Free -$5.59/mo gross.**
2. **Future optimization** (not needed today): the 8 `<svc>.errors_total` metrics could be consolidated to a single `panakoes/dev.errors_total` with a `service` dimension (1 metric instead of 8), saving ~$2/mo. Trade-off: less granular dashboarding. Defer until cost matters.

**Verified savings (organic):** -$5.59/mo gross next billing cycle. **Additional architectural cut available:** -$2/mo via metric-dimension consolidation; deferred.

## Section 5: EC2 - Other ($6.23/mo, 7%) - DRILLED 2026-05-19

### What it is (verified)
| Sub-component | Amount |
|---|---|
| NatGateway-Hours | $5.44 |
| EBS:SnapshotUsage | $0.76 |
| Other (NatGateway-Bytes, EBS:VolumeUsage.gp3, DataTransfer-Regional-Bytes) | <$0.03 |

### Why it exists
The NAT Gateway hours represent a NAT that was removed by PR #369 (Tier 1 cost cuts) mid-month. Live state has zero active NAT Gateways. The $5.44 is prorated billing from the ~5 days the NAT existed before destruction. EBS snapshots are the recent RDS snapshot+restore work (W2-T5) plus auto-snapshots.

### How to cut

**No action needed.** The NAT cost is non-recurring (the NAT is gone; billing rolls off next cycle). Expected steady-state EC2-Other = ~$0.78/mo (snapshots only). Net change: -$5.45/mo gross from current month to next.

EBS snapshot cost is real but small; the RDS pre-migration snapshot + re-encrypted copy from W2-T5 (~140 GB combined) account for most of it. Retiring v1 RDS instance + pre-migration snapshots after burn-in clears the bulk of this too.

**Verified savings:** -$5.45/mo gross next billing cycle, automatic.

## Section 6: KMS ($5.78/mo, 7%)

### What it is
Customer-Managed Key (CMK) hours + API request rate (most $$ is from key hours: $1/key/month).

### Why it exists
Currently has ~5-6 per-service CMKs from the pre-Wave-2 architecture. Wave 2 T1 (PR #365) introduced 2 consolidated CMKs (`panakoes/app-data` + `panakoes/logs`) but did not yet retire the per-service ones.

### How to cut
1. **Wave 2 T2-T7 (already in backlog):** migrate the per-service CMKs to the consolidated ones, retire the per-service keys. Net: -3 to -4 keys, -$3 to -$4/mo gross.

**Estimated savings:** $3-4/mo gross. Already scoped; needs the apply pass.

## Section 7: RDS ($3.14/mo, 4%)

### What it is
The auth-db RDS instance.

### Why it exists
Better-Auth needs a real Postgres; SQLite isn't right for production-like dev.

### How to cut
1. **Aurora Serverless v2 with scale-to-zero (cold-start latency ~5s):** for dev where idle is most of the day, scaling to zero saves ~80% of instance time. At our scale that's ~$2/mo.
2. **Smaller instance class** (`db.t4g.micro` if we're on something larger).

**Estimated savings on RDS:** $1-2/mo gross. Small but free if we're already not using it 24x7.

## Section 8: WAF ($2.28/mo, 3%)

### What it is
Web ACL with rules; baseline floor.

### Why it exists
Security baseline on the public ALB.

### How to cut
Probably not worth optimizing for $2/mo. Leave alone.

## Section 9: Sequence of cuts (ordered by savings vs effort)

1. **Wave 2 KMS T2-T7** (estimated -$3 to -$4/mo gross). Already designed. ~2-3h of agent work.
2. **VPC Endpoint audit + single-AZ for dev** (-$15-25/mo gross). 1-2h of investigation + 1-2h of Terraform changes. Highest-leverage single lever.
3. **ECS idle-scale-to-zero for 3-4 services** (-$3-6/mo gross). 1-2h of CloudWatch alarm + ECS scale config + EventBridge wake-up plumbing.
4. **EC2-Other drill** (-$2-4/mo gross, possibly more). 30min of investigation.
5. **ELB LCU optimization** (-$5-10/mo gross). 2-3h of listener-rule audit + reconfiguration.
6. **CloudWatch custom-metric audit** (-$2-4/mo gross). 1h of investigation.
7. **RDS Serverless v2 scale-to-zero** (-$1-2/mo gross). 30min Terraform change + verify cold-start is acceptable for dev.

**Total potential cuts: $31-55/mo gross.** Bringing the post-cuts gross to $33-57/mo.

## Section 10: What we are deliberately NOT cutting

- **Activate Founders credits coverage** is real and absorbs everything for the next 11 months. The gross-cost work is mostly portfolio + post-credits-exhaustion preparation. We are not in a real budget crunch.
- **Public IPv4 ($3.71/mo)** for ALB ingress is required for public-facing services; not cuttable.
- **Route 53 ($0.50/mo)** + Secrets Manager ($1.01/mo) + Cost Explorer ($0.31/mo) are baseline fixed floors.
- **WAF ($2.28/mo)** is security baseline; cutting risks portfolio + production posture.

## Section 11: Operational notes for next session

- Re-pull the live numbers (this doc's data is from 2026-05-19; will drift weekly).
- Wave 2 KMS T2-T7 is the lowest-risk highest-confidence cut. Ship that first.
- VPC endpoint audit needs Phil's review per-endpoint (which services genuinely call which AWS APIs from inside the VPC).
- CloudWatch + ECS optimizations are independent; can be parallel agents in different worktrees.

## Appendix A: Re-pull-on-demand command

```bash
START=$(date -u -d '30 days ago' +%Y-%m-%d)
END=$(date -u +%Y-%m-%d)
AWS_PROFILE=panakoes-admin aws ce get-cost-and-usage \
  --time-period Start=$START,End=$END \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --filter '{"Dimensions":{"Key":"RECORD_TYPE","Values":["Usage"]}}' \
  --output json | jq -r '
    [.ResultsByTime[].Groups[] | {key: .Keys[0], amt: (.Metrics.UnblendedCost.Amount | tonumber)}]
    | group_by(.key)
    | map({key: .[0].key, total: (map(.amt) | add)})
    | sort_by(-.total)
    | .[]
    | "$\(.total | tostring | .[:6]) \(.key)"'
```

## Appendix B: This doc's status

- v1 skeleton: data accurate, recommendations directional. Each "cut" section is a starting point for a focused agent dispatch, not a finished plan.
- Next pass should add: terraform-file references for each cut, dry-run terraform plan output for the easy ones (KMS + RDS), an EventBridge schedule design for ECS idle-scale-to-zero, and per-VPC-endpoint usage data so the audit recommendation is concrete.
