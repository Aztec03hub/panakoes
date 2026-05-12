# infra/dev/ecs

Terraform module that provisions the dev-environment ECS Fargate cluster and the application services deployed on top of it.

Today's services:

- **auth** (TypeScript / Better-Auth on Hono, port 8080)
- **cost-api** (Python / FastAPI, port 8000): Tier 2 admin-dashboard backend
- **admin-api** (Python / FastAPI, port 8000): Tier 3 admin-dashboard backend (lifecycle ops)
- **ingestion-api** (Python / FastAPI, port 8000): pre-signed-URL audio upload surface
- **query-api** (Python / FastAPI, port 8000): read-only surface across ingestion, summaries, sessions
- **gpu-spawner** (Python / FastAPI, port 8000): issues `ec2:RunInstances` for streaming GPU sessions
- **health-aggregator** (Python / FastAPI, port 8000): Tier 1 admin-dashboard backend; polls ECS / ELBv2 / Logs to compute per-service health
- **summarization** (Python / FastAPI, port 8000): Anthropic Claude summarization of transcripts
- **notification** (Python / FastAPI, port 8000): SES transactional email + webhook delivery
- **session-manager** (Python / FastAPI, port 8000): streaming-session lifecycle state
- **billing** (Python / FastAPI, port 8000): Stripe TEST-mode billing + webhook ingest

This is the **first application-service deploy module in the project**. The pattern set here is the template every subsequent service module follows: ingestion-api and query-api land in their own follow-up PRs.

## What this module owns

- One ECS cluster (`panakoes-dev`), Fargate-only, Container Insights enabled. Both `FARGATE` and `FARGATE_SPOT` capacity providers registered; `FARGATE` is the default strategy.
- One internal Network Load Balancer per service (today: `panakoes-dev-auth`), in the three private subnets, no public exposure.
- One TCP listener + one IP-target-type target group per service, with HTTP `/health` health checks against the container.
- One security group per service, locked down: inbound only from the API Gateway VPC Link SG (and intra-VPC for NLB health checks).
- One ECS task definition + one ECS service per microservice (today: `auth`).

## What this module does NOT own

