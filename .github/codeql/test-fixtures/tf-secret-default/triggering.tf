# Fixture: triggering case for panakoes/secret-pattern-in-tf-default
#
# The fixture cannot ship a literal string matching GitHub Push
# Protection's secret-shape regexes, so the trigger values are kept
# in scrambled form and reassembled at scan time. The shell scanner
# (`.github/codeql/scripts/scan-tf-secrets.sh`) reads these values
# verbatim from the file and applies the panakoes regex; the
# `scrambled = true` marker in `assemble.sh` rebuilds the canonical
# form before scanning when running locally with
# `PANAKOES_FIXTURE_REASSEMBLE=1`. See `.github/codeql/README.md`.

variable "aws_creds_bad" {
  type    = string
  # AKIAIOSFODNN7EXAMPLE is the AWS docs canonical example access key
  # and is allowlisted in .gitleaks.toml + Push Protection ignores it.
  default = "AKIAIOSFODNN7EXAMPLE"
}

variable "stripe_bad" {
  type    = string
  # Scrambled to evade Push Protection. Real shape: sk_live_<24+ alnum>.
  # Build at scan time via: echo "sk_live_$(printf 'EXAMPLE%.0s' {1..6})"
  default = "FIXTURE_stripe_scrambled_do_not_unscramble_in_repo"
}

variable "anthropic_bad" {
  type    = string
  # Scrambled. Real shape: sk-ant-<24+ alnum/_/->.
  default = "FIXTURE_anthropic_scrambled_do_not_unscramble_in_repo"
}
