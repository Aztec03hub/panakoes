# ===========================================================================
# admin-api service: NLB + target group + listener + SG + task def + service
#
# Tier 3 admin-dashboard backend. Python / FastAPI on ARM64 Fargate. The
# service runs lifecycle operations (terminate session, revoke API
# credentials, force password reset, suspend/reactivate tenant, etc.)
# guarded by application-layer typed-confirmation, idempotency-by-key,
# step-up MFA, and audit-before-AND-after (see ADR-032).
#
# Mirrors the auth + cost-api service shape. Differences:
#
#   - Container port is 8000 (uvicorn default).
#   - No DATABASE_URL secret. admin-api state lives in DynamoDB
#     (lifecycle-state, audit-log, streaming-sessions, ingestion,
#     tenants, api-keys); reads via the boto3 default credential chain
#     pickup the task role.
#   - Adds the DynamoDB prefix-list egress rule (admin-api uses 6 DDB
#     tables; without this rule, boto3 calls time out at task start).
#   - No EventBridge env vars beyond the bus name; the task role grants
#     events:PutEvents on the project bus directly.
# ===========================================================================

locals {
  admin_api_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["admin-api"]
  admin_api_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["admin-api"]
  admin_api_log_group_name     = data.terraform_remote_state.observability.outputs.log_group_names["admin-api"]
  admin_api_image_uri          = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-admin-api:${var.admin_api_image_tag}"
}

# ---------------------------------------------------------------------------
# admin-api task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "admin_api_task" {
  name        = "${local.name_prefix}-admin-api-task"
  description = "Security group for the admin-api ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints) plus S3 and DynamoDB gateway-endpoint prefix lists."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-admin-api-task"
    Service = "admin-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "admin_api_task_from_vpc_link" {
  security_group_id            = aws_security_group.admin_api_task.id
  description                  = "Allow API Gateway VPC Link to reach the admin-api container port."
  from_port                    = var.admin_api_container_port
  to_port                      = var.admin_api_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "admin-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "admin_api_task_from_alb" {
  security_group_id            = aws_security_group.admin_api_task.id
  description                  = "Allow shared ALB to reach the admin-api container port."
  from_port                    = var.admin_api_container_port
  to_port                      = var.admin_api_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = data.terraform_remote_state.alb.outputs.alb_sg_id

  tags = merge(local.common_tags, {
    Service = "admin-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "admin_api_task_healthcheck" {
  security_group_id = aws_security_group.admin_api_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the admin-api container port."
  from_port         = var.admin_api_container_port
  to_port           = var.admin_api_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "admin-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "admin_api_task_egress" {
  security_group_id = aws_security_group.admin_api_task.id
  description       = "Allow the admin-api task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS, Events) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "admin-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "admin_api_task_egress_internet" {
  security_group_id = aws_security_group.admin_api_task.id
  description       = "Allow HTTPS to the internet for AWS APIs (Secrets Manager, ECR, CloudWatch Logs) -- required on public subnets without VPC interface endpoints."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"

  tags = merge(local.common_tags, {
    Service = "admin-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "admin_api_task_egress_s3" {
  security_group_id = aws_security_group.admin_api_task.id
  description       = "Allow the admin-api task to reach S3 (for ECR layer downloads) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "admin-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "admin_api_task_egress_dynamodb" {
  security_group_id = aws_security_group.admin_api_task.id
  description       = "Allow the admin-api task to reach DynamoDB (lifecycle-state, audit-log, streaming-sessions, ingestion, tenants, api-keys) via the DynamoDB gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.dynamodb.id

  tags = merge(local.common_tags, {
    Service = "admin-api"
  })
}

# ---------------------------------------------------------------------------
# admin-api task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "admin_api" {
  family                   = "${local.name_prefix}-admin-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.admin_api_cpu
  memory                   = var.admin_api_memory

  execution_role_arn = local.admin_api_execution_role_arn
  task_role_arn      = local.admin_api_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "admin-api"
      image     = local.admin_api_image_uri
      essential = true

      portMappings = [
        {
          name          = "admin-api"
          containerPort = var.admin_api_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars match the pydantic-settings schema in
      # services/admin-api/src/panakoes_admin_api/config.py.
      environment = [
        { name = "SERVICE_NAME", value = "admin-api" },
        { name = "LOG_LEVEL", value = var.admin_api_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        # Disable the OTLP exporter until the ADOT sidecar lands. See
        # the matching comment in cost_api.tf for context; symmetric
        # treatment keeps task-def env contracts uniform across the
        # Python services.
        { name = "OTEL_SDK_DISABLED", value = "true" },
        { name = "LIFECYCLE_STATE_TABLE", value = "${local.name_prefix}-lifecycle-state" },
        { name = "AUDIT_LOG_TABLE", value = "${local.name_prefix}-audit-log" },
        { name = "STREAMING_SESSIONS_TABLE", value = "${local.name_prefix}-streaming-sessions" },
        { name = "INGESTION_TABLE", value = "${local.name_prefix}-ingestion" },
        { name = "TENANTS_TABLE", value = "${local.name_prefix}-tenants" },
        { name = "API_KEYS_TABLE", value = "${local.name_prefix}-api-keys" },
        { name = "EVENTS_BUS_NAME", value = local.name_prefix },
        # JWT validator env contract (panakoes_auth_client.from_env reads
        # JWT_SECRET / JWT_ISSUER / JWT_AUDIENCE). Values must match the
        # AUTH_JWT_ISSUER / AUTH_JWT_AUDIENCE the auth service signs with
        # in infra/dev/ecs/main.tf or token validation will fail.
        { name = "JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "JWT_AUDIENCE", value = var.auth_jwt_audience },
      ]

      # JWT validation secret. admin-api validates JWTs but does not
      # sign them; it has no SQL backend.
      secrets = [
        {
          name      = "JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.admin_api_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.admin_api_container_port}${var.admin_api_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "admin-api"
  })
}

# ---------------------------------------------------------------------------
# admin-api ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "admin_api" {
  name            = "${local.name_prefix}-admin-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.admin_api.arn
  desired_count   = var.admin_api_desired_count

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
    security_groups  = [aws_security_group.admin_api_task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = data.terraform_remote_state.alb.outputs.target_group_arns["admin-api"]
    container_name   = "admin-api"
    container_port   = var.admin_api_container_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = data.terraform_remote_state.service_discovery.outputs.namespace_arn

    service {
      port_name      = "admin-api"
      discovery_name = "admin-api"

      client_alias {
        port     = var.admin_api_container_port
        dns_name = "admin-api"
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
    Service = "admin-api"
  })

  depends_on = [data.terraform_remote_state.alb]
}
