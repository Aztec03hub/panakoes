# Fixture: non-triggering case. Resource ARNs are scoped.
data "aws_iam_policy_document" "good" {
  statement {
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::panakoes-dev-uploads/*",
    ]
  }
}
