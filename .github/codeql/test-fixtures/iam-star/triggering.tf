# Fixture: triggering case for panakoes/iam-policy-resource-star
# Service-scoped IAM module granting * on resources. Should fire.
data "aws_iam_policy_document" "bad" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["*"]
  }
}
