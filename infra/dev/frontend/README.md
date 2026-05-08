# Dev Environment Frontend (CloudFront + S3 Origin)

Per-environment Terraform configuration creating the static-asset
hosting tier for the Panakoes SvelteKit admin app: a private S3 origin
bucket, a CloudFront distribution named `panakoes-dev-admin`, and a
modern Origin Access Control (OAC) that signs requests from CloudFront
to S3.

## What this creates

- `aws_s3_bucket.frontend` named `panakoes-dev-frontend-<suffix>`. Holds
  the SvelteKit build output. Public-access fully blocked, versioning
  enabled, CMK-encrypted, OAC-only access via bucket policy.
- `aws_kms_key.frontend` aliased `alias/panakoes-dev-frontend`.
  Rotation enabled, 30-day deletion window. Key policy grants the
  CloudFront service principal `Decrypt` and `GenerateDataKey*` scoped
  to this account so the OAC fetch path can decrypt KMS-encrypted
  objects.
- `aws_cloudfront_origin_access_control.frontend`. Modern OAC (sigv4,
  s3 origin type, signing always). Replaces the legacy Origin Access
  Identity pattern; works with KMS-encrypted origins, no IAM user to
  rotate.
- `aws_cloudfront_distribution.admin` named `panakoes-dev-admin`.
  Default cache behavior: GET/HEAD only, redirect-to-https, compression
  on. Cache policy = AWS managed `Caching-Optimized`. Response-headers
  policy = AWS managed `Managed-SecurityHeadersPolicy`. Custom error
  responses (403 + 404) fall back to `/index.html` with status 200 so
  SvelteKit's client-side router can resolve deep links. Geo
  restriction: none. Price class: `PriceClass_100` (US/Europe). Viewer
  certificate: CloudFront default cert (custom domain + ACM cert
  deferred until DNS is wired).
- `aws_s3_bucket.frontend_logs` named
  `panakoes-dev-frontend-logs-<suffix>`. Receives CloudFront standard
  access logs. SSE-S3 (AES256) because the CloudFront standard-log
  delivery path does not support SSE-KMS on the destination bucket.
  Lifecycle expires logs at `var.log_retention_days` (default 90).
  `log-delivery-write` ACL grants the AWS Log Delivery group write
  access (the legacy mechanism CloudFront standard logs require).

## Apply

    cd infra/dev/frontend
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS and random providers, then
initializes the S3 backend (the bucket created by `infra/bootstrap/`).

`init -backend=false` is safe for offline validation:

    terraform init -backend=false
    terraform validate

## Deployment workflow (post-apply)

After `terraform apply` completes, build and ship the SvelteKit admin
app:

    cd ../../../app/admin   # adjust to wherever the SvelteKit app lives
    pnpm build
    aws s3 sync .svelte-kit/output/ s3://$(terraform -chdir=../../../infra/dev/frontend output -raw bucket_name)/ --delete
    aws cloudfront create-invalidation \
      --distribution-id $(terraform -chdir=../../../infra/dev/frontend output -raw distribution_id) \
      --paths "/*"

In short:

    pnpm build && \
    aws s3 sync .svelte-kit/output/ s3://<bucket>/ --delete && \
    aws cloudfront create-invalidation --distribution-id <id> --paths "/*"

`--delete` keeps the bucket free of stale assets from prior builds so
SvelteKit's content-hashed filenames do not accumulate. The `/*`
invalidation forces every edge cache to re-fetch on the next viewer
request; CloudFront charges per-path-pattern, so a single `/*` is
cheaper than enumerating each changed file when the deploy footprint
is small.

> **SvelteKit adapter note:** the deploy command above mirrors the
> task brief exactly. Most SvelteKit setups using `@sveltejs/adapter-static`
> place the static output in `build/`, not `.svelte-kit/output/`. If
> you flip the adapter or upgrade SvelteKit, double-check the source
> path before running the sync.

## WAF association (DEFERRED)

The brief asked us to associate `panakoes-dev-public-acl` from
`infra/dev/waf/`. That ACL is provisioned with `scope = REGIONAL` so
it can attach to ALBs and regional API Gateways; CloudFront requires
`scope = CLOUDFRONT` (a separate WAF resource that must be created in
us-east-1 regardless of where downstream services run).

This module wires the data source and a `var.associate_waf` toggle so
the wiring is ready, but the toggle defaults to `false` because
attempting to associate a regional ACL with a CloudFront distribution
fails at apply time.

The forward path:

1. Add `infra/dev/waf-cloudfront/` (the storage module's README already
   anticipates this) creating an ACL with `scope = CLOUDFRONT`.
2. Repoint this module's `data "terraform_remote_state" "waf"` block at
   the new state key (or add a second data source).
