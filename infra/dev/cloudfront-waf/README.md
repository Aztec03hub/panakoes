# Dev Environment CloudFront WAF

Per-environment Terraform configuration creating the CloudFront-scoped
WAFv2 web ACL that fronts the dev SvelteKit admin distribution
(`panakoes-dev-admin`, viewer host
`https://dmaopcm3hnxog.cloudfront.net/`).

## Why a second WAF module

AWS WAFv2 partitions web ACLs by `scope`. The ACL in `infra/dev/waf/`
is `REGIONAL` and can only attach to ALB / API Gateway / AppSync.
CloudFront requires `scope = "CLOUDFRONT"`, and CloudFront ACLs MUST
live in us-east-1 regardless of where the rest of the stack runs.
The two scopes cannot share an ACL. This module owns the
CloudFront-scoped half of the dev WAF posture.

## What this creates

- `aws_wafv2_web_acl.cloudfront` named
  `panakoes-dev-cloudfront-acl` (CloudFront scope, default-allow).
- `aws_cloudwatch_log_group.waf` named
  `aws-waf-logs-panakoes-dev-cloudfront` (30-day retention,
  KMS-encrypted).
- `aws_kms_key.waf` aliased `alias/panakoes-dev-cloudfront-waf`
  (rotation enabled, 7-day deletion window). The key policy
  delegates encrypt rights to `logs.us-east-1.amazonaws.com`,
  scoped to this log group's ARN.
- `aws_wafv2_web_acl_logging_configuration.cloudfront` wiring the
  ACL to the log group, with the `Authorization` and `Cookie`
  headers redacted in delivered logs so bearer tokens and session
  cookies do not appear in CloudWatch.

The CloudFront distribution itself is NOT created here; it lives in
`infra/dev/frontend/`. The association is set by that module via its
`web_acl_id` attribute, which reads this module's `web_acl_arn`
output via a `terraform_remote_state` data source.

## Rule list (priority order)

| Priority | Rule | Action | Notes |
|----------|------|--------|-------|
| 1 | `AWSManagedRulesCommonRuleSet` | Managed (block) | Broad OWASP-Top-10 coverage. No overrides; the admin SPA does not PUT arbitrary bodies through CloudFront. |
| 2 | `AWSManagedRulesKnownBadInputsRuleSet` | Managed (block) | Active-exploit signatures (Log4Shell, Spring4Shell, etc). |
| 3 | `AWSManagedRulesAmazonIpReputationList` | Managed (block) | AWS-curated malicious IPs (botnets, anonymizers, scrapers). |
| 4 | `RateLimitPerIP` | Block | 2000 requests per 5 minutes, keyed by source IP. |

### Managed rule choice rationale

`CommonRuleSet`, `KnownBadInputsRuleSet`, and
`AmazonIpReputationList` are the three managed groups with the best
signal-to-noise ratio for a static SPA + bearer-authenticated JSON
fetch surface. The regional WAF in `infra/dev/waf/` additionally runs
`SQLiRuleSet` because the public API services accept query payloads.
We deliberately do not duplicate SQLiRuleSet here: the same JSON
bodies pass through the regional WAF when the SPA calls API Gateway,
so doubling the inspection would double-bill request evaluation
without adding meaningful security. Add SQLiRuleSet at the
CloudFront layer only if a SQL-touching endpoint is ever served
directly through CloudFront (e.g. a server-rendered admin page).

`AnonymousIpList`, `BotControlRuleSet`, and `AccountTakeoverPrevention`
are intentionally excluded: AnonymousIpList false-positives on
legitimate VPN users (Phil works behind one daily), and the latter
two are paid-tier features with $10+/month base cost that are not
justified at dev scale.

### Rate-limit threshold

2000 requests / 5-minute rolling window per source IP. Sizing
rationale:

- A cold first-page load of the SvelteKit admin SPA fetches HTML,
  the entry JS chunk, CSS, fonts, and async route chunks: well under
  100 requests in practice.
- An operator hammering refresh and clicking through every dashboard
  tab over a 5-minute window tops out in the low hundreds.
- 2000 leaves an order of magnitude of headroom for the worst real
  user while still tripping on credential-stuffing, scrape attempts,
  and naive denial-of-service from a single IP.

The threshold is exposed via `var.rate_limit_per_5min` so dev can
ratchet it up (or down to count-mode by adjusting the rule action)
in response to observed false-positive metrics.

### Why no scope-down on the rate-limit rule

The regional WAF rate-limit rule excludes `/health` so ALB liveness
probes are never throttled. CloudFront has no equivalent probe
surface: viewers connect to the CloudFront edge, which forwards to
the S3 origin via OAC. There is no traffic at the CloudFront layer
that mimics a probe loop and merits an exemption, so the rate-limit
rule applies universally.

