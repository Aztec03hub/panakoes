# infra/dev/ecs

Terraform module that provisions the dev-environment ECS Fargate cluster and the first application service deployed on top of it: the auth microservice (Better-Auth on Hono, TypeScript), its internal Network Load Balancer, target group, security group, task definition, and ECS service.

This is the **first application-service deploy module in the project**. The pattern set here is the template every subsequent service module follows: ingestion-api, summarization, notification, query-api, session-manager, billing.

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
    auth = aws_lb_listener.auth.arn
    # ingestion-api  = aws_lb_listener.ingestion_api.arn
    # summarization  = aws_lb_listener.summarization.arn
    # ...
  }
}
```

Map keys MUST match the service names the api-gateway module's `local.routes` table uses (`auth`, `ingestion-api`, `summarization`, `notification`, `query-api`, `session-manager`, `billing`). A mismatch silently drops the routes for the affected service in api-gateway's `local.active_routes` filter.

## Adding a new service

Mirror the auth resource set:

1. New `aws_lb` (internal, NLB type, private subnets).
2. New `aws_lb_target_group` (port = container port, protocol = TCP, target_type = ip, HTTP `/health` health check).
3. New `aws_lb_listener` (TCP on the container port, default action = forward to the target group).
4. New `aws_security_group` for the service tasks, with one inbound rule from the API Gateway VPC Link SG and one egress to the VPC CIDR.
5. New `aws_ecs_task_definition` (Fargate, awsvpc, ARM64 unless the workload needs x86_64, with the service's required env vars and secret ARNs).
6. New `aws_ecs_service` (cluster = `aws_ecs_cluster.main`, launch_type = FARGATE, attach the target group, attach the task SG).
7. **Append the listener ARN to `nlb_listener_arns` in `outputs.tf` under the matching service-name key.**
8. `terraform apply` this module, then re-apply api-gateway.

For services in the same module file convention, consider splitting into `auth.tf`, `ingestion_api.tf`, etc. once more than two services live here, to keep the file size sane.

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

## Outputs

- `cluster_arn`, `cluster_name`, `cluster_id`: cluster identifiers.
- `nlb_listener_arns`: **the contract output** consumed by `infra/dev/api-gateway/`.
- `auth_nlb_arn`, `auth_nlb_dns_name`, `auth_target_group_arn`: NLB surface.
- `auth_task_definition_arn`, `auth_task_definition_family`: task definition references.
- `auth_service_name`, `auth_service_arn`: ECS service references.
- `auth_task_security_group_id`: for the planned auth-db SG-to-SG tightening pass.
