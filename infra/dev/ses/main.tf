locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "ses"
  }

  name_prefix = "${var.project_name}-${var.environment}"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Domain identity (DKIM)
#
# `aws_sesv2_email_identity` with identity = the domain registers a
# DKIM-signed sender identity. AWS generates three DKIM key pairs and
# exposes the CNAME tokens via the resource's `dkim_signing_attributes`
# output; the operator then publishes the three CNAMEs at the registrar
# (Cloudflare for `lafayettelabs.com`). Verification flips to SUCCESS
# minutes after the CNAMEs propagate.
#
# Once the domain is verified, ANY `*@lafayettelabs.com` sender can be
# used as the SES `Source`, so future services (auth, billing) get
# their senders for free without re-running this module.
# ---------------------------------------------------------------------------

resource "aws_sesv2_email_identity" "domain" {
  email_identity         = var.sender_domain
  configuration_set_name = aws_sesv2_configuration_set.this.configuration_set_name

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Email identity (sandbox-mode recipient)
#
# SES sandbox mode caps sending to verified-only RECIPIENTS, not just
# verified senders. The dev environment stays in sandbox to avoid
# accidentally sending to real customers before deliverability is
# tuned, so we explicitly verify Phil's address as a permitted
# recipient for smoke tests.
#
# AWS emails a confirmation link to this address on first create; the
# operator clicks it before the identity flips to verified.
# ---------------------------------------------------------------------------

resource "aws_sesv2_email_identity" "primary_sender" {
  email_identity         = var.primary_sender_email
  configuration_set_name = aws_sesv2_configuration_set.this.configuration_set_name

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Configuration set
#
# Centralises event publishing (sends / deliveries / bounces /
# complaints / opens / clicks) for every message that names this set.
# A single configuration set per environment keeps the IAM policy and
# CloudWatch metric namespace simple. Reputation metrics enabled so
# AWS Health Dashboard surfaces bounce / complaint rate dashboards.
# ---------------------------------------------------------------------------

resource "aws_sesv2_configuration_set" "this" {
  configuration_set_name = local.name_prefix

  reputation_options {
    reputation_metrics_enabled = true
  }

  sending_options {
    sending_enabled = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Event destination: CloudWatch
#
# Publishes per-event metric counters to CloudWatch under namespace
# `AWS/SES` with the configuration-set name as a dimension. Lets us
# alarm on bounce-rate or complaint-rate crossing the SES-enforced
# thresholds (5% bounce, 0.1% complaint) before SES auto-pauses
# sending. `matching_event_types` is the SESv2 union of every
# observable event.
# ---------------------------------------------------------------------------

resource "aws_sesv2_configuration_set_event_destination" "cloudwatch" {
  configuration_set_name = aws_sesv2_configuration_set.this.configuration_set_name
  event_destination_name = "${local.name_prefix}-cloudwatch"

  event_destination {
    enabled = true

    matching_event_types = [
      "SEND",
      "REJECT",
      "BOUNCE",
      "COMPLAINT",
      "DELIVERY",
      "OPEN",
      "CLICK",
      "RENDERING_FAILURE",
      "DELIVERY_DELAY",
      "SUBSCRIPTION",
    ]

    cloud_watch_destination {
      dimension_configuration {
        dimension_name          = "ses:configuration-set"
        dimension_value_source  = "MESSAGE_TAG"
        default_dimension_value = local.name_prefix
      }
    }
  }
}

# ---------------------------------------------------------------------------
# IAM user for SMTP credential derivation
#
# SES SMTP uses an IAM user's access key (id, secret) and converts the
# pair to an SMTP username (= access key id) and SMTP password
# (= HMAC-SHA256(secret, "SendRawEmail") with the AWS version-prefix
# byte). The user has no console password; programmatic access only.
# ---------------------------------------------------------------------------

resource "aws_iam_user" "ses_smtp" {
  name = "${local.name_prefix}-ses-smtp"
  path = "/service/"

  tags = local.common_tags
}

data "aws_iam_policy_document" "ses_send" {
  statement {
    sid    = "AllowSendRawEmailScoped"
    effect = "Allow"
    actions = [
      "ses:SendRawEmail",
      "ses:SendEmail",
    ]
    resources = [
      aws_sesv2_email_identity.domain.arn,
      aws_sesv2_email_identity.primary_sender.arn,
      aws_sesv2_configuration_set.this.arn,
    ]
    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = [var.primary_sender_email]
    }
  }
}

resource "aws_iam_user_policy" "ses_send" {
  name   = "${local.name_prefix}-ses-send"
  user   = aws_iam_user.ses_smtp.name
  policy = data.aws_iam_policy_document.ses_send.json
}

resource "aws_iam_access_key" "ses_smtp" {
  user = aws_iam_user.ses_smtp.name
}
