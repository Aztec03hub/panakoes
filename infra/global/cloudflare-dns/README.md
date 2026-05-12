# infra/global/cloudflare-dns

Terraform-managed DNS records on the two Cloudflare zones owned by
LaFayette Labs:

- `panakoes.com` (project)
- `lafayettelabs.com` (LLC)

Both zones were registered at Cloudflare on 2026-05-07. Until this
module was authored, every DNS record was added manually via the
Cloudflare dashboard. This module captures the current state in
Terraform and becomes the source of truth for all future DNS changes
on either zone.

## Why this exists

- **Drift control.** Manual DNS edits in the Cloudflare dashboard
  drift away from documented intent. A Terraform module lets PRs
  carry the diff and CI replay the plan.
- **Auditability.** Every record carries a `comment` attribute that
  cites the PR that introduced it; the git history shows who changed
  what and when.
- **Disaster recovery.** If a zone is accidentally cleared or
  re-created, `terraform apply` recreates every record with the
  documented values, byte-for-byte.

## Cloudflare API token

The module reads its API token from `var.cloudflare_api_token`,
populated via the `TF_VAR_cloudflare_api_token` environment variable
on the operator's local machine. The token MUST NOT be committed to
the repository; an operator-local `terraform.tfvars` is acceptable
ONLY if it is covered by `.gitignore` (the repo `.gitignore` already
excludes `*.tfvars` patterns common to terraform conventions; verify
before saving any file).

### Token scope (create in Cloudflare dashboard)

1. Sign in to Cloudflare.
2. Top-right profile menu > My Profile > API Tokens > Create Token.
3. Choose "Create Custom Token".
4. Token name: `panakoes-tf-dns`.
5. Permissions:
   - **Zone | Zone | Read**
   - **Zone | Zone Settings | Edit**
   - **Zone | DNS | Edit**
6. Zone Resources:
   - Include | Specific zone | `panakoes.com`
   - Include | Specific zone | `lafayettelabs.com`
7. (Optional but recommended) Client IP Address Filtering: restrict
   to your home/dev IP for an extra defense-in-depth layer.
8. (Optional) TTL: set a 1-year expiry and put a calendar reminder
   to rotate.

Why these scopes (not "All zones, Edit"):

- **DNS:Edit** is the only write capability the module needs.
- **Zone:Read** lets the provider list the zones to translate names
  into zone IDs (the provider auto-resolves when needed).
- **Zone Settings:Edit** is required because Terraform's
  `cloudflare_record` resource occasionally needs to touch zone-level
  settings when reconciling proxy state.
- Restricting to two specific zones means a leaked token cannot
  touch any other Cloudflare resource on the account (Workers, R2,
  Pages config, other zones, billing, account settings).

Interview talk-track: this mirrors the AWS IAM least-privilege
pattern (explicit Resource ARN list + minimal action list) applied
to a third-party SaaS API.

## Zone IDs

The zone IDs are required as variables because the provider needs
them in `cloudflare_record` resources. Find them via:

- Cloudflare dashboard > Websites > `panakoes.com` > Overview, "Zone
  ID" in the right rail.
- Same path for `lafayettelabs.com`.

Zone IDs are 32-char lowercase hex values. They are not secrets and
are safe to commit (e.g., to an operator-local `terraform.tfvars`
that does ship in git) or to bake into a `tfvars` example committed
under a different name. The repo intentionally does not pin them here
to force the first operator to confirm them explicitly.

## SES inputs