- IAM roles. The task role and ECS task execution role are owned by `infra/dev/iam/`. This module pulls their ARNs via `terraform_remote_state` and references them in the task definition.
- CloudWatch log groups. The auth service log group `/panakoes/dev/auth` is owned by `infra/dev/observability/` (single source of truth for retention, encryption, metric filters). This module references it by name.
- KMS keys. Log encryption uses the shared observability CMK (the auth log group at `/panakoes/dev/auth` matches the key's `/panakoes/dev/*` encryption-context condition; this is NOT the Lambda case where each module needs its own key).
- Secrets. `infra/dev/secrets/` provisions the Secrets Manager entries (`jwt-signing-secret`, `database-url`); this module references their ARNs in the task definition's `secrets` block, and ECS resolves them at task start using the execution role.
- The Aurora cluster. `infra/dev/auth-db/` provisions the cluster and its security group. This module exposes the auth task SG so the Aurora SG can be tightened from VPC-CIDR ingress to SG-to-SG ingress in a follow-up pass.

## Apply prerequisites

In dependency order, apply or confirm applied:

1. `infra/bootstrap/` (state backend bucket + KMS).
2. `infra/dev/network/` (VPC, subnets).
3. `infra/dev/iam/` (auth task role + auth execution role with Secrets Manager permissions on `jwt-signing-secret` + `database-url`).
4. `infra/dev/observability/` (the `/panakoes/dev/auth` log group must exist before the awslogs driver in the task definition starts emitting).
5. `infra/dev/secrets/` (the secret resources must exist; their *values* should also be populated with real values before the auth tasks boot or they will crash on config validation).
6. `infra/dev/auth-db/` (the Aurora cluster and its endpoint must exist; the `database-url` secret must be populated with the real connection string).
7. `infra/dev/api-gateway/` (initial apply with `var.discover_ecs_nlbs = false` so the VPC Link security group exists).
8. **Auth service container image must be in ECR** at `659225405128.dkr.ecr.us-east-1.amazonaws.com/panakoes-dev-auth:latest` (built and pushed by the `services/auth` build pipeline). The ECS task pull will fail with `CannotPullContainerError` if the image is missing.

Then `terraform apply` this module.

## Post-apply: wire api-gateway to the discovered NLBs

After this module's first successful apply, flip `infra/dev/api-gateway/` to discovery mode:

```bash
cd infra/dev/api-gateway
terraform apply -var='discover_ecs_nlbs=true'
```

The api-gateway module reads this module's `nlb_listener_arns` output and provisions one VPC Link integration + the matching routes per discovered service. See `infra/dev/api-gateway/data.tf` line 48-83 for the documented contract.

## The contract: `nlb_listener_arns` output

```hcl
output "nlb_listener_arns" {
  value = {
    auth            = aws_lb_listener.auth.arn
    "cost-api"      = aws_lb_listener.cost_api.arn
    "admin-api"     = aws_lb_listener.admin_api.arn
    "ingestion-api" = aws_lb_listener.ingestion_api.arn
    "query-api"     = aws_lb_listener.query_api.arn
    # summarization  = aws_lb_listener.summarization.arn
    # ...
    auth              = aws_lb_listener.auth.arn
    "cost-api"        = aws_lb_listener.cost_api.arn
    "admin-api"       = aws_lb_listener.admin_api.arn
    summarization     = aws_lb_listener.summarization.arn
    notification      = aws_lb_listener.notification.arn
    "session-manager" = aws_lb_listener.session_manager.arn
    billing           = aws_lb_listener.billing.arn
    # ingestion-api    = aws_lb_listener.ingestion_api.arn  # PR #223
    # query-api        = aws_lb_listener.query_api.arn      # PR #223
  }
}
```

Map keys MUST match the service names the api-gateway module's `local.routes` table uses (`auth`, `ingestion-api`, `summarization`, `notification`, `query-api`, `session-manager`, `billing`). A mismatch silently drops the routes for the affected service in api-gateway's `local.active_routes` filter.

## Adding a new service

Mirror the auth / cost-api / admin-api resource set in a dedicated `<service>.tf` file:

1. New `aws_lb` (internal, NLB type, private subnets).
2. New `aws_lb_target_group` (port = container port, protocol = TCP, target_type = ip, HTTP `/health` health check).
3. New `aws_lb_listener` (TCP on the container port, default action = forward to the target group).
4. New `aws_security_group` for the service tasks, with:
   - inbound from the API Gateway VPC Link SG on the container port,
   - inbound from the VPC CIDR on the container port (NLB health checks),
   - egress to the VPC CIDR (interface VPC endpoints: Secrets Manager, ECR, Logs, STS, KMS),
   - egress to the S3 prefix list on 443 (gateway endpoint, required for ECR layer downloads),
   - egress to the DynamoDB prefix list on 443 (gateway endpoint, only if the service uses DDB).
5. New `aws_ecs_task_definition` (Fargate, awsvpc, ARM64 unless the workload needs x86_64, with the service's required env vars and secret ARNs).
6. New `aws_ecs_service` (cluster = `aws_ecs_cluster.main`, launch_type = FARGATE, attach the target group, attach the task SG).
7. **Append the listener ARN to `nlb_listener_arns` in `outputs.tf` under the matching service-name key** (this is the contract `infra/dev/api-gateway/` reads).
8. Add per-service `<service>_*` variables to `variables.tf` (image tag, container port, cpu/memory, desired_count, log level, health check path, deregistration delay) so production overrides do not require a module rewrite.
9. Add per-service outputs (NLB ARN, NLB DNS, target group ARN, task definition ARN/family, service name/ARN, task SG ID).
10. Add the service name to `local.ecs_services` in `infra/dev/iam/main.tf` (provisions execution + task roles) and to its `local.execution_secret_arns` map (which secrets it reads at startup). Wire the service-specific task-role inline policy in the same module.
11. Add the service name to the log-group provisioning loop in `infra/dev/observability/main.tf`.
12. `terraform apply` this module, then re-apply api-gateway (its `discover_ecs_nlbs=true` mode picks up the new listener ARN automatically).

File-per-service convention: keep `auth.tf`-style files (today: `cost_api.tf`, `admin_api.tf`) so the pattern stays scannable as more services land.

## CPU architecture choice

Auth (and most TS / Python HTTP services) ships ARM64 (Graviton). Rationale:

- Node.js / Python HTTP services are CPU-light and IO-bound, exactly the profile Graviton wins on.
- ARM64 Fargate is roughly 20% cheaper per vCPU-hour than x86_64.
- `node:22-slim` (the auth Dockerfile base) ships a multi-arch manifest, so the existing `latest` tag resolves to the ARM64 variant on the task automatically.

If a service needs an x86_64-only dependency (native binaries without an ARM64 build), flip `cpu_architecture = "X86_64"` in that service's task definition `runtime_platform` block. The build pipeline must still publish a manifest (or an explicit ARM64 / x86_64 tag) for the ARM64 default to work.

## Auth service environment

Required env vars (from `services/auth/src/config.ts`):

- `DATABASE_URL` (secret, from `panakoes-dev/database-url`)
- `AUTH_JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`)

Optional env vars passed explicitly by this module:

- `PORT` (8080)
- `LOG_LEVEL` (info)
- `NODE_ENV` (production)
- `AUTH_JWT_ISSUER` (`https://auth.panakoes.com`)
- `AUTH_JWT_AUDIENCE` (`panakoes-api`)
- `AUTH_JWT_EXPIRES_IN_SECONDS` (3600)
- `BETTER_AUTH_URL` (`https://auth.panakoes.com`, placeholder until custom domain wires up)
- `AWS_REGION` (`us-east-1`)

## Deploy cadence

This module owns the *shape* of the service, not the deploy. CD (a future GitHub Actions workflow) will push a new image to ECR and call `aws ecs update-service --cluster panakoes-dev --service panakoes-dev-auth --force-new-deployment`. The `lifecycle.ignore_changes = [task_definition, desired_count]` block on the ECS service prevents Terraform from rolling back the out-of-band deployment on the next apply.

To bump the task-definition shape itself (e.g. add an env var), edit this module and apply. The new revision will register but the running service will not roll until the next CD trigger or a manual `update-service` call.

## cost-api service environment

Required env vars (from `services/cost-api/src/panakoes_cost_api/config.py`):

- `AUTH_JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`) for JWT validation