3. Set `var.associate_waf = true` and re-apply.

Until then, CloudFront serves traffic without a WAF in front. The S3
bucket policy still blocks all non-OAC reads; the security exposure is
limited to viewer-side denial-of-service and bot abuse on cached
content, which the dev environment can absorb at this stage.

## Custom domain (DEFERRED)

Viewers reach the distribution via the auto-assigned
`<id>.cloudfront.net` hostname while the dev environment matures. To
attach a real domain (for example `admin-dev.panakoes.com`):

1. Issue an ACM certificate in `us-east-1` covering the alias.
2. Add the alias to `aliases = [...]` and replace
   `cloudfront_default_certificate = true` with `acm_certificate_arn`
   plus `ssl_support_method = "sni-only"` and
   `minimum_protocol_version = "TLSv1.2_2021"`.
3. Create the DNS record (CNAME or Route53 ALIAS) pointing the alias at
   the distribution's `domain_name`.

We are deferring this until the panakoes.com DNS strategy is decided.

## Consuming outputs from other configs

```hcl
data "terraform_remote_state" "frontend" {
  backend = "s3"
  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/frontend/terraform.tfstate"
    region = "us-east-1"
  }
}

# Then reference outputs as:
#   data.terraform_remote_state.frontend.outputs.bucket_name
#   data.terraform_remote_state.frontend.outputs.distribution_id
#   data.terraform_remote_state.frontend.outputs.distribution_domain_name
#   data.terraform_remote_state.frontend.outputs.kms_key_arn
```

## Constraints worth knowing

- **OAC, not OAI.** Origin Access Identity is the legacy mechanism;
  AWS recommends OAC for all new distributions because it supports KMS
  encryption on the origin and uses sigv4 (no IAM user to rotate).
- **CloudFront log bucket cannot use SSE-KMS.** Standard logs are
  delivered by the AWS Log Delivery service which only writes to
  SSE-S3 buckets. The origin bucket is CMK-encrypted; the logs bucket
  is AES256.
- **`log-delivery-write` ACL.** The logs bucket needs the legacy
  AWS Log Delivery canned ACL grant for CloudFront standard logs to
  land. Bucket Ownership is `BucketOwnerPreferred` (not
  `BucketOwnerEnforced`) so the ACL stays effective; switching to
  Enforced would break log delivery.
- **403/404 -> /index.html.** SvelteKit's client-side router resolves
  routes that have no matching S3 object. Without the custom error
  response mapping, every deep-link refresh would 403.
- **Managed policy IDs are stable.** AWS guarantees the Caching-Optimized
  and Managed-SecurityHeadersPolicy IDs do not change. Pinning the IDs
  rather than looking them up by name avoids a cross-account drift
  risk if AWS ever introduces aliases.
- **WAF scope mismatch.** See "WAF association (DEFERRED)" above.

## Cost expectations

- CloudFront: $0.085/GB egress (first 10 TB, US/Europe). PriceClass_100
  excludes the more expensive Asia/SA POPs.
- HTTPS requests: $0.0075 per 10,000 (US/Europe). At dev volume this
  rounds to zero.
- S3 storage: pennies. The static SvelteKit bundle is sub-megabyte;
  even with versioning, monthly storage charges are noise.
- KMS CMK: $1/month flat for the dedicated frontend CMK.
- KMS request charges: bucket-key enabled on the SSE config means S3
  amortizes encryption requests at the bucket level, so KMS request
  cost stays bounded.
- Access logs: tiny; expire at 90 days.
- WAF: not yet attached, so $0 until the CloudFront-scoped ACL lands.

Total fixed monthly: ~$1 (KMS) + traffic. The distribution itself has
no flat fee.

## Outputs

| Output                       | Type   | Purpose                                                         |
|------------------------------|--------|-----------------------------------------------------------------|
| `bucket_name`                | string | Origin bucket name; deploy syncs into this                      |
| `bucket_arn`                 | string | Origin bucket ARN; for IAM policies                             |
| `distribution_id`            | string | CloudFront distribution ID; required for cache invalidations    |
| `distribution_arn`           | string | CloudFront distribution ARN                                     |
| `distribution_domain_name`   | string | `<id>.cloudfront.net` hostname viewers reach                    |
| `kms_key_arn`                | string | CMK ARN encrypting the origin bucket                            |
| `kms_key_alias`              | string | `alias/panakoes-dev-frontend`                                   |
| `logs_bucket_name`           | string | CloudFront access-log bucket name                               |
| `logs_bucket_arn`            | string | CloudFront access-log bucket ARN                                |
| `origin_access_control_id`   | string | OAC ID; reuse for additional same-account origins if added later |
