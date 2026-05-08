# Incident Response Runbook

## When to use this runbook

Reach for this runbook the moment something is wrong in dev or production:

- A CloudWatch alarm fires and is published to the `panakoes-dev-system-alerts` SNS topic.
- A user-visible behavior breaks (login fails, transcripts fail to generate, billing errors).
- A deployment causes a regression detectable in logs, traces, or smoke tests.
- An external dependency degrades (Stripe outage, Anthropic API outage, AWS service event).
- A security signal fires (gitleaks alert, GitHub secret scanning alert, suspicious audit-log entry).

Use the `disaster-recovery.md` runbook only when this incident requires foundational data or infrastructure recovery (state corruption, RDS PITR, etc.). Most incidents are resolved with the rollback procedures in this file alone.

## Prerequisites

- AWS CLI authenticated against the Panakoes AWS account.
- `gh` CLI authenticated.
- Read access to CloudWatch dashboards and the audit-log table `panakoes-dev-audit-log`.
- Ability to subscribe to or read the `panakoes-dev-system-alerts` SNS topic (an email subscription is the minimum; PagerDuty / phone alerts come later).

## Procedure

### 1. Detection

Incidents enter the runbook through one of these channels:

1. **CloudWatch alarms.** Alarm state transitions to `ALARM` and publishes to the `panakoes-dev-system-alerts` SNS topic. Subscribe at least one durable channel (email, eventually PagerDuty) to that topic.
2. **GitHub Actions failures.** A workflow on `main` fails (deploy, post-merge smoke). The failure email plus the Actions summary is the primary signal.
3. **External monitoring.** Cloudflare uptime checks against `panakoes.com`, third-party status pages for Stripe / AWS / Anthropic.
4. **User report.** Until the user base is non-zero this is rare; once it exists, treat any user report with the same severity rubric below.
5. **Self-detection during dev work.** A test fails locally in a way that suggests the live system is also broken; treat as detection.

For each detection, capture immediately: timestamp (UTC), source (alarm name / workflow ID / who reported), the raw signal text or URL.

### 2. Triage and severity

Use this matrix. Assign a severity within 5 minutes of detection.

| Severity | Definition | Examples | Response cadence |
|---|---|---|---|
| **SEV-1** | Customer-facing impact: paying users cannot complete the core flow (signup, upload, transcribe, view, pay). Data loss is in progress or has occurred. Security incident with a confirmed breach. | RDS down, transcription pipeline silently dropping jobs, exposed credential confirmed in public history, billing webhook silently failing. | Drop everything. Engage immediately. Status update every 30 minutes until mitigation. |
| **SEV-2** | Degraded but partially functional: feature broken for a subset of users, performance unacceptable, retries succeed but slowly, batch jobs queueing without processing. | Streaming sessions fail to spawn within 90 seconds, summary generation timing out for long transcripts, CloudWatch alarms firing on error rate > 5%. | Engage within 30 minutes of detection. Status update at start, on mitigation, at resolution. |
| **SEV-3** | Internal-only impact: dev environment broken, CI flaky, observability gap, runbook found wrong. No user-visible effect. | Dev pipeline breaks but prod (when it exists) is unaffected, a CloudWatch dashboard panel returns no data, a follow-up ticket is uncovered. | Engage within one business day. Document and fix; no status updates needed beyond the resulting PR. |

Severity can escalate. A SEV-3 that turns out to mask a SEV-1 (rare but possible) escalates the moment the broader impact is identified.

### 3. Communication

Audience and channels (today and where they evolve):

- **Today (solo).** Phil is the operator and the audience. Capture the timeline in a scratch file (`/tmp/incident-<UTC-timestamp>.md` or a doc in `~/Documents/Facebook/`) so the postmortem has source material. Tag the relevant `.agent-runs/` report if the incident was triggered by an agent's commit.
- **Once a team or co-maintainer exists.** Spin up a shared incident channel (Slack, Discord, or GitHub Discussions). Status updates land there. Same content as the scratch file, just in a shared location.
- **Once there are paying customers.** Public status page (planned: `status.panakoes.com`, host on Cloudflare or BetterStack). SEV-1 and SEV-2 customer-affecting incidents publish updates there. SEV-3 stays internal.

Status update template (use it from day one, even when the audience is just Phil):
```
[YYYY-MM-DD HH:MM UTC] [SEV-X] [<short title>]
What we know: <facts>
What we are doing: <action>
ETA to next update: <duration>
```

### 4. Mitigation

Mitigate first, root-cause second. The goal of incident response is to stop the bleeding; root-cause analysis happens after.

#### 4a. Revert a recent PR

If the incident correlates with a recent merge to `main`, revert the PR. The Conventional Commit history makes blame unambiguous; the revert is a one-liner.

```bash
# Identify the offending PR.
gh pr list --state merged --base main --limit 10

# Revert it. This opens a revert PR; merge it as soon as CI passes.
gh pr revert <PR-number>
gh pr merge <revert-PR-number> --squash --auto --delete-branch
```

The revert ships through the same `--auto` merge flow used for normal PRs (`workflow_panakoes_pr_flow`). Required checks must still pass; in incident mode this is a feature, not a bug, since it prevents the revert itself from breaking things further.

#### 4b. CloudFront invalidation (website)

The SvelteKit static site is served from S3 + CloudFront. A bad asset can be cached at edges; invalidate after redeploying:

```bash
aws cloudfront create-invalidation \
  --distribution-id <distribution-id> \
  --paths "/*"
```

For targeted invalidation (faster, cheaper), pass specific paths instead of `/*`. Keep `/*` for SEV-1 site-wide breakage.

#### 4c. Lambda alias rollback

