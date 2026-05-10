locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "ecs"
  }

  name_prefix  = "${var.project_name}-${var.environment}"
  cluster_name = local.name_prefix

  # ---------------------------------------------------------------------
  # Network references
  # ---------------------------------------------------------------------
  vpc_id             = data.terraform_remote_state.network.outputs.vpc_id
  vpc_cidr_block     = data.terraform_remote_state.network.outputs.vpc_cidr_block
  private_subnet_ids = data.terraform_remote_state.network.outputs.private_subnet_ids

  # ---------------------------------------------------------------------
  # IAM references (consumed, not provisioned, in this module)
  # ---------------------------------------------------------------------
  auth_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["auth"]
  auth_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["auth"]

  # ---------------------------------------------------------------------
  # Observability references
  # ---------------------------------------------------------------------
  auth_log_group_name = data.terraform_remote_state.observability.outputs.log_group_names["auth"]

  # ---------------------------------------------------------------------
  # Secret ARN references (resolved at task start by the execution role)
  # ---------------------------------------------------------------------
  secret_arns             = data.terraform_remote_state.secrets.outputs.secret_arns
  jwt_signing_secret_arn  = local.secret_arns["jwt-signing-secret"]
  database_url_secret_arn = local.secret_arns["database-url"]

  # ---------------------------------------------------------------------
  # Cross-module SG references
  # ---------------------------------------------------------------------
  api_gateway_vpc_link_sg_id = data.terraform_remote_state.api_gateway.outputs.vpc_link_security_group_id

  # ---------------------------------------------------------------------
  # ECR image URI for the auth service
  #
  # Constructed from the current account + region rather than
  # hardcoded so a future cross-account / multi-region rollout does
  # not require a code edit. `ecr_account_id_override` covers the
  # cross-account case explicitly.
  # ---------------------------------------------------------------------
  ecr_account_id = (
    var.ecr_account_id_override == ""
    ? data.aws_caller_identity.current.account_id
    : var.ecr_account_id_override
  )

  auth_image_uri = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-auth:${var.auth_image_tag}"
}

# ===========================================================================
# ECS cluster
#
# Fargate-only; we never register EC2 capacity providers. Container
# Insights flips on per the variable so dashboards have task-level
# CPU / memory / network metrics from day one. Capacity providers
# `FARGATE` and `FARGATE_SPOT` are both registered; today every
# service in this module runs on `FARGATE`, but registering Spot now
# makes a future cost-optimisation flip a one-line task-definition
# change instead of a cluster-level migration.
# ===========================================================================

resource "aws_ecs_cluster" "main" {
  name = local.cluster_name

  setting {
    name  = "containerInsights"
    value = var.container_insights
  }

  tags = local.common_tags
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

# ===========================================================================
# Auth service: NLB + target group + listener
#
# Internal NLB lives in the private subnets only; nothing from the
# internet reaches it directly. The API Gateway VPC Link is the sole
# upstream that opens connections to this NLB. NLB target type `ip`
# is required for awsvpc-mode Fargate tasks; `instance` only works
# with EC2 launch type.
# ===========================================================================

resource "aws_lb" "auth" {
  name               = "${local.name_prefix}-auth"
  internal           = true
  load_balancer_type = "network"
  subnets            = local.private_subnet_ids

  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = false

  tags = merge(local.common_tags, {
    Service = "auth"
  })
}

resource "aws_lb_target_group" "auth" {
  name        = "${local.name_prefix}-auth"
  port        = var.auth_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  deregistration_delay = var.auth_deregistration_delay_seconds

  # Health check is HTTP against the auth service's `/health` route
  # (services/auth/src/health/health.ts returns `{status: "ok"}`).
  # NLB supports HTTP/HTTPS health checks on TCP target groups; we
  # use HTTP to avoid the cost of a TLS listener at this layer (the
  # NLB is internal-only and the API Gateway VPC Link to NLB hop
  # stays inside the VPC; TLS lives at the public ingress edge).
  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = var.auth_health_check_path
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
    timeout             = 6
    matcher             = "200"
  }

  tags = merge(local.common_tags, {
    Service = "auth"
  })
}

resource "aws_lb_listener" "auth" {
  load_balancer_arn = aws_lb.auth.arn
  port              = var.auth_container_port
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.auth.arn
  }

  tags = merge(local.common_tags, {
    Service = "auth"
  })
}

