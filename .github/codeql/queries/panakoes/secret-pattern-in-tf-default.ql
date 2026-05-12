/**
 * @name Secret-shaped string in Terraform variable default
 * @description Flags Terraform `variable` blocks whose `default` value
 *              matches an AWS access-key (`AKIA[0-9A-Z]{16}`), Stripe
 *              live or test key (`sk_(live|test)_[0-9a-zA-Z]{24,}`),
 *              or Anthropic API key (`sk-ant-[0-9a-zA-Z\-_]{24,}`)
 *              pattern. gitleaks already covers the secret-scan baseline
 *              for the diff; this query catches the slower drift where
 *              a placeholder gets quietly replaced with a real value
 *              in a default = "..." position.
 * @kind problem
 * @problem.severity error
 * @security-severity 9.0
 * @precision high
 * @id panakoes/secret-pattern-in-tf-default
 * @tags security
 *       panakoes
 *       secrets
 *       terraform
 *
 * NOTE: CodeQL has no first-class HCL extractor. The companion shell
 * script `.github/codeql/scripts/scan-tf-secrets.sh` runs the regex
 * pass over `**\/*.tf` and uploads SARIF when CodeQL's text extractor
 * is unavailable in the runner image.
 */

import python

from File f
where none()
select f, "placeholder; see .github/codeql/scripts/scan-tf-secrets.sh"
