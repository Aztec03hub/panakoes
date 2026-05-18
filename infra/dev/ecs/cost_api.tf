# ===========================================================================
# cost-api service: NLB + target group + listener + SG + task def + service
#
# Tier 2 admin-dashboard backend. Python / FastAPI on ARM64 Fargate. The
# service reads AWS Cost Explorer at the application layer and caches
# results in DynamoDB (cost-cache, tenant-cost-rollup, alert-state); it
# validates JWTs minted by the auth service using the shared
# `jwt-signing-secret` (read at task start by the execution role).
#
# Mirrors the auth service shape (NLB internal-only, IP target type,
# HTTP /health probe, container SG locked to the API Gateway VPC Link
# SG inbound + VPC-CIDR + S3 prefix list + DynamoDB prefix list egress,
# Fargate awsvpc, ARM64 runtime). Differences from auth:
#
#   - Container port is 8000 (uvicorn default; matches services/cost-api
#     Dockerfile EXPOSE 8000 + CMD --port 8000).
#   - No DATABASE_URL secret. cost-api has no SQL backend; all state is
#     DynamoDB read via the boto3 default credential chain that picks
#     up the task role.
#   - Adds a DynamoDB prefix-list egress rule. cost-api uses 4 DDB
#     tables; without this rule, boto3 calls to DDB IPs fall back to
#     NAT (which dev does not have) and time out at task start.
# ===========================================================================

locals {
  cost_api_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["cost-api"]
  cost_api_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["cost-api"]
  cost_api_log_group_name     = data.terraform_remote_state.observability.outputs.log_group_names["cost-api"]
  cost_api_image_uri          = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-cost-api:${var.cost_api_image_tag}"
}

# ---------------------------------------------------------------------------
# cost-api task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "cost_api_task" {
  name        = "${local.name_prefix}-cost-api-task"
  description = "Security group for the cost-api ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints) plus S3 and DynamoDB gateway-endpoint prefix lists."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-cost-api-task"
    Service = "cost-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "cost_api_task_from_vpc_link" {
  security_group_id            = aws_security_group.cost_api_task.id
  description                  = "Allow API Gateway VPC Link to reach the cost-api container port."
  from_port                    = var.cost_api_container_port
  to_port                      = var.cost_api_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "cost_api_task_from_alb" {
  security_group_id            = aws_security_group.cost_api_task.id
  description                  = "Allow shared ALB to reach the cost-api container port."
  from_port                    = var.cost_api_container_port
  to_port                      = var.cost_api_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = data.terraform_remote_state.alb.outputs.alb_sg_id

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "cost_api_task_healthcheck" {
  security_group_id = aws_security_group.cost_api_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the cost-api container port."
  from_port         = var.cost_api_container_port
  to_port           = var.cost_api_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "cost_api_task_egress" {
  security_group_id = aws_security_group.cost_api_task.id
  description       = "Allow the cost-api task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "cost_api_task_egress_s3" {
  security_group_id = aws_security_group.cost_api_task.id
  description       = "Allow the cost-api task to reach S3 (for ECR layer downloads) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "cost_api_task_egress_internet" {
  security_group_id = aws_security_group.cost_api_task.id
  description       = "Allow HTTPS to the internet for AWS APIs (Secrets Manager, ECR, CloudWatch Logs) -- required on public subnets without VPC interface endpoints."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "cost_api_task_egress_dynamodb" {
  security_group_id = aws_security_group.cost_api_task.id
  description       = "Allow the cost-api task to reach DynamoDB (cost-cache, tenant-cost-rollup, alert-state, audit-log) via the DynamoDB gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.dynamodb.id

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

# ---------------------------------------------------------------------------
# cost-api task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "cost_api" {
  family                   = "${local.name_prefix}-cost-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.cost_api_cpu
  memory                   = var.cost_api_memory

  execution_role_arn = local.cost_api_execution_role_arn
  task_role_arn      = local.cost_api_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "cost-api"
      image     = local.cost_api_image_uri
      essential = true

      portMappings = [
        {
          name          = "cost-api"
          containerPort = var.cost_api_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars match the pydantic-settings schema in
      # services/cost-api/src/panakoes_cost_api/config.py. Table names
      # match Terraform-provisioned table names from
      # infra/dev/admin-state/.
      environment = [
        { name = "SERVICE_NAME", value = "cost-api" },
        { name = "LOG_LEVEL", value = var.cost_api_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        # Disable the OTLP exporter until the ADOT sidecar lands.
        # Without this, panakoes_otel uses its default endpoint
        # (`http://localhost:4317`) and the gRPC exporter retries every
        # ~3s with "Failed to export metrics to localhost:4317" log
        # noise that masks real errors. Flip to `false` (or remove
        # entirely) once an ADOT sidecar pattern is wired into the
        # task definitions.
        { name = "OTEL_SDK_DISABLED", value = "true" },
        { name = "COST_CACHE_TABLE", value = "${local.name_prefix}-cost-cache" },
        { name = "TENANT_COST_ROLLUP_TABLE", value = "${local.name_prefix}-tenant-cost-rollup" },
        { name = "ALERT_STATE_TABLE", value = "${local.name_prefix}-alert-state" },
        { name = "AUDIT_LOG_TABLE", value = "${local.name_prefix}-audit-log" },
        # JWT validator env contract (panakoes_auth_client.from_env reads
        # JWT_SECRET / JWT_ISSUER / JWT_AUDIENCE). Values must match the
        # AUTH_JWT_ISSUER / AUTH_JWT_AUDIENCE the auth service signs with
        # in infra/dev/ecs/main.tf or token validation will fail.
        { name = "JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "JWT_AUDIENCE", value = var.auth_jwt_audience },
      ]

      # JWT validation secret. cost-api validates JWTs but does not
      # sign them; it has no SQL backend (no DATABASE_URL).
      secrets = [
        {
          name      = "JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.cost_api_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      # Container-level health check uses python's stdlib urllib so we
      # do not need curl in the runtime image.
      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.cost_api_container_port}${var.cost_api_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

# ---------------------------------------------------------------------------
# cost-api ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "cost_api" {
  name            = "${local.name_prefix}-cost-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.cost_api.arn
  desired_count   = var.cost_api_desired_count

  # AWS provider v6+ supports in-place capacity_provider_strategy
  # updates when force_new_deployment = true, which is preferable to
  # a destroy/recreate cycle on a running service.
  force_new_deployment = true

  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
    base              = 0
  }

  network_configuration {
    subnets          = local.public_subnet_ids
    security_groups  = [aws_security_group.cost_api_task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = data.terraform_remote_state.alb.outputs.target_group_arns["cost-api"]
    container_name   = "cost-api"
    container_port   = var.cost_api_container_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = data.terraform_remote_state.service_discovery.outputs.namespace_arn

    service {
      port_name      = "cost-api"
      discovery_name = "cost-api"

      client_alias {
        port     = var.cost_api_container_port
        dns_name = "cost-api"
      }
    }
  }

  health_check_grace_period_seconds = 60

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })

  depends_on = [data.terraform_remote_state.alb]
}