Optional env vars passed explicitly by this module:

- `SERVICE_NAME` (`cost-api`)
- `LOG_LEVEL` (`INFO`)
- `AWS_REGION` (`us-east-1`)
- `COST_CACHE_TABLE` (`panakoes-dev-cost-cache`)
- `TENANT_COST_ROLLUP_TABLE` (`panakoes-dev-tenant-cost-rollup`)
- `ALERT_STATE_TABLE` (`panakoes-dev-alert-state`)
- `AUDIT_LOG_TABLE` (`panakoes-dev-audit-log`)

## admin-api service environment

Required env vars (from `services/admin-api/src/panakoes_admin_api/config.py`):

- `AUTH_JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`) for JWT validation

Optional env vars passed explicitly by this module:

- `SERVICE_NAME` (`admin-api`)
- `LOG_LEVEL` (`INFO`)
- `AWS_REGION` (`us-east-1`)
- `LIFECYCLE_STATE_TABLE`, `AUDIT_LOG_TABLE`, `STREAMING_SESSIONS_TABLE`, `INGESTION_TABLE`, `TENANTS_TABLE`, `API_KEYS_TABLE` (the six DDB tables admin-api reads or mutates)
- `EVENTS_BUS_NAME` (`panakoes-dev`, the project EventBridge bus)

## ingestion-api service environment

Required env vars (from `services/ingestion-api/src/panakoes_ingestion_api/config.py`):

- `AUTH_JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`) for JWT validation

Optional env vars passed explicitly by this module:

