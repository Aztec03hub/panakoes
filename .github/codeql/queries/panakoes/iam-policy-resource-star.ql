/**
 * @name Terraform IAM policy with resources = ["*"] outside allowlist
 * @description Flags `aws_iam_policy_document` statements whose `resources`
 *              attribute is the literal `["*"]` in any Terraform file
 *              that is NOT on the documented allowlist (currently:
 *              `infra/dev/iam/main.tf`, where it is required for the
 *              account-scoped `logs:CreateLogGroup` bootstrap). Catches
 *              over-grants in service-scoped IAM modules.
 * @kind problem
 * @problem.severity warning
 * @security-severity 6.0
 * @precision high
 * @id panakoes/iam-policy-resource-star
 * @tags security
 *       panakoes
 *       iam
 *       least-privilege
 *
 * NOTE: CodeQL has no first-class Terraform/HCL extractor. This query
 * runs as a regex pass over committed `.tf` files via the YAML/text
 * fallback extractor. Findings are advisory and require human review.
 * The file-walk and pattern-match are documented in the companion
 * scanner script `.github/codeql/scripts/scan-iam-star.sh` for the
 * cases CodeQL's text extractor does not cover.
 */

// This query is a placeholder that documents intent; the regex scan
// lives in the companion shell script invoked from the CodeQL workflow
// (text-extractor language pack required for native execution).
// Selecting nothing here keeps the query parseable; the scan runs out
// of band and uploads SARIF separately when the text extractor is not
// available in the runner image.

import python

from File f
where none()
select f, "placeholder; see .github/codeql/scripts/scan-iam-star.sh"
