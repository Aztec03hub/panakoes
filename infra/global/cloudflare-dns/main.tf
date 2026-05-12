# Cloudflare DNS records for `panakoes.com` and `lafayettelabs.com`.
#
# Goal of this module: capture the current state of manually-managed
# Cloudflare DNS in Terraform so changes go through PRs and the source
# of truth lives in version control. Existing records (already added
# in the dashboard) are brought under management via `terraform import`
# (see README.md). New records added here MUST match the existing
# values byte-for-byte; the import is non-destructive only if plan
# returns zero diffs after the import.
#
# All records use `proxied = false` (DNS-only). The application
# endpoints (CloudFront, API Gateway, SES, Cloudflare Pages) handle
# their own TLS termination + caching; layering Cloudflare's proxy on
# top would double-cache and break some headers. The Cloudflare Pages
# apex/www records are the one exception where proxy could be enabled
# in a follow-up if we want Cloudflare features in front of Pages,
# but Pages already runs on Cloudflare's edge so the value is small.
#
# Cloudflare Email Routing MX records are managed here because
# Cloudflare's Email Routing UI auto-provisions them; bringing them
# into Terraform makes the dependency explicit and prevents drift if
# the routing config is ever toggled off and back on. The MX target
# hostnames are Cloudflare's published, stable Email Routing targets.

locals {
  cloudflare_email_routing_mx = [
    { priority = 10, target = "route1.mx.cloudflare.net" },
    { priority = 20, target = "route2.mx.cloudflare.net" },
    { priority = 50, target = "route3.mx.cloudflare.net" },
  ]

  # SPF that authorizes Cloudflare Email Routing (forwarder) + Amazon
  # SES (transactional senders). Anything else fails SPF and, under
  # the DMARC quarantine policy below, lands in spam.
  spf_value = "v=spf1 include:_spf.mx.cloudflare.net include:amazonses.com ~all"

  dmarc_value = "v=DMARC1; p=${var.dmarc_policy}; rua=mailto:${var.dmarc_rua_mailbox}; ruf=mailto:${var.dmarc_rua_mailbox}; fo=1; adkim=s; aspf=s"
}

########################################
# panakoes.com zone
########################################

# SES domain-identity verification token. Issued by AWS when the
# `panakoes.com` SES identity was created in PR #265. Type TXT,
# host `_amazonses.panakoes.com`.
resource "cloudflare_record" "panakoes_ses_verification" {
  zone_id = var.panakoes_zone_id
  name    = "_amazonses"
  type    = "TXT"
  content = var.ses_verification_token_panakoes
  ttl     = 1 # 1 = "automatic"; Cloudflare picks based on proxy state.
  proxied = false
  comment = "SES domain-identity verification token (PR #265). Terraform-managed."
}

# SES DKIM CNAMEs. SES always issues exactly three. Each token T
# produces `<T>._domainkey.panakoes.com -> <T>.dkim.amazonses.com`.
resource "cloudflare_record" "panakoes_ses_dkim" {
  for_each = toset(var.ses_dkim_tokens_panakoes)

  zone_id = var.panakoes_zone_id
  name    = "${each.value}._domainkey"
  type    = "CNAME"
  content = "${each.value}.dkim.amazonses.com"
  ttl     = 1
  proxied = false
  comment = "SES DKIM CNAME (PR #265). Terraform-managed."
}

# Apex landing page (Cloudflare Pages). Cloudflare auto-flattens CNAME
# at the apex; using a CNAME here is the standard pattern for a
# Cloudflare-hosted Pages site.
resource "cloudflare_record" "panakoes_apex" {
  zone_id = var.panakoes_zone_id
  name    = "@"
  type    = "CNAME"
  content = var.panakoes_pages_hostname
  ttl     = 1
  proxied = true
  comment = "Apex CNAME to Cloudflare Pages landing site (PR #275). Terraform-managed."
}

resource "cloudflare_record" "panakoes_www" {
  zone_id = var.panakoes_zone_id
  name    = "www"
  type    = "CNAME"
  content = var.panakoes_pages_hostname
  ttl     = 1
  proxied = true
  comment = "www CNAME to Cloudflare Pages landing site (PR #275). Terraform-managed."
}

