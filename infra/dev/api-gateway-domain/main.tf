locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "api-gateway-domain"
  }

  api_id     = data.terraform_remote_state.api_gateway.outputs.api_id
  stage_name = data.terraform_remote_state.api_gateway.outputs.stage_name

  # Use the external cert if one is provided; otherwise fall back to the
  # module-managed `aws_acm_certificate.api` (created only when no external
  # cert is set, via the `count` below).
  cert_arn = var.external_cert_arn != "" ? var.external_cert_arn : aws_acm_certificate.api[0].arn
}

# ---------------------------------------------------------------------------
# ACM certificate for the custom domain
#
# DNS-validated (the validation method that does not require email
# delivery). ACM emits a `_xxx.<domain>` CNAME pointing at
# `_yyy.acm-validations.aws`; Phil adds that record to Cloudflare
# (DNS authority for panakoes.com). Once ACM observes the record, the
# certificate flips from PENDING_VALIDATION to ISSUED, typically in
# 5 to 30 minutes. The `lifecycle.create_before_destroy = true` block
# protects future rotations: when the cert is rotated, the new cert
# is created and validated before the old one is destroyed, avoiding
# an outage window on the API Gateway domain attachment.
# ---------------------------------------------------------------------------
resource "aws_acm_certificate" "api" {
  count = var.external_cert_arn == "" ? 1 : 0

  domain_name       = var.custom_domain_name
  validation_method = "DNS"

  tags = merge(local.common_tags, {
    Name = var.custom_domain_name
  })

  lifecycle {
    create_before_destroy = true
  }
}

# ---------------------------------------------------------------------------
# API Gateway v2 custom domain name
#
# Gated on `enable_domain_mapping` so the first apply (cert-only) does
# not fail on a PENDING_VALIDATION cert. AWS rejects
# `aws_apigatewayv2_domain_name` if the referenced certificate is not
# yet ISSUED with `BadRequestException: The certificate ... is not in
# state ISSUED`.
#
# `security_policy = "TLS_1_2"` is the only value HTTP API v2
# supports; AWS rejects any other value. `endpoint_type = "REGIONAL"`
# matches the HTTP API itself (HTTP APIs are always regional; EDGE
# domains are a REST-API-only feature).
# ---------------------------------------------------------------------------
resource "aws_apigatewayv2_domain_name" "api" {
  count = var.enable_domain_mapping ? 1 : 0

  domain_name = var.custom_domain_name

  domain_name_configuration {
    certificate_arn = local.cert_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }

  tags = merge(local.common_tags, {
    Name = var.custom_domain_name
  })
}

# ---------------------------------------------------------------------------
# API Gateway v2 mapping
#
# Connects the custom domain to the existing HTTP API at the existing
# stage. With no `api_mapping_key`, requests to the apex of the custom
# domain (`https://api.dev.panakoes.com/<path>`) route to the stage's
# routes (`/v1/<service>/<...>`). Adding a mapping key here would
# require the client to call `https://api.dev.panakoes.com/<key>/...`,
# which is NOT what we want; the default invoke URL of the underlying
# stage already includes `/dev/`, but the mapping strips the stage
# prefix on the custom-domain edge.
# ---------------------------------------------------------------------------
resource "aws_apigatewayv2_api_mapping" "api" {
  count = var.enable_domain_mapping ? 1 : 0

  api_id      = local.api_id
  domain_name = aws_apigatewayv2_domain_name.api[0].id
  stage       = local.stage_name
}