This module assumes the SES domain identities for both zones have
already been provisioned (PR #265). To populate the DKIM token lists:

```bash
aws ses get-identity-dkim-attributes \
  --identities panakoes.com lafayettelabs.com \
  --profile panakoes-admin \
  --output json
```

The output gives three `DkimTokens` per identity. Capture them and
feed into `ses_dkim_tokens_panakoes` / `ses_dkim_tokens_lafayettelabs`.

The SES verification token (the `_amazonses` TXT value) is captured
from the SES Console's "Domain identity details" page, or via:

```bash
aws ses get-identity-verification-attributes \
  --identities panakoes.com lafayettelabs.com \
  --profile panakoes-admin
```

The `VerificationToken` field is the value to feed into
`ses_verification_token_panakoes` / `ses_verification_token_lafayettelabs`.

## Record inventory

### `panakoes.com` (12 records)

| Resource | Type | Name | Notes |
|---|---|---|---|
| `panakoes_ses_verification` | TXT | `_amazonses` | SES domain verification (PR #265) |
| `panakoes_ses_dkim` (x3) | CNAME | `<token>._domainkey` | SES DKIM (PR #265) |
| `panakoes_apex` | CNAME | `@` | Cloudflare Pages landing (PR #275) |
| `panakoes_www` | CNAME | `www` | Cloudflare Pages landing (PR #275) |
| `panakoes_dmarc` | TXT | `_dmarc` | DMARC policy |
| `panakoes_spf` | TXT | `@` | SPF (Cloudflare Email Routing + SES) |
| `panakoes_mx` (x3) | MX | `@` | Cloudflare Email Routing |

### `lafayettelabs.com` (12 records)

| Resource | Type | Name | Notes |
|---|---|---|---|
| `lafayettelabs_apex` | CNAME | `@` | Cloudflare Pages site (existing, imported) |
| `lafayettelabs_www` | CNAME | `www` | Cloudflare Pages site (existing, imported) |
| `lafayettelabs_ses_verification` | TXT | `_amazonses` | SES domain verification (PR #265, imported) |
| `lafayettelabs_ses_dkim` (x3) | CNAME | `<token>._domainkey` | SES DKIM (PR #265, imported) |
| `lafayettelabs_dmarc` | TXT | `_dmarc` | DMARC policy |
| `lafayettelabs_spf` | TXT | `@` | SPF (Cloudflare Email Routing + SES) |
| `lafayettelabs_mx` (x3) | MX | `@` | Cloudflare Email Routing |

Note that the API Gateway custom-domain CNAMEs from PR #264 are NOT
in this module yet. They are pending Phil adding them by hand; once
they land they should be added here in a follow-up PR and IMPORTed
the same way the SES records are.

## First-apply procedure (Phil runs this post-merge)

### 1. Init + collect inputs

```bash
cd infra/global/cloudflare-dns

export TF_VAR_cloudflare_api_token="<token from the section above>"

# Find zone IDs (Cloudflare dashboard).
export TF_VAR_panakoes_zone_id="<32-hex-chars>"
export TF_VAR_lafayettelabs_zone_id="<32-hex-chars>"

# SES tokens.
export TF_VAR_ses_verification_token_panakoes="<from aws ses get-identity-verification-attributes>"
export TF_VAR_ses_verification_token_lafayettelabs="<from aws ses get-identity-verification-attributes>"

# SES DKIM (three tokens each).
export TF_VAR_ses_dkim_tokens_panakoes='["t1","t2","t3"]'
export TF_VAR_ses_dkim_tokens_lafayettelabs='["t1","t2","t3"]'

# Pages targets.
export TF_VAR_panakoes_pages_hostname="panakoes-landing.pages.dev"   # confirm exact value from PR #275
export TF_VAR_lafayettelabs_apex_target="lafayettelabs.pages.dev"    # confirm via dig
export TF_VAR_lafayettelabs_www_target="lafayettelabs.pages.dev"     # confirm via dig

terraform init
terraform validate
```

### 2. Import existing records

Cloudflare record IDs are 32-char lowercase hex values. Find each one
via the Cloudflare API:

```bash
ZONE_ID="$TF_VAR_panakoes_zone_id"   # or lafayettelabs zone id

curl -sS -H "Authorization: Bearer $TF_VAR_cloudflare_api_token" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?per_page=100" \
  | jq -r '.result[] | [.id, .type, .name, .content] | @tsv'
```

This prints every record in the zone, one per line:
`<record_id>  <TYPE>  <name>  <content>`. Map each to a Terraform
resource and run import:

```bash
# Pattern: terraform import <resource_address> <zone_id>/<record_id>

# panakoes.com zone - replace <RECORD_ID> placeholders with values from the curl above.
terraform import cloudflare_record.panakoes_ses_verification               "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.panakoes_ses_dkim["<TOKEN_1>"]'        "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.panakoes_ses_dkim["<TOKEN_2>"]'        "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.panakoes_ses_dkim["<TOKEN_3>"]'        "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import cloudflare_record.panakoes_apex                           "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import cloudflare_record.panakoes_www                            "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import cloudflare_record.panakoes_dmarc                          "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import cloudflare_record.panakoes_spf                            "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.panakoes_mx["route1.mx.cloudflare.net"]' "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.panakoes_mx["route2.mx.cloudflare.net"]' "$TF_VAR_panakoes_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.panakoes_mx["route3.mx.cloudflare.net"]' "$TF_VAR_panakoes_zone_id/<RECORD_ID>"

# lafayettelabs.com zone
terraform import cloudflare_record.lafayettelabs_apex                           "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import cloudflare_record.lafayettelabs_www                            "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import cloudflare_record.lafayettelabs_ses_verification               "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.lafayettelabs_ses_dkim["<TOKEN_1>"]'        "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.lafayettelabs_ses_dkim["<TOKEN_2>"]'        "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.lafayettelabs_ses_dkim["<TOKEN_3>"]'        "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import cloudflare_record.lafayettelabs_dmarc                          "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import cloudflare_record.lafayettelabs_spf                            "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.lafayettelabs_mx["route1.mx.cloudflare.net"]' "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.lafayettelabs_mx["route2.mx.cloudflare.net"]' "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
terraform import 'cloudflare_record.lafayettelabs_mx["route3.mx.cloudflare.net"]' "$TF_VAR_lafayettelabs_zone_id/<RECORD_ID>"
```

If a record from the resource list does NOT already exist in
Cloudflare (typical for DMARC/SPF on fresh zones), skip the import
for that resource. Terraform will create it on the first apply.

### 3. Plan + reconcile

```bash
terraform plan -out=tfplan
```

Expected outcome on a clean import:

- Zero changes for every record that was imported.
- Creates ONLY for records that did not previously exist (DMARC, SPF,
  any record not present in the dashboard yet).

If `terraform plan` shows updates to imported records, STOP. The
import either captured a record whose value drifted from what this
module declares (rare: SES tokens), or the variable value passed in
does not match what is in Cloudflare. Either revise the variable to
match Cloudflare's current value, or accept the change as
intentional and document why.

### 4. Apply

```bash
terraform apply tfplan
rm tfplan
```

### 5. Verify

```bash
dig panakoes.com +short
dig _dmarc.panakoes.com TXT +short
dig _amazonses.panakoes.com TXT +short
dig lafayettelabs.com +short
dig _dmarc.lafayettelabs.com TXT +short
```

Every query should return the values declared in this module.

## Future records (out of scope for this PR)

- **API Gateway custom domain CNAMEs (PR #264)**: pending Phil adding
  the records manually. Once they are live, add the resources to this
  module and IMPORT them the same way the SES records are imported.
- **admin.panakoes.com CNAME to CloudFront**: pending `infra/dev/frontend`
  being applied (Section F.2 of `docs/operator/aws-cloudflare-actions.md`).

Both follow-ups should add the new resource block, run the same
`terraform import` ritual, and confirm `terraform plan` is empty
before merging.

## Rollback

Cloudflare DNS rollback options, in order of preference:

1. **`git revert` the offending commit.** `terraform plan` will show
   the inverse changes; `terraform apply` re-aligns Cloudflare with
   the prior declared state.
2. **`terraform state rm`** a resource to drop it from Terraform
   management without deleting the record in Cloudflare (use when an
   import captured a record that should NOT be Terraform-managed).
3. **Manual hotfix in the Cloudflare dashboard** is acceptable for
   emergencies but creates drift; follow up immediately with a PR
   that brings the dashboard change back into this module.

Do NOT use `terraform destroy` here. It would tear down every DNS
record in both zones and effectively black-hole every Panakoes /
LaFayette Labs domain.
