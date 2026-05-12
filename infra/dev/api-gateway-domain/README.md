# dev/api-gateway-domain

Provisions the public custom domain that fronts the Panakoes dev HTTP API:

```
https://api.dev.panakoes.com/v1/<service>/<path>
```

This sits in front of the default invoke URL from `infra/dev/api-gateway/`
(`https://n2un8ica69.execute-api.us-east-1.amazonaws.com/dev/...`). The default
URL keeps working alongside the custom domain; nothing else needs to change.

## Why split from `dev/api-gateway/`

ACM certificate validation has a human-in-the-loop wait: Phil adds a DNS record
to Cloudflare, then ACM observes it and flips the cert from `PENDING_VALIDATION`
to `ISSUED` over 5 to 30 minutes. Isolating that wait keeps the main api-gateway
module's apply cycle fast and predictable.

## Why `api.dev.panakoes.com` (not `api.panakoes.com`)

Per-environment subdomain keeps dev, staging, and prod cleanly separated. Prod
will use `api.panakoes.com` in a parallel `infra/prod/api-gateway-domain/`
module once that environment is bootstrapped.

## Two-phase apply

DNS for `panakoes.com` is authoritative on Cloudflare (registered 2026-05-07),
and this agent does not have Cloudflare API credentials. The apply happens in
two phases with a manual Cloudflare step in between.

### Phase 1: provision the certificate

Default state. `enable_domain_mapping = false` keeps the
`aws_apigatewayv2_domain_name` and `aws_apigatewayv2_api_mapping` resources at
`count = 0`, so the first apply provisions ONLY the cert.

```bash
cd infra/dev/api-gateway-domain
AWS_PROFILE=panakoes-admin terraform init
AWS_PROFILE=panakoes-admin terraform apply
```

Read the DNS-validation record from Terraform output:

```bash
AWS_PROFILE=panakoes-admin terraform output certificate_validation_records
```

You get one entry shaped like:

```json
[
  {
    "name":  "_abc123.api.dev.panakoes.com.",
    "type":  "CNAME",
    "value": "_def456.xxxxxxxx.acm-validations.aws."
  }
]
```

### Phase 2: add the validation record to Cloudflare

In the Cloudflare dashboard for `panakoes.com`:

1. DNS to Records to Add record.
2. Type: `CNAME`.
3. Name: paste the `name` from the output, minus the `.panakoes.com.` suffix
   (Cloudflare appends the zone automatically). So `_abc123.api.dev`.
4. Target: paste the `value`, including the trailing dot Cloudflare strips on
   save.
5. Proxy status: DNS only (gray cloud). ACM cannot resolve through the
   Cloudflare proxy.
6. Save.

Then wait. Watch the status:

```bash
AWS_PROFILE=panakoes-admin aws acm describe-certificate \
  --certificate-arn $(terraform output -raw certificate_arn) \
  --query 'Certificate.Status' --output text
```

Status flips from `PENDING_VALIDATION` to `ISSUED` in 5 to 30 minutes once the
Cloudflare record propagates.

### Phase 3: wire the custom domain and API mapping

Once the cert is `ISSUED`:

```bash
# In infra/dev/api-gateway-domain/terraform.tfvars OR pass -var inline:
AWS_PROFILE=panakoes-admin terraform apply -var='enable_domain_mapping=true'
```

This creates the `aws_apigatewayv2_domain_name` (which materializes a
`d-xxx.execute-api.us-east-1.amazonaws.com` regional endpoint) and the
`aws_apigatewayv2_api_mapping` that attaches the custom domain to the dev
stage.

Read the regional endpoint:

```bash
AWS_PROFILE=panakoes-admin terraform output regional_domain_name
# e.g. d-abc123xyz.execute-api.us-east-1.amazonaws.com
```

### Phase 4: add the user-facing CNAME to Cloudflare

Back in the Cloudflare dashboard for `panakoes.com`:

1. DNS to Records to Add record.
2. Type: `CNAME`.
3. Name: `api.dev` (Cloudflare appends `.panakoes.com`).
4. Target: the `regional_domain_name` value above
   (`d-xxx.execute-api.us-east-1.amazonaws.com`).
5. Proxy status: DNS only (gray cloud). Proxying through Cloudflare would
   intercept TLS termination, defeating the ACM cert AWS is using to sign the
   response. Revisit this when we want Cloudflare WAF + caching in front; that
   is a separate decision involving the AWS-side WAF currently attached to the
   HTTP API.
6. Save.

### Phase 5: verify

```bash
curl -sS https://api.dev.panakoes.com/v1/auth/health
# expected: 200 OK with auth service health body
```

The default invoke URL keeps working:

```bash
curl -sS https://n2un8ica69.execute-api.us-east-1.amazonaws.com/dev/v1/auth/health
# still 200; the custom domain is an alias, not a replacement
```

## Cloudflare records summary

Two DNS records live in Cloudflare for this module:

| Type  | Name                          | Target                                       | Proxy    | Purpose                |
|-------|-------------------------------|----------------------------------------------|----------|------------------------|
| CNAME | `_abc123.api.dev`             | `_def456.xxxxxxxx.acm-validations.aws`       | DNS only | ACM cert validation    |
| CNAME | `api.dev`                     | `d-xxx.execute-api.us-east-1.amazonaws.com`  | DNS only | User-facing custom URL |

The validation record can be removed once ACM has issued the cert AND every
future renewal succeeds (ACM tries to renew via the same record annually; if
the record is deleted, renewal silently fails). Recommendation: leave it in
place. The cost is one row in Cloudflare DNS.

## Follow-ups

- `services/admin/src/lib/config.ts` `VITE_API_BASE_URL` default still points
  at the execute-api invoke URL. Flip to `https://api.dev.panakoes.com` in a
  follow-up PR once Phil confirms the custom domain answers `200` from his
  browser.
- Prod environment: parallel module `infra/prod/api-gateway-domain/` with
  `custom_domain_name = "api.panakoes.com"` once prod gets bootstrapped.
- Cloudflare WAF in front of AWS WAF: defer until traffic patterns justify it;
  decide between the two layers rather than running both.

## Inputs

| Name | Description | Type | Default |
|---|---|---|---|
| `aws_region` | AWS region for the cert and domain. Must match the HTTP API region. | `string` | `"us-east-1"` |
| `environment` | Environment name for tagging. | `string` | `"dev"` |
| `project_name` | Project name for tagging. | `string` | `"panakoes"` |
| `custom_domain_name` | Public hostname fronting the dev API. | `string` | `"api.dev.panakoes.com"` |
| `enable_domain_mapping` | Gate for phase 3 resources. Flip to `true` after cert validates. | `bool` | `false` |

## Outputs

| Name | Description |
|---|---|
| `certificate_arn` | ARN of the ACM cert. |
| `certificate_status` | Cert status (`PENDING_VALIDATION` or `ISSUED`). |
| `certificate_validation_records` | List of CNAME records to add to Cloudflare for validation. |
| `custom_domain_name` | Configured custom hostname. |
| `regional_domain_name` | AWS regional target for the user-facing CNAME (empty until phase 3). |
| `regional_zone_id` | Route 53 zone ID of the regional API Gateway domain (unused while DNS is in Cloudflare). |
| `api_mapping_id` | ID of the API mapping resource (empty until phase 3). |
