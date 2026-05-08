# Global Terraform configuration

Account-wide, region-agnostic resources for Panakoes:
- GitHub Actions OIDC identity provider
- IAM role assumed by GitHub Actions workflows

## Apply

This configuration uses the S3 backend created by `../bootstrap/`. The
backend bucket, table, and KMS key must already exist before this
config can be initialized.

    cd infra/global
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

## Outputs

After apply, save the role ARN for use in GitHub Actions workflows:

    AWS_PROFILE=lafayettelabs terraform output

The role ARN goes into workflow files as the `role-to-assume` input
to the `aws-actions/configure-aws-credentials` action. Example:

    - uses: aws-actions/configure-aws-credentials@v4
      with:
        role-to-assume: arn:aws:iam::659225405128:role/panakoes-github-actions
        aws-region: us-east-1

The workflow file must also declare:

    permissions:
      id-token: write   # required to fetch the OIDC token
      contents: read

## Trust policy scope

Currently the role accepts any GitHub Actions invocation from
`Aztec03hub/panakoes` (any branch, PR, or tag). Tighten by changing
the StringLike `sub` condition to a more specific pattern, e.g.:

- `repo:Aztec03hub/panakoes:ref:refs/heads/main` (only main branch)
- `repo:Aztec03hub/panakoes:environment:production` (only when
  workflow targets a `production` GitHub environment)

## Permission scope

The role currently has `AdministratorAccess` for early dev velocity.
Before production usage, scope down to a least-privilege policy
containing only the specific actions CI needs:
- ECR push (for container builds)
- ECS / Lambda update (for deployments)
- S3 write to deployment artifact buckets
- CloudWatch Logs read (for verification)
- IAM PassRole (only the specific roles CI needs to pass to ECS/Lambda)
