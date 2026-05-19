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

## Section 1: VPC ($39.65/mo, 45% of gross) - the biggest target

### What it is
| Sub-component | Amount | Notes |
|---|---|---|
| VPC Interface Endpoint hours | $35.94 | At $0.01/hour/endpoint/AZ, this is ~5 endpoints x 2 AZs running 24x7 (~10 endpoint-AZ-hours of billing per day, $0.10/day * 30d * 12 = $36) |
| Public IPv4 InUse | $3.71 | NAT Gateways or ALB public IPs |
| Public IPv4 Idle | <$0.01 | Negligible |

### Why it exists
Interface endpoints provide private routing from VPC to AWS service APIs (ECR, Secrets Manager, KMS, CloudWatch Logs, S3, etc.) without traversing the public internet. Each endpoint is per-AZ, billed per-hour. The Sunday-Monday session disabled the most expensive ones (NAT replacement migration) but a residual set persists.

### How to cut
1. **`aws ec2 describe-vpc-endpoints` audit:** list current endpoints with attached subnets. For each, ask: "is the service actually called from inside the VPC?" If not, delete. Likely candidates to prune (dev environment):
   - Cost Explorer (we call CE from outside the VPC, locally; the endpoint is unused)
   - Possibly STS, Secrets Manager (if services use IAM roles + KMS directly)
2. **Single-AZ for dev:** endpoints are per-AZ. Dev doesn't need HA. Picking one AZ per endpoint cuts cost by half on each. Potential savings: ~$18/mo if 4-5 endpoints go single-AZ.
3. **Consolidate to one shared endpoint where possible** (e.g., ECR + ECR-DKR are 2 separate endpoints; some workloads can use one).

**Estimated total savings on VPC:** $15-25/mo gross.

## Section 2: ELB ($19.69/mo, 22% of gross)

### What it is
| Sub-component | Amount |
|---|---|
| LoadBalancerUsage (ALB-hours + NLB-hours) | $10.87 |
| LCUUsage (Load Balancer Capacity Units) | $8.82 |

### Why it exists
W1-T5 (PR #362) removed 11 internal NLBs and consolidated services onto a shared ALB. The remaining ALB carries all internal + external traffic. LCU billing is the larger half: each LCU = 1 of (new connections / active connections / processed bytes / rule evaluations) per second.

### How to cut
1. **Listener rule audit:** more rules = more LCU. Are all 11+ target groups still needed? PR #362's consolidation may have left unused rules.
2. **LCU optimization:** if any of the LCU dimensions is the binding constraint, dial it down (e.g., reduce rule evaluations by consolidating prefix matches; reduce active connections by enabling Keep-Alive properly).
3. **External-facing API GW vs ALB:** for routes called from outside the VPC (which is "all of them" per W1-T3's API GW + ALB header routing), check whether API GW HTTP API would be cheaper than ALB for low-traffic dev (API GW HTTP API is $1.00 per million requests + no hourly).

**Estimated savings on ELB:** $5-10/mo gross.

## Section 3: ECS / Fargate ($9.52/mo, 11%)

### What it is
Fargate task-hours for the 11+ panakoes-dev services.

### Why it exists
Already on FARGATE_SPOT per PR #369 (~70% discount on Fargate vCPU/memory rates).

### How to cut
1. **Idle-scale-to-zero candidates:** the 4 dev services scaled to `desired_count = 0` (billing / gpu-spawner / ingestion-api / query-api) save 4x task-hours each. The remaining 7-8 services run 24x7. Are all of them actually called daily in dev? Candidates for further scale-to-zero (per service): health-aggregator (dev metric collection, can be on-demand), cost-api (read once daily by admin SPA), audit-log-reader (event-driven). Each scale-to-zero saves ~$1/mo.
2. **EventBridge wake-up:** for services that are needed occasionally, EventBridge-on-schedule starts them; service auto-shuts after N min idle.

**Estimated savings on ECS:** $3-6/mo gross.

## Section 4: CloudWatch ($8.00/mo, 9%)

### What it is
Log ingestion + storage + metric publishing.

### Why it exists
Already on 7d retention (PR #369). Container Insights disabled (PR #363, -$44/mo). What remains: ECS Service Connect metrics, application-level metric publishing, baseline AWS resource metrics.

### How to cut
1. **`cloudwatch list-metrics` audit:** which custom metrics are actually consumed by dashboards / alarms? Drop unconsumed publish calls.
2. **Log subscription filter audit:** any active filters that publish to a Lambda or Kinesis? Those add ingestion + per-event cost.
3. **Service Connect metrics:** can be opt-in per service rather than blanket.

**Estimated savings on CloudWatch:** $2-4/mo gross.

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
