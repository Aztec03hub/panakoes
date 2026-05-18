# Account and region data sources used to construct the regional
# CloudWatch Logs service principal and to pin the key policies to the
# current AWS account (so only callers in THIS account can use the
# keys, even via the AWS service principals).
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
