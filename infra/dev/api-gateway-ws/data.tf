# Account and region context. Used to build explicit ARNs for the
# CloudWatch log group KMS condition key and the SQS integration URI.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