- `SERVICE_NAME` (`ingestion-api`)
- `LOG_LEVEL` (`INFO`)
- `AWS_REGION` (`us-east-1`)
- `INGESTION_TABLE_NAME` (pulled from `infra/dev/data` remote state)
- `INGESTION_BUCKET` (pulled from `infra/dev/storage` remote state; bucket carries a random_id suffix)
- `PRESIGNED_URL_TTL_SECONDS` (`900`, 15-minute documented contract)
- `AUTH_JWT_ISSUER` / `AUTH_JWT_AUDIENCE` (the AUTH_JWT_* prefix is load-bearing here; ingestion-api's pydantic-settings schema uses that prefix, NOT the JWT_* prefix cost-api / admin-api / query-api use)

## query-api service environment

Required env vars (from `services/query-api/src/panakoes_query_api/config.py`):

- `JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`) for JWT validation

Optional env vars passed explicitly by this module:

- `SERVICE_NAME` (`query-api`)
- `LOG_LEVEL` (`INFO`)
- `AWS_REGION` (`us-east-1`)
- `DDB_INGESTION_TABLE` (pulled from `infra/dev/data` remote state)
- `DDB_SUMMARIES_TABLE` (`panakoes-dev-summaries`; the summaries table is NOT yet provisioned in `infra/dev/data/`, so endpoints that hit it will return `ResourceNotFoundException` at runtime until the table lands)
- `DDB_SESSIONS_TABLE` (pulled from `infra/dev/data` remote state)
- `JWT_ISSUER` / `JWT_AUDIENCE`
## summarization service environment

Required env vars (from `services/summarization/src/panakoes_summarization/config.py`):

- `JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`)
- `ANTHROPIC_API_KEY` (secret, from `panakoes-dev/anthropic-api-key`)

Optional env vars passed explicitly:

- `SERVICE_NAME` (`summarization`), `LOG_LEVEL` (`INFO`), `AWS_REGION` (`us-east-1`)
- `S3_TRANSCRIPTS_BUCKET` (`panakoes-dev-transcripts`)
- `S3_SUMMARIES_BUCKET` (`panakoes-dev-summaries`)
- `DDB_SUMMARIES_TABLE` (`panakoes-dev-summaries`)
- `JWT_ISSUER`, `JWT_AUDIENCE` (track `var.auth_jwt_issuer` / `var.auth_jwt_audience`)

## notification service environment

Required env vars (from `services/notification/src/panakoes_notification/config.py`):

- `JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`)
- `SES_SMTP` (secret, from `panakoes-dev/ses-smtp-credentials`; JSON-shaped `{username, password}`)

Optional env vars passed explicitly:

- `SERVICE_NAME` (`notification`), `LOG_LEVEL` (`INFO`), `AWS_REGION` (`us-east-1`)
- `SES_FROM_ADDRESS` (`no-reply@panakoes.com`)
- `DDB_NOTIFICATION_TABLE` (`panakoes-dev-notification`)
- `JWT_ISSUER`, `JWT_AUDIENCE`

## session-manager service environment

Required env vars:

- `JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`)

Optional env vars passed explicitly:

- `SERVICE_NAME` (`session-manager`), `LOG_LEVEL` (`INFO`), `AWS_REGION` (`us-east-1`)
- `SESSIONS_TABLE_NAME` (`panakoes-dev-streaming-sessions`)
- `JWT_ISSUER`, `JWT_AUDIENCE`

Note: `services/session-manager/src/panakoes_session_manager/config.py` currently reads `AUTH_JWT_SECRET` / `AUTH_JWT_ISSUER` / `AUTH_JWT_AUDIENCE`. The task definition here ships the project-standard `JWT_*` names per the platform contract (PR #218 root cause). A follow-up service-side rename is required before tokens validate end-to-end.

## billing service environment

Required env vars (from `services/billing/src/panakoes_billing/config.py`):

- `JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`)
- `STRIPE_API_KEY` (secret, from `panakoes-dev/stripe-test-key`; validator rejects non-`sk_test_` values)
- `STRIPE_WEBHOOK_SECRET` (secret, from `panakoes-dev/stripe-webhook-signing-secret`)

Optional env vars passed explicitly:

- `SERVICE_NAME` (`billing`), `LOG_LEVEL` (`INFO`), `AWS_REGION` (`us-east-1`)
- `DDB_BILLING_TABLE` (`panakoes-dev-billing-events`)
- `JWT_ISSUER`, `JWT_AUDIENCE`

