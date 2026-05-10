# ADR-035: New AWS account friction mitigations

## Status

Accepted. Lived since 2026-05-09.

## Context

A fresh AWS account (or a new region in an existing account) carries a set of platform-level gates that do not apply to a warmed-up account. These gates are invisible until the first launch trips them, and they sit on the critical path of any workload that uses EC2 directly. We hit three distinct flavors during the GPU AMI bake on 2026-05-09 in account `659225405128`:

**1. `PendingVerification` on the first arbitrary `ec2:RunInstances` call.** The first Packer build for the GPU streaming AMI at ~19:30 CT was rejected with:

```
PendingVerification: Your request for accessing resources in this region is being validated, and will not be processed until the validation is complete. ... please allow up to 4 hours for this process to complete.
```

The gate fires once per account-region pair, on the first direct `RunInstances` call, regardless of instance type. It cleared via an emailed verification link a few minutes later (the documented window is "up to 4 hours" but in practice it was minutes). No charge is incurred on the rejected launch. The gate is invisible from the console quota pages and not surfaced by `aws ec2 describe-instances` or service-quotas; the only signal is the `RunInstances` rejection itself.

Critically, the gate fires for **direct** EC2 launches (Packer, `aws_instance`, AWS Batch compute environments that own their own EC2 fleet). It does **not** fire for managed services that abstract EC2 (NAT Gateway, RDS / Aurora, ALB / NLB, ECS Fargate). An account can have months of NAT-Gateway and RDS spend with no signal that the EC2 verification gate is still pending.

**2. `VcpuLimitExceeded` for the G/VT On-Demand instance bucket.** Immediately after the verification gate cleared at ~19:32 CT, the second Packer attempt was rejected with:

```
VcpuLimitExceeded: You have requested more vCPU capacity than your current vCPU limit of 0 allows for the instance bucket that the specified instance type belongs to.
```

The default vCPU quota for "Running On-Demand G and VT instances" (quota code `L-DB2E81BA`) on a fresh account is **zero**, not the more familiar 5 vCPU default that most instance families ship with. The `g4dn.xlarge` we needed for the GPU AMI bake is in this bucket, so the practical effect is "no GPU launches at all on a fresh account until you request a quota raise." This is independent of the `PendingVerification` gate; clearing one does not clear the other.

**3. `L-3819A6DF` ("All G and VT Spot Instance Requests") quota also defaults to zero.** Same instance family, the spot variant. The Whisper transcription compute environment is configured for `g4dn.xlarge` Spot, so the Tier-1 async-transcription path will fail at first launch with the spot-side equivalent of the on-demand `VcpuLimitExceeded` until this quota is also raised.

**Quota-request behavior.** Requests are submitted via:

```
aws service-quotas request-service-quota-increase \
  --service-code ec2 \
  --quota-code <L-...> \
  --desired-value <N> \
  --region <r>
```

The call returns a `RequestId`. Status flow:

- `PENDING` -> `APPROVED` (auto-approval, on the order of minutes) for small bumps to already-non-zero quotas.
- `PENDING` -> `CASE_OPENED` (routed to human review, hours to days) for first-time GPU vCPU requests on a fresh account.

Auto-approval favors incremental raises on warmed-up quotas. A fresh account asking for its first GPU vCPUs will reliably land in `CASE_OPENED`. An operator can speed a `CASE_OPENED` case by adding a use-case justification message to the auto-opened support case via the AWS Console -> Support Center. A free-tier (Basic Support) account cannot post on cases via the Support API, which returns `SubscriptionRequiredException`; the console UI is the only path.

## Decision

For every new LaFayette Labs AWS account, **and** for every new region in an existing account, perform a "new-account warm-up" sequence within the first week of provisioning, **before** any production launch depends on the account or region. The sequence has three steps:

**1. Trip the EC2 verification gate ahead of need.** Launch one `t3.micro` on-demand instance in the target region, let it run for 5 to 10 minutes, then terminate. The verification gate clears once and stays cleared for that account-region pair. Preferred form is a one-shot Terraform apply that creates and immediately destroys the instance (so the action is auditable in state history). A manual console click documented in the operator runbook is acceptable when Terraform isn't yet bootstrapped in the account.