resource "cloudflare_record" "panakoes_dmarc" {
  zone_id = var.panakoes_zone_id
  name    = "_dmarc"
  type    = "TXT"
  content = local.dmarc_value
  ttl     = 1
  proxied = false
  comment = "DMARC policy. Terraform-managed."
}

resource "cloudflare_record" "panakoes_spf" {
  zone_id = var.panakoes_zone_id
  name    = "@"
  type    = "TXT"
  content = local.spf_value
  ttl     = 1
  proxied = false
  comment = "SPF for Cloudflare Email Routing + SES. Terraform-managed."
}

# Cloudflare Email Routing MX records. Three targets at three
# different priorities, per Cloudflare's published Email Routing
# documentation. Backs the `security@`, `conduct@`, `noreply@`
# aliases per the `phil_contact_info.md` memory.
resource "cloudflare_record" "panakoes_mx" {
  for_each = { for r in local.cloudflare_email_routing_mx : r.target => r }

  zone_id  = var.panakoes_zone_id
  name     = "@"
  type     = "MX"
  content  = each.value.target
  priority = each.value.priority
  ttl      = 1
  proxied  = false
  comment  = "Cloudflare Email Routing MX. Terraform-managed."
}

########################################
# lafayettelabs.com zone
########################################

# Apex + www: the LaFayette Labs site is already live on Cloudflare
# Pages (per `docs/STATUS.md`). These records IMPORT existing values;
# do not change defaults without confirming the apply plan is empty.
resource "cloudflare_record" "lafayettelabs_apex" {
  zone_id = var.lafayettelabs_zone_id
  name    = "@"
  type    = "CNAME"
  content = var.lafayettelabs_apex_target
  ttl     = 1
  proxied = true
  comment = "Apex CNAME to Cloudflare Pages LL site. Terraform-managed (imported)."
}

resource "cloudflare_record" "lafayettelabs_www" {
  zone_id = var.lafayettelabs_zone_id
  name    = "www"
  type    = "CNAME"
  content = var.lafayettelabs_www_target
  ttl     = 1
  proxied = true
  comment = "www CNAME to Cloudflare Pages LL site. Terraform-managed (imported)."
}

resource "cloudflare_record" "lafayettelabs_ses_verification" {
  zone_id = var.lafayettelabs_zone_id
  name    = "_amazonses"
  type    = "TXT"
  content = var.ses_verification_token_lafayettelabs
  ttl     = 1
  proxied = false
  comment = "SES domain-identity verification token (PR #265). Terraform-managed (imported)."
}

resource "cloudflare_record" "lafayettelabs_ses_dkim" {
  for_each = toset(var.ses_dkim_tokens_lafayettelabs)

  zone_id = var.lafayettelabs_zone_id
  name    = "${each.value}._domainkey"
  type    = "CNAME"
  content = "${each.value}.dkim.amazonses.com"
  ttl     = 1
  proxied = false
  comment = "SES DKIM CNAME (PR #265). Terraform-managed (imported)."
}

resource "cloudflare_record" "lafayettelabs_dmarc" {
  zone_id = var.lafayettelabs_zone_id
  name    = "_dmarc"
  type    = "TXT"
  content = local.dmarc_value
  ttl     = 1
  proxied = false
  comment = "DMARC policy. Terraform-managed."
}

resource "cloudflare_record" "lafayettelabs_spf" {
  zone_id = var.lafayettelabs_zone_id
  name    = "@"
  type    = "TXT"
  content = local.spf_value
  ttl     = 1
  proxied = false
  comment = "SPF for Cloudflare Email Routing + SES. Terraform-managed."
}

resource "cloudflare_record" "lafayettelabs_mx" {
  for_each = { for r in local.cloudflare_email_routing_mx : r.target => r }

  zone_id  = var.lafayettelabs_zone_id
  name     = "@"
  type     = "MX"
  content  = each.value.target
  priority = each.value.priority
  ttl      = 1
  proxied  = false
  comment  = "Cloudflare Email Routing MX. Terraform-managed."
}