# ===========================================================================
# Auth task security group
#
# Inbound: container port (8080 by default) from the API Gateway
# VPC Link SG only. The NLB itself does not have a security group
# (NLBs do not support SGs in the classic sense; the target SG
# enforces the boundary). The VPC Link SG referenced here is the
# documented egress origin for VPC Link -> NLB target traffic
# (api-gateway module's outputs.tf, vpc_link_security_group_id).
#
# Outbound: open to the VPC CIDR. The auth task talks to:
#   - Aurora Postgres on 5432 (auth-db SG)
#   - Secrets Manager via VPC interface endpoint (vpc-endpoints SG)
#   - CloudWatch Logs via VPC interface endpoint
#   - ECR (api + dkr) via VPC interface endpoints (image pull at
#     task-start; the execution role drives this, but the task SG
#     gates the network path)
# Restricting egress further is a follow-up tightening pass; today
# VPC-CIDR egress matches the project pattern.
# ===========================================================================

resource "aws_security_group" "auth_task" {
  name        = "${local.name_prefix}-auth-task"
  description = "Security group for the auth ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR for DB / Secrets / Logs / ECR via VPC endpoints."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-auth-task"
    Service = "auth"
  })
}

resource "aws_vpc_security_group_ingress_rule" "auth_task_from_vpc_link" {
  security_group_id            = aws_security_group.auth_task.id
  description                  = "Allow API Gateway VPC Link to reach the auth container port."
  from_port                    = var.auth_container_port
  to_port                      = var.auth_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "auth"
  })
}

# NLB health checks originate from inside the VPC against the target
# IP on the traffic port; AWS does not document a fixed source range
# for NLB health checks, but they always come from inside the VPC the
# NLB lives in. The VPC-CIDR ingress rule below covers them
# explicitly so the target group's health checks do not depend on
# any AWS-internal source classification.
resource "aws_vpc_security_group_ingress_rule" "auth_task_healthcheck" {
  security_group_id = aws_security_group.auth_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the auth container port."
  from_port         = var.auth_container_port
  to_port           = var.auth_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "auth"
  })
}

resource "aws_vpc_security_group_egress_rule" "auth_task_egress" {
  security_group_id = aws_security_group.auth_task.id
  description       = "Allow the auth task to reach Aurora, Secrets Manager, CloudWatch Logs, and ECR (interface endpoints) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "auth"
  })
}

# S3 gateway endpoint requires egress to the S3 prefix list, NOT the
# VPC CIDR. Gateway endpoints work by route-table redirect: the packet's
# destination IP is still S3's public IP (e.g. 54.231.x.x), and the
# route table sends it to the gateway endpoint. The security group must
# allow that destination explicitly.
#
# Without this rule, the ECS task can REACH the ECR DKR interface
# endpoint (which lives at a VPC-internal IP), but the actual image
# layer download from S3 (where ECR stores blobs) times out.
# Symptom: `CannotPullContainerError: dial tcp <S3 IP>:443: i/o timeout`.
data "aws_prefix_list" "s3" {
  name = "com.amazonaws.${data.aws_region.current.region}.s3"
}

# DynamoDB is also a gateway endpoint (per infra/dev/vpc-endpoints/);
# any task that uses DynamoDB needs an explicit egress rule to the
# DynamoDB prefix list for the same reason as S3 above. The auth task
# does not currently call DynamoDB, but services like cost-api and
# admin-api do; the prefix list lookup lives here so any service
# task SG in this module can reference it without re-declaring the data.
data "aws_prefix_list" "dynamodb" {
  name = "com.amazonaws.${data.aws_region.current.region}.dynamodb"
}

resource "aws_vpc_security_group_egress_rule" "auth_task_egress_s3" {
  security_group_id = aws_security_group.auth_task.id
  description       = "Allow the auth task to reach S3 (for ECR layer downloads) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "auth"
  })
}