## Outputs

- `cluster_arn`, `cluster_name`, `cluster_id`: cluster identifiers.
- `nlb_listener_arns`: **the contract output** consumed by `infra/dev/api-gateway/`. Today maps `auth`, `cost-api`, `admin-api`, `ingestion-api`, `query-api`, `health-aggregator`.

## health-aggregator service environment

Required env vars (from `services/health-aggregator/src/panakoes_health_aggregator/config.py`):

- `JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`) for JWT validation

Optional env vars passed explicitly by this module:

- `SERVICE_NAME` (`health-aggregator`)
- `LOG_LEVEL` (`INFO`)
- `AWS_REGION` (`us-east-1`)
- `ECS_CLUSTER` (`panakoes-dev`; pinned to the cluster this module provisions)
- `JWT_ISSUER` / `JWT_AUDIENCE`

Task role grants are scoped READ-only: `ecs:DescribeServices/ListServices` on the `panakoes-dev` cluster, `elasticloadbalancing:DescribeTargetHealth/DescribeTargetGroups/DescribeLoadBalancers` (no resource-level auth on ELBv2 Describe* verbs), and `logs:FilterLogEvents/DescribeLogGroups/DescribeLogStreams` on `/panakoes/dev/*`.
- `auth_*`, `cost_api_*`, `admin_api_*`, `ingestion_api_*`, `query_api_*`: per-service NLB / target group / task definition / service / task SG references.

## gpu-spawner service environment

Required env vars (from `services/gpu-spawner/src/panakoes_gpu_spawner/config.py`):

- `JWT_SECRET` (secret, from `panakoes-dev/jwt-signing-secret`) for JWT validation
- `GPU_AMI_ID` (the gpu-transcribe Packer AMI; pinned via `var.gpu_spawner_ami_id`, defaults to the bake in `infra/dev/batch/variables.tf`)
- `GPU_INSTANCE_TYPE` (`g4dn.xlarge`)
- `GPU_SECURITY_GROUP_ID` (SG attached to the launched GPU instance; defaults to empty so a misconfigured deploy fails fast at first spawn request rather than at task boot)
- `GPU_SUBNET_ID` (subnet the launched instance lands in; same fail-fast default as SG)
- `GPU_IAM_INSTANCE_PROFILE` (consumed from `infra/dev/iam` output `gpu_instance_profile_name`; the spawner's `iam:PassRole` grant is scoped to this exact profile)
- `SESSION_MANAGER_WS_ENDPOINT` (`wss://session-manager.panakoes.com`)
- `PROJECT_TAG` (`panakoes`)
- `GPU_SPAWNER_TAG` (`panakoes-dev-gpu-spawner`; the spawner enforces this tag on launch and the IAM policy gates `RunInstances` + `TerminateInstances` on the same value)

Optional env vars passed explicitly by this module:

- `SERVICE_NAME` (`gpu-spawner`)
- `LOG_LEVEL` (`INFO`)
- `AWS_REGION` (`us-east-1`)
- `JWT_ISSUER` / `JWT_AUDIENCE` (must match the auth service's signing claims)

Notes:

- The task role's trust principal was `lambda.amazonaws.com` in the original Lambda plan; this PR flips it to `ecs-tasks.amazonaws.com` in `infra/dev/iam/main.tf` and provisions a matching ECS execution role via the `aws_iam_role.execution` for_each loop. The existing `ec2:RunInstances` / `iam:PassRole` policy attached to the task role is unchanged.
- The image-tag default is the placeholder `initial`; the first apply requires `TF_VAR_gpu_spawner_image_tag=initial-<sha>` once the `image-bake-on-change.yml` workflow (PR #268) lands the first bake for `gpu-spawner` in ECR.
- `nlb_listener_arns`: **the contract output** consumed by `infra/dev/api-gateway/`. Maps `auth`, `cost-api`, `admin-api`, `summarization`, `notification`, `session-manager`, `billing`.
- `auth_*`, `cost_api_*`, `admin_api_*`, `summarization_*`, `notification_*`, `session_manager_*`, `billing_*`: per-service NLB / target group / task definition / service / task SG references.