Lambdas use versioned aliases (`live`, `canary`) per the deployment pattern. Rolling back is a one-call alias update:

```bash
# Identify the prior good version.
aws lambda list-versions-by-function --function-name <fn>

# Repoint the live alias.
aws lambda update-alias \
  --function-name <fn> \
  --name live \
  --function-version <prior-version-number>
```

For Lambda functions deployed by Terraform with `publish = true`, the version number ladders monotonically; the prior good version is whatever the latest version was before the bad deploy.

#### 4d. ECS service rollback

An ECS Fargate service running a bad image rolls back via `update-service` to the prior task definition revision:

```bash
aws ecs list-task-definitions --family-prefix <service-family>
aws ecs update-service \
  --cluster <cluster> \
  --service <service> \
  --task-definition <family>:<prior-revision> \
  --force-new-deployment
```

If the bad image is the issue (not the task definition), use the ECR retag procedure in `disaster-recovery.md` Lane E and force a new deployment.

#### 4e. Disable a broken feature flag or env var

For runtime-toggleable features, flip the flag via SSM Parameter Store or Secrets Manager and force the consuming service to reload:

```bash
aws ssm put-parameter --name <param> --value <safe-value> --overwrite
aws ecs update-service --cluster <cluster> --service <service> --force-new-deployment
```

Keep an eye on services that cache config in memory; a force redeploy is the simplest cache-buster.

### 5. Forensics

Once the system is mitigated and stable, gather the data needed for a postmortem.

1. **Audit log queries.** The DynamoDB table `panakoes-dev-audit-log` records application-level events (per ADR-023 / ADR-017 in `PLANNING.md`). Query for the suspect time window:
   ```bash
   aws dynamodb query \
     --table-name panakoes-dev-audit-log \
     --index-name <gsi-name> \
     --key-condition-expression "<key-condition>" \
     --filter-expression "occurred_at BETWEEN :start AND :end" \
     --expression-attribute-values '{":start":{"S":"<ISO-UTC>"},":end":{"S":"<ISO-UTC>"}}'
   ```
2. **CloudTrail queries.** Management-plane events (IAM changes, console logins, infra modifications) live in CloudTrail. Cross-reference with the audit log; together they cover both app and AWS API surfaces.
3. **CloudWatch Logs Insights.** Cross-service log correlation:
   ```bash
   aws logs start-query \
     --log-group-names "/aws/lambda/<fn1>" "/ecs/<service1>" \
     --start-time <epoch-start> \
     --end-time <epoch-end> \
     --query-string 'fields @timestamp, @message | filter @message like /<pattern>/'
   ```
4. **X-Ray traces.** For latency or cross-service issues, the X-Ray service map and individual traces are the fastest path. Filter by trace ID or by error-status segments.
5. **Stripe events** (if billing-related): `stripe events list --limit 50` and walk the IDs back through the audit log to confirm idempotency keys behaved.

### 6. Post-incident actions

Within 48 hours of resolution, complete the post-incident workflow.

1. **5-whys.** Drive each "why" until the answer is a process, design, or system gap, not a person. Capture the chain in the postmortem doc.
2. **Postmortem.** Write a postmortem with sections: Summary, Timeline (UTC, minute-resolution), Detection, Triage, Mitigation, Root cause (5-whys output), What went well, What went badly, Action items. File it under `docs/postmortems/<YYYY-MM-DD>-<short-slug>.md` (create the directory on first use).
3. **Action items.** Each one is a tracked GitHub issue with an owner and a follow-up due date. Anything labeled "make sure this never recurs" must produce either a code change, an automated test, an alarm, or a runbook update; vague resolutions do not count.
4. **Update relevant runbook.** If this runbook (or `disaster-recovery.md`, or `dev-troubleshooting.md`) was wrong, missing a step, or pointed at the wrong command, update it in the same PR cycle as the postmortem. The incident is not closed until the runbook reflects what was actually done.
5. **Update CLAUDE.md if a recurring pattern emerged.** New "lessons" (per the `feedback_panakoes_lessons` memory) get a one-line entry plus a memory update so future agent runs avoid the trap.

## Verification

The incident is resolved when:

1. The original detection signal has cleared (alarm back to `OK`, workflow green, user report acknowledged and verified fixed).
2. Downstream synthetic checks (smoke tests) pass.
3. No new alarms have fired in the 30 minutes following mitigation.
4. The audit log shows expected events and no anomalous ones.
5. The postmortem is filed and action items are tracked.

## Rollback

The mitigation steps in section 4 each include a rollback path: a revert commit can itself be reverted, an alias can be re-pointed, an invalidation can be re-issued. If a mitigation makes the incident worse (rare but possible), apply the inverse operation immediately and treat the meta-incident as a new SEV-1.

## References

- `CLAUDE.md` "Discipline Rules" for the rollback-via-revert posture (no `git reset --hard` on shared history, no force-push to main).
- `PLANNING.md` ADR-015 for the observability stack (CloudWatch + X-Ray + ADOT) that drives detection.
- `PLANNING.md` ADR-017 for the audit-trail decision (DynamoDB custom log + CloudTrail).
- `docs/adr/ADR-023-audit-library-three-backends.md` for the audit library that writes to `panakoes-dev-audit-log`.
- `disaster-recovery.md` for foundational data or infra recovery (state, RDS PITR, DynamoDB PITR, S3 versioning, ECR retag, GitHub repo).
- `dev-troubleshooting.md` for local-dev issues that are not production incidents.
- `workflow_panakoes_pr_flow` memory for the standard PR flow used by revert PRs.
- `feedback_panakoes_lessons` memory for the lesson-capture pattern that postmortem action items feed into.
