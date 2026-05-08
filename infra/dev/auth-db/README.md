# Dev Environment Auth Database (Aurora Serverless v2 Postgres)

Per-environment Terraform configuration creating the Aurora Serverless
v2 PostgreSQL 16 cluster that backs the Better-Auth tables (`user`,
`session`, `account`, `verification`) for the auth microservice in the
`dev` environment. Storage and Performance Insights are encrypted with
a dedicated customer-managed KMS key; the cluster lives in private
subnets only and is reachable on 5432/TCP from inside the dev VPC.

## What this creates

| Resource                                          | Name / Identifier                                |
|---------------------------------------------------|--------------------------------------------------|
| Aurora Serverless v2 cluster (Postgres 16.4)      | `panakoes-dev-auth-<random suffix>`              |
| Cluster writer instance (`db.serverless`)         | `panakoes-dev-auth-writer`                       |
| DB subnet group (3 AZs, private subnets)          | `panakoes-dev-auth-db`                           |
| Cluster security group (inbound 5432 from VPC)    | `panakoes-dev-auth-db-sg`                        |
| KMS CMK for storage + Performance Insights        | `alias/panakoes-dev-auth-db`                     |
| Enhanced Monitoring IAM role                      | `panakoes-dev-auth-db-enhanced-monitoring`       |

Cluster sizing:

- `min_capacity` = 0.5 ACU (Aurora's idle floor)
- `max_capacity` = 4 ACU (cap the dev blast radius on a runaway query)
- Backup retention = 7 days
- Deletion protection = on
- `skip_final_snapshot` = true (dev only; flip to false in prod)

The KMS CMK has rotation enabled (annual, AWS-managed) and a 7-day
deletion window matching `dev/secrets/`. The shorter window is a
deliberate dev-tier choice: dev material is replaceable in minutes and
a 30-day undelete blocks reuse of the alias.

## Why Aurora Serverless v2 over plain RDS Postgres

Three decisions, each independently defensible:

1. **Aurora over RDS Postgres**: Better-Auth's `session` table is a
   high-frequency read path. Aurora's storage architecture
   (separate-storage-and-compute, 6-way replicated across 3 AZs) makes
   the production failover semantics qualitatively different from RDS
   single-AZ + replica. We want the dev cluster to behave the way the
   prod cluster will, not differently.
2. **Serverless v2 over fixed instance class**: dev traffic is bursty.
   At 0.5 ACU idle, Serverless v2 runs around $0.06/hr. The smallest
   non-Serverless Aurora class (`db.t4g.medium`) runs around $0.10/hr
   regardless of load. Serverless v2 wins on cost during the
   long stretches between integration test sweeps and scales up
   automatically when CI hammers the cluster.
3. **Postgres 16 over MySQL or earlier Postgres major**: Better-Auth
   ships SQL migrations targeting Postgres; 16 is the latest major
   Aurora supports as of this module. 16.4 specifically pins us to
   the latest LTS-aligned minor at module-authoring time.

## Apply (NOT YET, plan-only)

This module is committed and `terraform init -backend=false` /
`terraform validate` / `terraform fmt` clean. It is **not yet
applied** to AWS. Apply ordering blocks on the secrets module landing
first (tracked: `infra/dev/secrets/` is committed but not applied per
its own README), so the cluster reads the master password out of
Secrets Manager rather than the plan-only `random_password` fallback.

When the time comes:

    cd infra/dev/auth-db
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS and random providers and
initializes the S3 backend (the bucket created by `infra/bootstrap/`).
The lock file shipped with this module pins exact provider hashes for
reproducible init.

## Post-apply: rotate the master password and hand off

The first apply seeds the master password from one of two sources:

1. If `infra/dev/secrets/` is applied, the cluster reads the live
   value from `panakoes-dev/postgres-auth-db-password`.
2. If secrets is still pending, Terraform creates a `random_password`
   resource and uses that value.

In both cases, immediately after apply, rotate the master password to
a fresh value and store it in Secrets Manager. This breaks any
linkage between Terraform state (which sees the password) and the
running cluster:

    NEW_PASSWORD=$(openssl rand -base64 48 | tr -d '/+=' | head -c 32)

    aws secretsmanager put-secret-value \
      --region us-east-1 \
      --secret-id panakoes-dev/postgres-auth-db-password \
      --secret-string "$NEW_PASSWORD"

    aws rds modify-db-cluster \
      --region us-east-1 \
      --db-cluster-identifier "$(terraform output -raw cluster_arn | awk -F: '{print $NF}')" \
      --master-user-password "$NEW_PASSWORD" \
      --apply-immediately

The `lifecycle { ignore_changes = [master_password] }` block on the
cluster keeps Terraform from reverting that rotation on the next
apply.

### Build the connection string and write it to `panakoes-dev/database-url`

    CLUSTER_ENDPOINT=$(terraform output -raw cluster_endpoint)
    PORT=$(terraform output -raw port)
    DB_NAME=panakoes_auth
    USER=panakoes_auth

    aws secretsmanager put-secret-value \
      --region us-east-1 \
      --secret-id panakoes-dev/database-url \
      --secret-string "postgres://${USER}:${NEW_PASSWORD}@${CLUSTER_ENDPOINT}:${PORT}/${DB_NAME}"

The auth service reads `panakoes-dev/database-url` from Secrets
Manager at startup; once the secret is populated, the service has a
live path to the cluster.

### Hand off to the auth service

1. Update the auth service ECS task definition to inject
   `panakoes-dev/database-url` as `DATABASE_URL` via the ECS execution
   role's Secrets Manager grant (already provisioned in
   `infra/dev/iam/`).