**2. Pre-request likely-zero quotas.** For any account that will run GPU workloads, submit raises on day one for the GPU-family quotas that default to zero:

- `L-DB2E81BA` ("Running On-Demand G and VT instances") to a non-zero value, minimum 4 vCPUs (one `g4dn.xlarge`).
- `L-3819A6DF` ("All G and VT Spot Instance Requests") to a non-zero value, minimum 4 vCPUs (16 is reasonable for a small batch fleet).

Repeat this pattern for any other instance family the workload will touch (P family for high-end GPU training, Inf family for Inferentia, Trn for Trainium). The principle: any workload-relevant family that defaults to zero gets requested during the bootstrap window, not during the rollout window.

**3. Document the warm-up in the operator runbook.** Update `docs/operator/aws-cloudflare-actions.md` Section A so "AWS account bootstrap" is no longer just CloudTrail, budgets, IAM, and MFA. The warm-up steps (verification-gate trip + pre-request quotas) become first-class bootstrap items, called out as required before the account is considered production-ready.

## Consequences

**Positive.**

- Production-launch day no longer carries account-level platform friction. The team discovers the gates in dev, when waiting costs nothing, instead of during the rollout window, when an outage compounds.
- Quota cases get human review during the bootstrap window's business hours, not during the rollout window's "wait, why can't we launch?" panic. A `CASE_OPENED` that takes two business days to resolve is a non-event when surfaced on day one of a new account; the same delay during a launch is a P1.
- Interview-defensible answer for "what AWS gotchas have you hit in production?" The senior-architect frame is: new accounts are not warmed accounts, and the discipline is to surface platform friction in dev rather than discover it under load.
- The pattern composes with existing Terraform discipline. The verification-gate trip is a one-shot apply; the quota requests are AWS CLI calls that can be wrapped into a `scripts/aws-account-warmup.sh` helper for future LaFayette Labs accounts.

**Negative.**

- A few minutes of toil per new account / region. One `t3.micro` launch (cents in cost) plus two quota-request CLI calls.
- Quotas are scoped per-region. Multi-region operators repeat the dance per region. If `panakoes-dev` ever expands to `us-west-2` for HA, both the verification gate and the GPU quotas re-trip from zero in `us-west-2` despite being warmed in `us-east-1`. The operator runbook should call this out explicitly so the multi-region expansion does not surprise the operator on launch day.
- The pre-warm pattern doesn't help if AWS adds new platform gates in the future (a new "RunInstances on instance family X requires opt-in" check, a new "first ALB requires verification," etc.). The narrow defense is bounded; the broader discipline (treat new-account state as untrusted; surface platform friction in dev) generalizes and is the more durable lesson.
- Pre-requesting quotas before a clear use case can produce friction with AWS Trust & Safety review on the support case. The mitigation is to attach a one-paragraph use-case justification to the case immediately after the request lands in `CASE_OPENED`, citing the target workload (GPU AMI bake for Whisper transcription, batch inference for Panakoes).

## References

- 2026-05-09 incident: account `659225405128`, first GPU AMI bake attempt at ~19:30 CT.
- Quota request ids opened during the incident: `aac9d65f76c64de581b9891f99905a9eyaTBj9F8` (on-demand G/VT, desired 4 vCPUs) and `2384aa35afaf49d09f13858145110a4bZYSdTqMw` (spot G/VT, desired 16 vCPUs).
- Memory entry `aws_pending_verification_first_ec2_launch.md`, the operator-side codification of the on-demand verification gate.
- AWS knowledge center: [Why is my EC2 instance launch failing with PendingVerification?](https://repost.aws/knowledge-center/ec2-pending-verification).
- AWS docs: [Service quotas for Amazon EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html).
- `docs/operator/aws-cloudflare-actions.md` Section A (AWS account bootstrap), to be updated by a follow-up PR to incorporate the warm-up sequence.
- [`ADR-030: Admin bypass actor on the branch-protection ruleset`](ADR-030-ruleset-bypass-actor-for-emergencies.md), unrelated but a useful cross-reference for "platform-level gotchas the maintainer hit and codified."
