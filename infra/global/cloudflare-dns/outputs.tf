output "zone_ids" {
  description = "Map of zone-name to Cloudflare zone ID for the zones managed by this module."
  value = {
    "panakoes.com"      = var.panakoes_zone_id
    "lafayettelabs.com" = var.lafayettelabs_zone_id
  }
}

output "record_ids" {
  description = <<-EOT
    Map of record-resource-name to Cloudflare record ID for every
    record this module manages. Useful for cross-module references
    and for confirming the import set after the first apply.
  EOT
  value = {
    # panakoes.com
    "panakoes_ses_verification" = cloudflare_record.panakoes_ses_verification.id
    "panakoes_ses_dkim"         = { for k, r in cloudflare_record.panakoes_ses_dkim : k => r.id }
    "panakoes_apex"             = cloudflare_record.panakoes_apex.id
    "panakoes_www"              = cloudflare_record.panakoes_www.id
    "panakoes_dmarc"            = cloudflare_record.panakoes_dmarc.id
    "panakoes_spf"              = cloudflare_record.panakoes_spf.id
    "panakoes_mx"               = { for k, r in cloudflare_record.panakoes_mx : k => r.id }

    # lafayettelabs.com
    "lafayettelabs_apex"             = cloudflare_record.lafayettelabs_apex.id
    "lafayettelabs_www"              = cloudflare_record.lafayettelabs_www.id
    "lafayettelabs_ses_verification" = cloudflare_record.lafayettelabs_ses_verification.id
    "lafayettelabs_ses_dkim"         = { for k, r in cloudflare_record.lafayettelabs_ses_dkim : k => r.id }
    "lafayettelabs_dmarc"            = cloudflare_record.lafayettelabs_dmarc.id
    "lafayettelabs_spf"              = cloudflare_record.lafayettelabs_spf.id
    "lafayettelabs_mx"               = { for k, r in cloudflare_record.lafayettelabs_mx : k => r.id }
  }
}