2. Run `pnpm drizzle-kit push` from the auth service against the
   cluster to create the Better-Auth schema (`user`, `session`,
   `account`, `verification` tables).
3. Smoke-test `POST /auth/sign-up` via the auth service health-check.

## Connectivity model

- **Inbound**: 5432/TCP from the dev VPC CIDR `10.10.0.0/16`. Any
  resource attached to the dev VPC (ECS tasks, Lambda-in-VPC, an
  operator using AWS Session Manager or a bastion) can reach the
  cluster. There is no internet path.
- **Outbound**: unrestricted from the cluster's security group.
  Aurora has no meaningful outbound surface, but the rule is explicit
  for least-surprise.
- **Future tightening**: when ECS task security groups exist, replace
  the VPC CIDR ingress rule with a security-group-to-security-group
  reference (one per consuming service). The `security_group_id`
  output is the seam for that change.

## Variables

| Variable                | Type   | Default          | Purpose                                        |
|-------------------------|--------|------------------|------------------------------------------------|
| `aws_region`            | string | `us-east-1`      | AWS region                                     |
| `environment`           | string | `dev`            | Environment name (tagging, naming)             |
| `project_name`          | string | `panakoes`       | Project name (tagging, naming)                 |
| `engine_version`        | string | `16.4`           | Aurora Postgres engine version                 |
| `min_capacity_acu`      | number | `0.5`            | Serverless v2 minimum capacity (ACUs)          |
| `max_capacity_acu`      | number | `4`              | Serverless v2 maximum capacity (ACUs)          |
| `backup_retention_days` | number | `7`              | Automated backup retention                     |
| `master_username`       | string | `panakoes_auth`  | Cluster master username                        |
| `database_name`         | string | `panakoes_auth`  | Initial database created on bootstrap          |

## Outputs

| Output              | Type   | Purpose                                                 |
|---------------------|--------|---------------------------------------------------------|
| `cluster_arn`       | string | Cluster ARN; required in IAM policies                   |
| `cluster_endpoint`  | string | Writer endpoint; auth service uses this                 |
| `reader_endpoint`   | string | Reader endpoint; analytics / reports                    |
| `port`              | number | Postgres port (5432)                                    |
| `security_group_id` | string | Cluster SG ID; consumers reference for SG-to-SG rules   |
| `kms_key_arn`       | string | CMK ARN; required for `kms:Decrypt` on snapshots / PI   |

## Cost expectations

- **Compute**: 0.5 ACU idle is roughly $0.06/hr ($43/mo if the
  cluster never scales above the floor). A cluster that bursts to 4
  ACU for an hour during integration tests adds about $0.40 per
  burst.
- **Storage**: $0.10/GB/month. Better-Auth schema with a few thousand
  rows is dollar-pennies.
- **Backups**: free up to the size of the cluster's data; storage
  beyond that is $0.021/GB/month.
- **KMS CMK**: $1/month plus per-request charges (negligible at dev
  query volume).
- **Enhanced Monitoring + Performance Insights (free tier)**: $0.

Total fixed cost of an idle dev cluster: about $44/month plus
storage. That is the price of a real Aurora cluster behaving the way
production will; the cheaper alternative (RDS Postgres on a t4g)
ducks the architectural rehearsal value the cluster provides.

## Consuming outputs from other configs

Downstream services (auth task role IAM policy in `infra/dev/iam/`,
ECS task definition for the auth service) read these outputs via a
`terraform_remote_state` data source pointing at this config's state:

    data "terraform_remote_state" "auth_db" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/auth-db/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.auth_db.outputs.cluster_endpoint
    #   data.terraform_remote_state.auth_db.outputs.security_group_id
    #   data.terraform_remote_state.auth_db.outputs.kms_key_arn