# ===========================================================================
# Auth task definition
#
# Fargate launch type, awsvpc network mode (the only mode Fargate
# supports). Architecture is ARM64 (Graviton) for the auth workload
# specifically: a Node.js HTTP server doing JWT signing + Drizzle
# Postgres calls is CPU-light and IO-bound, exactly the profile
# Graviton wins on; AWS lists ARM64 Fargate as ~20% cheaper per
# vCPU-hour than x86_64. The auth Dockerfile (services/auth/Dockerfile)
# uses node:22-slim which has multi-arch image manifests, so the
# `latest` tag resolves to the ARM64 variant on this task; the build
# pipeline must publish a multi-arch manifest (or an explicit ARM64
# tag) for this to work. If the build pipeline only ships x86_64,
# either flip `cpu_architecture` here to `X86_64` or rebuild the
# image with buildx for both architectures.
#
# Runtime env vars cover the auth service config schema in
# services/auth/src/config.ts. Required: DATABASE_URL,
# AUTH_JWT_SECRET. Optional with defaults that we override
# explicitly: PORT, LOG_LEVEL, NODE_ENV, AUTH_JWT_ISSUER,
# AUTH_JWT_AUDIENCE, AUTH_JWT_EXPIRES_IN_SECONDS, BETTER_AUTH_URL.
#
# Secrets are resolved by ECS at task start using the execution
# role's secretsmanager:GetSecretValue permission (granted in
# infra/dev/iam/main.tf for the `database-url` and
# `jwt-signing-secret` ARNs). The application sees them as ordinary
# environment variables; it does not need any AWS SDK call.
# ===========================================================================

resource "aws_ecs_task_definition" "auth" {
  family                   = "${local.name_prefix}-auth"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.auth_cpu
  memory                   = var.auth_memory

  execution_role_arn = local.auth_execution_role_arn
  task_role_arn      = local.auth_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "auth"
      image     = local.auth_image_uri
      essential = true

      portMappings = [
        {
          containerPort = var.auth_container_port
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "PORT", value = tostring(var.auth_container_port) },
        { name = "LOG_LEVEL", value = var.auth_log_level },
        { name = "NODE_ENV", value = var.auth_node_env },
        { name = "AUTH_JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "AUTH_JWT_AUDIENCE", value = var.auth_jwt_audience },
        { name = "AUTH_JWT_EXPIRES_IN_SECONDS", value = tostring(var.auth_jwt_expires_in_seconds) },
        { name = "BETTER_AUTH_URL", value = var.auth_better_auth_url },
        { name = "AWS_REGION", value = data.aws_region.current.region },
      ]

      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = local.database_url_secret_arn
        },
        {
          name      = "AUTH_JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.auth_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      # Container-level health check mirrors the NLB target-group
      # health check so a wedged process gets killed by ECS even if
      # the NLB has not yet flipped the target unhealthy. Uses
      # node's built-in fetch (Node 22) so we do not need curl in
      # the runtime image.
      healthCheck = {
        command = [
          "CMD-SHELL",
          "node -e \"fetch('http://127.0.0.1:${var.auth_container_port}${var.auth_health_check_path}').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "auth"
  })
}

# ===========================================================================
# Auth ECS service
#
# Fargate launch via the cluster's default capacity provider strategy
# (FARGATE base 1, weight 1). Tasks land in the three private
# subnets and attach the auth_task SG. ARM64 Fargate slots are
# universally available in us-east-1.
#
# `force_new_deployment = true` paired with `triggers_replace` on
# the task definition revision means a `terraform apply` after a new
# image push (which moves the `latest` tag) does NOT trigger a
# rollout on its own. Real CD will land via a GitHub Actions
# workflow that calls `aws ecs update-service --force-new-deployment`
# directly; this Terraform module owns the *shape* of the service,
# not the deploy cadence.
#
# `health_check_grace_period_seconds` is set to 60 to give the Node
# process time to boot and start serving `/health` before the NLB
# starts marking it unhealthy.
#
# `deployment_circuit_breaker` rolls back automatically on a failed
# deployment (5 consecutive task failures). Cheap insurance.
# ===========================================================================

resource "aws_ecs_service" "auth" {
  name            = "${local.name_prefix}-auth"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.auth.arn
  desired_count   = var.auth_desired_count
  launch_type     = "FARGATE"

  # Single-AZ tolerance for dev (1 task across 3 subnets means AWS
  # picks one); 1 task is fine for non-prod.
  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.auth_task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.auth.arn
    container_name   = "auth"
    container_port   = var.auth_container_port
  }

  health_check_grace_period_seconds = 60

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # Avoid Terraform churn when the task-definition revision moves
  # via an out-of-band deploy (e.g. CD pipeline doing
  # `update-service --force-new-deployment` after pushing a new
  # image). Without this, every Terraform apply would attempt to
  # roll the service back to the revision Terraform last applied.
  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  tags = merge(local.common_tags, {
    Service = "auth"
  })

  depends_on = [aws_lb_listener.auth]
}
