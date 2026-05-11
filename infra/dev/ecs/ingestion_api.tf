# ===========================================================================
# ingestion-api service: NLB + target group + listener + SG + task def + service
#
# FastAPI service that issues pre-signed S3 PUT URLs for raw audio
# uploads, persists upload metadata to the panakoes-dev-ingestion DDB
# table, and triggers async transcription via the transcriber-lib
# integration. Python / FastAPI on ARM64 Fargate.
#
# Mirrors the cost-api + admin-api shape (NLB internal-only, IP target
# type, HTTP /health probe, container SG locked to the API Gateway VPC
# Link SG inbound + VPC-CIDR + S3 prefix list + DynamoDB prefix list
# egress, Fargate awsvpc, ARM64 runtime). Notable shape choices:
#
#   - Container port is 8000 (uvicorn default; matches
#     services/ingestion-api/Dockerfile EXPOSE 8000 + CMD --port 8000).
#   - No DATABASE_URL secret. ingestion-api has no SQL backend; state
#     lives in DynamoDB (ingestion table) and S3 (audio-uploads
#     bucket). The iam module's execution_secret_arns map currently
#     allowlists `database_url` for this service as well, but the
#     service config.py does not declare a DATABASE_URL field; we do
#     NOT inject it here so the service does not silently inherit a
#     stale secret. Tracked as a follow-up to trim the iam allowlist.
#   - JWT validator env contract for ingestion-api uses the
#     `AUTH_JWT_SECRET` / `AUTH_JWT_ISSUER` / `AUTH_JWT_AUDIENCE` prefix
#     (config.py field names `auth_jwt_secret`, `auth_jwt_issuer`,
#     `auth_jwt_audience`), NOT the `JWT_*` prefix cost-api and
#     admin-api use. pydantic-settings is case-insensitive but the
#     prefix is load-bearing; mismatching this is the same failure
#     mode that PR #218 fixed for cost-api/admin-api.
#   - DDB prefix-list egress is required (the service reads/writes the
#     ingestion table; without the rule boto3 calls time out at task
#     start, same as cost-api).
# ===========================================================================

locals {
  ingestion_api_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["ingestion-api"]
  ingestion_api_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["ingestion-api"]
  ingestion_api_log_group_name     = data.terraform_remote_state.observability.outputs.log_group_names["ingestion-api"]
  ingestion_api_image_uri          = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-ingestion-api:${var.ingestion_api_image_tag}"
}

resource "aws_lb" "ingestion_api" {
  name               = "${local.name_prefix}-ingestion-api"
  internal           = true
  load_balancer_type = "network"
  subnets            = local.private_subnet_ids

  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = false

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })
}

resource "aws_lb_target_group" "ingestion_api" {
  name        = "${local.name_prefix}-ingestion-api"
  port        = var.ingestion_api_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  deregistration_delay = var.ingestion_api_deregistration_delay_seconds

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = var.ingestion_api_health_check_path
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
    timeout             = 6
    matcher             = "200"
  }

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })
}

resource "aws_lb_listener" "ingestion_api" {
  load_balancer_arn = aws_lb.ingestion_api.arn
  port              = var.ingestion_api_container_port
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ingestion_api.arn
  }

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })
}

# ---------------------------------------------------------------------------
# ingestion-api task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "ingestion_api_task" {
  name        = "${local.name_prefix}-ingestion-api-task"
  description = "Security group for the ingestion-api ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints) plus S3 and DynamoDB gateway-endpoint prefix lists."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-ingestion-api-task"
    Service = "ingestion-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "ingestion_api_task_from_vpc_link" {
  security_group_id            = aws_security_group.ingestion_api_task.id
  description                  = "Allow API Gateway VPC Link to reach the ingestion-api container port."
  from_port                    = var.ingestion_api_container_port
  to_port                      = var.ingestion_api_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "ingestion_api_task_healthcheck" {
  security_group_id = aws_security_group.ingestion_api_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the ingestion-api container port."
  from_port         = var.ingestion_api_container_port
  to_port           = var.ingestion_api_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "ingestion_api_task_egress" {
  security_group_id = aws_security_group.ingestion_api_task.id
  description       = "Allow the ingestion-api task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "ingestion_api_task_egress_s3" {
  security_group_id = aws_security_group.ingestion_api_task.id
  description       = "Allow the ingestion-api task to reach S3 (for ECR layer downloads AND for pre-signed-URL audio-uploads bucket access) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "ingestion_api_task_egress_dynamodb" {
  security_group_id = aws_security_group.ingestion_api_task.id
  description       = "Allow the ingestion-api task to reach DynamoDB (panakoes-dev-ingestion) via the DynamoDB gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.dynamodb.id

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })
}

# ---------------------------------------------------------------------------
# ingestion-api task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "ingestion_api" {
  family                   = "${local.name_prefix}-ingestion-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.ingestion_api_cpu
  memory                   = var.ingestion_api_memory

  execution_role_arn = local.ingestion_api_execution_role_arn
  task_role_arn      = local.ingestion_api_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "ingestion-api"
      image     = local.ingestion_api_image_uri
      essential = true

      portMappings = [
        {
          containerPort = var.ingestion_api_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars match the pydantic-settings schema in
      # services/ingestion-api/src/panakoes_ingestion_api/config.py.
      # Bucket name is pulled from the storage remote state so a
      # bucket-name change (the bucket carries a random_id suffix)
      # propagates here without a code edit.
      environment = [
        { name = "SERVICE_NAME", value = "ingestion-api" },
        { name = "LOG_LEVEL", value = var.ingestion_api_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        { name = "INGESTION_TABLE_NAME", value = data.terraform_remote_state.data.outputs.ingestion_table_name },
        { name = "INGESTION_BUCKET", value = data.terraform_remote_state.storage.outputs.audio_uploads_bucket_name },
        { name = "PRESIGNED_URL_TTL_SECONDS", value = tostring(var.ingestion_api_presigned_url_ttl_seconds) },
        # JWT validator env contract. ingestion-api's pydantic-settings
        # schema uses the AUTH_JWT_* prefix (config.py fields
        # auth_jwt_secret / auth_jwt_issuer / auth_jwt_audience), NOT
        # the JWT_* prefix cost-api / admin-api use. Mismatching this
        # is the same failure mode PR #218 fixed for the other two
        # services.
        { name = "AUTH_JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "AUTH_JWT_AUDIENCE", value = var.auth_jwt_audience },
      ]

      # JWT validation secret. ingestion-api validates JWTs but does not
      # sign them; it has no SQL backend (no DATABASE_URL).
      secrets = [
        {
          name      = "AUTH_JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.ingestion_api_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      # Container-level health check uses python's stdlib urllib so we
      # do not need curl in the runtime image.
      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.ingestion_api_container_port}${var.ingestion_api_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })
}

# ---------------------------------------------------------------------------
# ingestion-api ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "ingestion_api" {
  name            = "${local.name_prefix}-ingestion-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.ingestion_api.arn
  desired_count   = var.ingestion_api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.ingestion_api_task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.ingestion_api.arn
    container_name   = "ingestion-api"
    container_port   = var.ingestion_api_container_port
  }

  health_check_grace_period_seconds = 60

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
  })

  depends_on = [aws_lb_listener.ingestion_api]
}
