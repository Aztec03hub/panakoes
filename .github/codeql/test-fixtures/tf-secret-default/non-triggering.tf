# Fixture: non-triggering case. Defaults are placeholders, env-sourced
# at apply time via TF_VAR_*; no real key shapes present.

variable "aws_creds_ok" {
  type    = string
  default = "set-via-tf-var-at-apply-time"
}

variable "stripe_ok" {
  type      = string
  sensitive = true
  default   = null
}
