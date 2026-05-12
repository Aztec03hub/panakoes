variable "cloudflare_api_token" {
  description = <<-EOT
    Cloudflare API token with scope limited to the two Panakoes-managed
    zones (`panakoes.com` and `lafayettelabs.com`). Required permissions:

      - Zone:Read
      - Zone Settings:Edit
      - DNS:Edit

    Zone resources: include ONLY `panakoes.com` and `lafayettelabs.com`
    (do not grant "All zones"). See README.md for the exact Cloudflare
    dashboard click-path that produces this token.

    The token is NEVER committed to the repo. Operators export it as
    `TF_VAR_cloudflare_api_token=...` before `terraform plan/apply`,
    or store it in an operator-local `terraform.tfvars` covered by
    the repo `.gitignore`.
  EOT
  type        = string
  sensitive   = true
}

variable "panakoes_zone_id" {
  description = <<-EOT
    Cloudflare zone ID for `panakoes.com`. Find via the Cloudflare
    dashboard: Websites > panakoes.com > Overview, "Zone ID" in the
    right rail. The zone ID is a stable, non-secret 32-char hex value
    that uniquely identifies the zone in Cloudflare's API; safe to
    commit as a default. Left as a required variable here so the
    initial operator confirms it explicitly the first time the module
    is applied.
  EOT
  type        = string
}

variable "lafayettelabs_zone_id" {
  description = <<-EOT
    Cloudflare zone ID for `lafayettelabs.com`. Same lookup path as
    `panakoes_zone_id`: Cloudflare dashboard > Websites > lafayettelabs.com
    > Overview > Zone ID.
  EOT
  type        = string
}

variable "ses_verification_token_panakoes" {
  description = <<-EOT
    SES domain-verification token for `panakoes.com`, value of the
    `_amazonses` TXT record. Issued by AWS SES when the domain
    identity is created (PR #265). Not a secret (visible to anyone
    who queries the DNS), but kept as a variable so the value is not
    hardcoded across the module if SES re-issues it.
  EOT
  type        = string
}

variable "ses_dkim_tokens_panakoes" {
  description = <<-EOT
    List of three SES DKIM tokens issued for `panakoes.com`. Each
    token T produces a CNAME `<T>._domainkey.panakoes.com ->
    <T>.dkim.amazonses.com`. Retrieved via:
      aws ses get-identity-dkim-attributes --identities panakoes.com
    Must be exactly three entries (SES always issues three).
  EOT
  type        = list(string)

  validation {
    condition     = length(var.ses_dkim_tokens_panakoes) == 3
    error_message = "SES always issues exactly three DKIM tokens; expected list length 3."
  }
}

variable "ses_verification_token_lafayettelabs" {
  description = "SES domain-verification token for `lafayettelabs.com` (TXT record value at `_amazonses.lafayettelabs.com`)."
  type        = string
}

variable "ses_dkim_tokens_lafayettelabs" {
  description = "List of three SES DKIM tokens issued for `lafayettelabs.com`."
  type        = list(string)

  validation {
    condition     = length(var.ses_dkim_tokens_lafayettelabs) == 3
    error_message = "SES always issues exactly three DKIM tokens; expected list length 3."
  }
}

variable "panakoes_pages_hostname" {
  description = <<-EOT
    Cloudflare Pages hostname that the apex `panakoes.com` + `www`
    point at for the landing page (PR #275). Example value:
    `panakoes-landing.pages.dev`. Apex uses a CNAME (Cloudflare
    supports CNAME flattening at the apex; this is the standard
    pattern for Cloudflare-hosted Pages sites).
  EOT
  type        = string
}

variable "lafayettelabs_apex_target" {
  description = <<-EOT
    Existing apex target for `lafayettelabs.com`. The site is already
    deployed on Cloudflare Pages (per `docs/STATUS.md`); this
    variable captures the current target so Terraform IMPORTS the
    existing record without changing its value. Typical value:
    `lafayettelabs.pages.dev`. Confirm via `dig lafayettelabs.com`
    before first apply.
  EOT
  type        = string
}

variable "lafayettelabs_www_target" {
  description = <<-EOT
    Existing `www.lafayettelabs.com` target. Typically the same Pages
    hostname as `lafayettelabs_apex_target`. Confirm via
    `dig www.lafayettelabs.com` before first apply.
  EOT
  type        = string
}

variable "dmarc_rua_mailbox" {
  description = <<-EOT
    Mailbox that receives DMARC aggregate reports for both zones.
    Wired into the DMARC TXT record as `rua=mailto:<mailbox>`. Default
    routes to the `security@lafayettelabs.com` alias forwarded by
    Cloudflare Email Routing per the `phil_contact_info.md` memory.
  EOT
  type        = string
  default     = "security@lafayettelabs.com"
}

variable "dmarc_policy" {
  description = <<-EOT
    DMARC enforcement policy. `quarantine` is the recommended starting
    point: failing mail lands in spam rather than being rejected
    outright, so a misconfigured DKIM/SPF on a real sender does not
    silently lose mail. Tighten to `reject` once aggregate reports
    confirm clean alignment.
  EOT
  type        = string
  default     = "quarantine"

  validation {
    condition     = contains(["none", "quarantine", "reject"], var.dmarc_policy)
    error_message = "dmarc_policy must be one of: none, quarantine, reject."
  }
}