## False-positive risk for the SPA's own JS bundle

CommonRuleSet's body-content rules
(`SizeRestrictions_BODY`, `GenericRFI_BODY`,
`CrossSiteScripting_BODY`) inspect request bodies, not response
bodies. The CloudFront viewer-to-edge traffic for a static SPA is
overwhelmingly GET requests with no body. The two scenarios worth
flagging:

1. **CSP report endpoints.** If the SPA later wires a CSP
   `report-uri` pointing through CloudFront, the JSON CSP report
   posts could trip XSS-body rules on legitimate report content.
   Mitigation: send CSP reports to a dedicated subdomain or to a
   third-party report collector, not through this distribution.
2. **Long query strings.** Some SvelteKit hydration paths emit
   verbose `?from=<encoded-url>` query strings on login redirects.
   `SizeRestrictions_QUERYSTRING` defaults to 1024 bytes. Modern
   redirect URLs comfortably fit, but a deeply-nested return path
   could theoretically trip the rule. The CloudWatch sampled-requests
   panel surfaces any such trip; remediate by overriding the rule
   to `count` if it happens.

The JS bundle itself (responses from CloudFront to the viewer) is
NOT inspected by WAF (WAF is a request-side control), so there is
zero risk of the WAF blocking, mutating, or rate-limiting the
delivery of `app.<hash>.js`, vendor chunks, or any static asset.

## Viewing blocked-request samples

The WAF console aggregates the most recent 100 blocked / counted
requests per rule, with full request fingerprints minus the
redacted headers.

1. AWS Console → WAF & Shield → Web ACLs.
2. Region filter at top right: **Global (CloudFront)**.
3. Open `panakoes-dev-cloudfront-acl`.
4. **Sampled requests** tab. Pick a rule and a time window. The
   table shows the source IP, country, URI path, HTTP method,
   labels applied, and the action taken (BLOCK / COUNT / ALLOW).
5. Click any row for the full request structure (headers, query
   string, body fingerprint) used to evaluate the rule.

For CLI-driven investigation:

```bash
aws wafv2 get-sampled-requests \
  --web-acl-arn "$(terraform output -raw web_acl_arn)" \
  --rule-metric-name panakoes-dev-cloudfront-acl-rate-limit \
  --scope CLOUDFRONT \
  --region us-east-1 \
  --time-window StartTime=2026-05-11T00:00:00Z,EndTime=2026-05-11T01:00:00Z \
  --max-items 100
```

For longer-window analysis, query the CloudWatch log group
`aws-waf-logs-panakoes-dev-cloudfront` directly via Logs Insights:

```
fields @timestamp, action, terminatingRuleId, httpRequest.clientIp, httpRequest.uri
| filter action = "BLOCK"
| sort @timestamp desc
| limit 200
```

## Apply

```bash
cd infra/dev/cloudfront-waf
AWS_PROFILE=panakoes-admin terraform init
AWS_PROFILE=panakoes-admin terraform plan -lock-timeout=2m
AWS_PROFILE=panakoes-admin terraform apply
```

After this module applies, re-apply `infra/dev/frontend/` so the
CloudFront distribution picks up the new `web_acl_id` attribute via
its remote-state lookup. The change to the distribution is in-place
(no replacement) and propagates through CloudFront's edge network in
roughly 5 minutes.

## Cost expectations

- WAF web ACL: $5/month minimum.
- Managed rule groups: $1/month each ($3 total for the three
  AWS Managed rule groups).
- Custom rules: $1/month each (1 custom rule: rate-limit).
- Request evaluation: $0.60 per million requests inspected.
- KMS CMK: $1/month for the dedicated WAF logs key.
- CloudWatch Logs: $0.50/GB ingested + $0.03/GB stored. WAF emits one
  log record per inspected request; at dev volume this is pennies.

Total fixed monthly: roughly $10 before traffic. CloudFront request
volume for the dev admin SPA is bounded by Phil and a handful of
collaborators, so the per-request component is effectively zero.

## Outputs

| Output           | Type   | Purpose                                                                       |
|------------------|--------|-------------------------------------------------------------------------------|
| `web_acl_arn`    | string | ARN of the CloudFront-scoped web ACL; consumed by `infra/dev/frontend/`       |
| `web_acl_id`     | string | ID of the web ACL; used for CLI lookups                                       |
| `kms_key_arn`    | string | ARN of the WAF logs CMK                                                       |
| `log_group_name` | string | Name of the CloudFront WAF CloudWatch log group                               |
| `log_group_arn`  | string | ARN of the CloudFront WAF CloudWatch log group                                |
