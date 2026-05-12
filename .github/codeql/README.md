# Panakoes Custom CodeQL Queries

This directory holds project-specific CodeQL queries that catch Panakoes patterns the generic `security-and-quality` suite misses. Native CodeQL queries live under `queries/panakoes/`; companion shell scanners that cover languages CodeQL has no first-class extractor for (Terraform HCL, raw Markdown, TS env-var reads) live under `scripts/`.

## What ships

| File | Catches | Languages | Report mode |
|---|---|---|---|
| `queries/panakoes/jwt-env-var-prefix-mismatch.ql` | `AUTH_JWT_*` env-var read outside `services/auth/` (validators must use `JWT_*`; signers use `AUTH_JWT_*`, per ADR-039 / ADR-041) | Python | report-only |
| `queries/panakoes/iam-policy-resource-star.ql` (+ `scripts/scan-iam-star.sh`) | `aws_iam_policy_document` statement with `resources = ["*"]` outside the allowlist (`infra/dev/iam/main.tf`) | Terraform | report-only |
| `queries/panakoes/em-dash-in-source.ql` (+ `scripts/scan-em-dash.sh`) | U+2014 em-dash or U+2013 en-dash in committed source. Catches the gap the bash detector misses for files added via `cat <<EOF` patterns | Python (native) + all text files (script) | report-only |
| `queries/panakoes/secret-pattern-in-tf-default.ql` (+ `scripts/scan-tf-secrets.sh`) | Terraform `variable` `default = "..."` matching AWS access-key (`AKIA[A-Z0-9]{16}`), Stripe key (`sk_(live\|test)_*`), or Anthropic key (`sk-ant-*`) pattern | Terraform | report-only |

All four queries are **report-only** today: they surface findings to the GitHub Security tab and SARIF artifacts but do NOT fail the build. The first PR enabling them as required status checks should follow after the existing findings (see "Existing findings on main", below) are triaged and either fixed or explicitly accepted via a SARIF baseline.

## Why some queries are shell scripts

CodeQL v4 does not ship a first-class HCL extractor; querying `.tf` files via the YAML / generic-text fallback is fragile and produces noisy results. Rather than build a custom extractor (~3 days of work), the IAM-star and TF-secret queries are paired with `bash + grep` scanners under `scripts/`. Each scanner emits SARIF 2.1.0, which the CodeQL workflow uploads via `github/codeql-action/upload-sarif@v4`. The end-user experience (findings show up in the Security tab) is identical.

The em-dash query has a Python-native form (covers `services/*.py` literals) and a shell scanner that walks every text file under `git ls-files`.

## Local test commands

CodeQL has a built-in test runner. With the `codeql` CLI installed (https://github.com/github/codeql-cli-binaries):

```bash
# Run the native .ql queries against their fixtures
codeql test run .github/codeql/queries/panakoes/

# Run the shell scanners against the working tree
bash .github/codeql/scripts/scan-iam-star.sh    /tmp/iam.sarif
bash .github/codeql/scripts/scan-tf-secrets.sh  /tmp/tf.sarif
bash .github/codeql/scripts/scan-em-dash.sh     /tmp/em.sarif
bash .github/codeql/scripts/scan-jwt-prefix.sh  /tmp/jwt.sarif

# Inspect the SARIF output
jq '.runs[].results | length' /tmp/iam.sarif
```

The `.expected` files next to each `.ql` document the rows the native query should produce against the fixtures in `test-fixtures/`.

### Em-dash fixture caveat

The em-dash fixtures (`test-fixtures/em-dash/triggering.{py,md}`) cannot ship literal U+2014 / U+2013 characters because the repo's pre-commit em-dash detector (`scripts/check_no_em_dashes.sh`) rejects any staged file containing them. The fixtures use marker substitution; the local test driver materializes the live characters before scanning:

```bash
# Derive runtime fixtures with literal em / en dashes
python -c "import pathlib; p=pathlib.Path('.github/codeql/test-fixtures/em-dash/triggering.md'); p.with_suffix('.runtime.md').write_text(p.read_text().replace('<<EM>>', chr(0x2014)).replace('<<EN>>', chr(0x2013)))"

# Then run the scanner against the working tree (it will see the .runtime.md)
bash .github/codeql/scripts/scan-em-dash.sh /tmp/em.sarif
```

`.runtime.*` files are git-ignored implicitly (they are never staged); CI runs scanners against the real tree so the fixtures themselves never trigger on PRs.

## Existing findings on main (2026-05-11)

Run the scanners against current `main` to seed the baseline. As of the PR that introduces this directory:

- **`scan-iam-star.sh`**: 8 files outside the allowlist contain `resources = ["*"]` (api-gateway, cost-rollup-aggregator, frontend, observability, security, step-functions, transcribe-worker, waf). Most are AWS-API-required wildcards (e.g., `kms:Decrypt` with condition keys, `logs:DescribeLogGroups`); each needs a per-statement audit + a justifying comment OR a tightened ARN. **Phil review needed.**
- **`scan-jwt-prefix.sh`**: 16 hits across 6 files in `services/ingestion-api/` and `services/session-manager/`. These are documented validators that share `AUTH_JWT_SECRET` with the signer (HS256 shared-secret model, pre-ADR-041 architecture). If ADR-041 (KMS-signed JWTs) lands, these all flip to `JWT_VERIFY_KEY` reads. **Phil review needed; tracks the migration from HS256 to KMS-signed.**
- **`scan-em-dash.sh`**: 0 hits (the bash em-dash detector is doing its job).
- **`scan-tf-secrets.sh`**: 0 hits.

## Adding a new query

1. Drop the `.ql` under `queries/panakoes/<name>.ql` with full QLDoc frontmatter (name, description, kind, severity, id, tags).
2. Add a triggering + non-triggering fixture under `test-fixtures/<name>/`.
3. Add a `.expected` file describing the rows the query should produce.
4. If the target language lacks a CodeQL extractor (HCL, plain text, JSON, etc.), add a companion `scripts/scan-<name>.sh` that emits SARIF. Wire it into `.github/workflows/codeql.yml`.
5. Update the table above.
6. Default to **report-only** until the existing findings on `main` are triaged; then promote to a required status check in a separate PR.

## Why we keep this small

Custom queries are easy to write and hard to maintain. Every new query is one more thing that can throw, false-positive, or drift from intent. The bar to add a new one is: this catches a bug class that has already bitten us in a real PR, OR an interview-defensible threat model says it will. Otherwise stick with `security-and-quality`.
