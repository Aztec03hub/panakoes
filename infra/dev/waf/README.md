# Dev Environment WAF

Per-environment Terraform configuration creating the regional WAFv2
web ACL that fronts the public-facing Panakoes APIs once their
Application Load Balancers / API Gateways exist. The ACL is provisioned
ahead of the consumers so it can be associated as soon as those
resources land.

## What this creates

- `aws_wafv2_web_acl.public` named `panakoes-dev-public-acl`
  (regional scope, default-allow).
- `aws_cloudwatch_log_group.waf` named
  `aws-waf-logs-panakoes-dev-public-acl` (30-day retention,
  KMS-encrypted).
- `aws_kms_key.waf` aliased `alias/panakoes-dev-waf` (rotation
  enabled, 7-day deletion window). The key policy delegates encrypt
  rights to the regional CloudWatch Logs service principal, scoped to
  this log group's ARN.
- `aws_wafv2_web_acl_logging_configuration.public` wiring the ACL to
  the log group, with the `Authorization` and `Cookie` headers
  redacted in delivered logs so JWTs and session cookies do not
  appear in CloudWatch.

## Rule list (priority order)

| Priority | Rule | Action | Notes |
|----------|------|--------|-------|
| 1 | `AWSManagedRulesCommonRuleSet` | Managed (block) | `SizeRestrictions_BODY` and `GenericRFI_BODY` overridden to `count` (false-positive on legit JSON / audio metadata; body-size limits enforced in middleware-lib) |
| 2 | `AWSManagedRulesKnownBadInputsRuleSet` | Managed (block) | Active-exploit signatures (Log4Shell, Spring4Shell, etc) |
| 3 | `AWSManagedRulesAmazonIpReputationList` | Managed (block) | AWS-curated malicious IPs (botnets, anonymizers, scrapers) |
| 4 | `AWSManagedRulesSQLiRuleSet` | Managed (block) | SQL injection signatures across all request fields |
| 5 | `RateLimitPerIP` | Block | 1000 requests per 5 minutes, keyed by source IP. Scope-down `NotStatement` exempts `/health` so ALB liveness probes never get throttled |
| 6 | `GeoBlock` (commented out) | n/a | TODO: enable once a threat-driven country list is justified by traffic data; syntax skeleton present in `main.tf` |

JWT presence is not validated at the WAF layer. WAF cannot inspect
JWT signatures, and our auth flows (`/auth/sign-in`, `/auth/validate`)
must accept anonymous requests, so the auth service handles
authentication itself. The WAF's job is volumetric and structural
defense; identity is the service's job.

## Apply

    cd infra/dev/waf
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

The web ACL is **not** associated with any ALB / API Gateway /
CloudFront distribution at apply time, because those resources do
not yet exist. The ACL exists in `IDLE` state until associated;
Terraform billing for an unassociated regional ACL is the standard
$5/month minimum plus per-rule fees ($1/managed-rule-group, $1/custom
rule, $0.60/million requests evaluated against the empty association).

## Associating the ACL with downstream resources

Once an ALB or regional API Gateway exists in another module:

```hcl
data "terraform_remote_state" "waf" {
  backend = "s3"
  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/waf/terraform.tfstate"
    region = "us-east-1"
  }
}

resource "aws_wafv2_web_acl_association" "ingestion_alb" {
  resource_arn = aws_lb.ingestion.arn
  web_acl_arn  = data.terraform_remote_state.waf.outputs.web_acl_arn
}
```

For CloudFront (which is a global service requiring `scope = CLOUDFRONT`
and a us-east-1 ACL), this module is **not** the right ACL: a separate
`infra/dev/waf-cloudfront/` module will be added when the SvelteKit
front-end's CloudFront distribution lands.

## Constraints worth knowing

- **Log group name prefix.** AWS WAF rejects log-group destinations
  whose names do not start with the literal `aws-waf-logs-`. The brief
  asked for `/aws/wafv2/panakoes-dev-public-acl`; we adapted to
  `aws-waf-logs-panakoes-dev-public-acl` to satisfy the WAF
  constraint while preserving the ACL identifier in the suffix.
- **KMS key policy delegation.** CloudWatch Logs cannot encrypt to a
  CMK unless the key policy explicitly allows
  `logs.<region>.amazonaws.com` to use it. We pin that grant to this
  log group's ARN so unrelated log groups in the region cannot reuse
  the key.
- **Body-size enforcement.** WAF's default 8 KB body inspection
  ceiling collides with legitimate audio-metadata uploads. Body-size
  limits are enforced by the application layer (middleware-lib);
  WAF's `SizeRestrictions_BODY` is set to `count` so we still see the
  metric without blocking real users.
- **Rate-limit window.** AWS WAFv2 rate-based statements use a
  rolling 5-minute window even when `evaluation_window_sec` is set.
  The argument is documented but the underlying minimum is 60
  seconds. Setting 300 here is explicit-is-better-than-implicit.

## Consuming outputs from other configs

Downstream modules (ALB, API Gateway, log-export) read these outputs
via a `terraform_remote_state` data source pointing at this config's
state:

```hcl
data "terraform_remote_state" "waf" {
  backend = "s3"
  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/waf/terraform.tfstate"
    region = "us-east-1"
  }
}

# Then reference outputs as:
#   data.terraform_remote_state.waf.outputs.web_acl_arn
#   data.terraform_remote_state.waf.outputs.kms_key_arn
#   data.terraform_remote_state.waf.outputs.log_group_arn
```

## Cost expectations

- WAF web ACL: $5/month minimum.
- Managed rule groups: $1/month each ($4 total for the four AWS
  Managed rule groups).
- Custom rules: $1/month each (1 custom rule: rate-limit).
- Request evaluation: $0.60 per million requests inspected.
- KMS CMK: $1/month for the dedicated WAF logs key.
- CloudWatch Logs: $0.50/GB ingested + $0.03/GB stored. WAF emits one
  log record per inspected request; at dev volume this is pennies.

Total fixed monthly: roughly $11 before any traffic. Production should
revisit whether all four managed rule groups are pulling their weight
against observed false-positive rates.

## Outputs

| Output           | Type   | Purpose                                                   |
|------------------|--------|-----------------------------------------------------------|
| `web_acl_arn`    | string | ARN of the web ACL; passed to ALB / API Gateway associations |
| `web_acl_id`     | string | ID of the web ACL; used for CLI lookups                   |
| `kms_key_arn`    | string | ARN of the WAF logs CMK                                   |
| `log_group_name` | string | Name of the WAF CloudWatch log group                      |
| `log_group_arn`  | string | ARN of the WAF CloudWatch log group                       |
