# ===========================================================================
# query-api service: NLB + target group + listener + SG + task def + service
#
# Read-only FastAPI surface across the ingestion, summaries, and
# streaming-sessions DDB tables. Python / FastAPI on ARM64 Fargate.
# The task role granted by infra/dev/iam (per `query-api` entry in
# `local.execution_secret_arns` and the dedicated query-api inline
# policy at infra/dev/iam/main.tf:417+) is read-only by design.
#
# Mirrors the cost-api + admin-api shape. Notable shape choices:
#
#   - Container port is 8000 (uvicorn default; matches
#     services/query-api/Dockerfile EXPOSE 8000 + CMD --port 8000).
#   - JWT validator env contract uses the `JWT_*` prefix
#     (services/query-api/.../config.py fields jwt_secret /
#     jwt_issuer / jwt_audience), matching cost-api / admin-api. Note
#     this differs from ingestion-api which uses the `AUTH_JWT_*`
#     prefix.
#   - DDB prefix-list egress is required (query-api reads three DDB
#     tables; without the rule boto3 calls time out at task start).
#   - The `panakoes-dev-summaries` table referenced by config.py is
#     NOT yet provisioned in infra/dev/data/ (only ingestion,
#     audit-log, tenants, api-keys, streaming-sessions are there
#     today). We pass the literal name the service config expects so
#     once the summaries table lands the service already knows where
#     to look. Until then any query-api endpoint that hits summaries
#     will return a runtime ResourceNotFoundException; the list +
#     ingestion + sessions endpoints are unaffected.
# ===========================================================================

locals {
  query_api_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["query-api"]
  query_api_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["query-api"]
  query_api_log_group_name     = data.terraform_remote_state.observability.outputs.log_group_names["query-api"]
  query_api_image_uri          = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-query-api:${var.query_api_image_tag}"
}

resource "aws_lb" "query_api" {
  name               = "${local.name_prefix}-query-api"
  internal           = true
  load_balancer_type = "network"
  subnets            = local.private_subnet_ids

  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = false

  tags = merge(local.common_tags, {
    Service = "query-api"
  })
}

resource "aws_lb_target_group" "query_api" {
  name        = "${local.name_prefix}-query-api"
  port        = var.query_api_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  deregistration_delay = var.query_api_deregistration_delay_seconds

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = var.query_api_health_check_path
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
    timeout             = 6
    matcher             = "200"
  }

  tags = merge(local.common_tags, {
    Service = "query-api"
  })
}

resource "aws_lb_listener" "query_api" {
  load_balancer_arn = aws_lb.query_api.arn
  port              = var.query_api_container_port
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.query_api.arn
  }

  tags = merge(local.common_tags, {
    Service = "query-api"
  })
}

# ---------------------------------------------------------------------------
# query-api task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "query_api_task" {
  name        = "${local.name_prefix}-query-api-task"
  description = "Security group for the query-api ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints) plus S3 and DynamoDB gateway-endpoint prefix lists."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-query-api-task"
    Service = "query-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "query_api_task_from_vpc_link" {
  security_group_id            = aws_security_group.query_api_task.id
  description                  = "Allow API Gateway VPC Link to reach the query-api container port."
  from_port                    = var.query_api_container_port
  to_port                      = var.query_api_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "query-api"
  })
}

resource "aws_vpc_security_group_ingress_rule" "query_api_task_healthcheck" {
  security_group_id = aws_security_group.query_api_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the query-api container port."
  from_port         = var.query_api_container_port
  to_port           = var.query_api_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "query-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "query_api_task_egress" {
  security_group_id = aws_security_group.query_api_task.id
  description       = "Allow the query-api task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "query-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "query_api_task_egress_s3" {
  security_group_id = aws_security_group.query_api_task.id
  description       = "Allow the query-api task to reach S3 (for ECR layer downloads) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "query-api"
  })
}

resource "aws_vpc_security_group_egress_rule" "query_api_task_egress_dynamodb" {
  security_group_id = aws_security_group.query_api_task.id
  description       = "Allow the query-api task to reach DynamoDB (ingestion, summaries, streaming-sessions) via the DynamoDB gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.dynamodb.id

  tags = merge(local.common_tags, {
    Service = "query-api"
  })
}

# ---------------------------------------------------------------------------
# query-api task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "query_api" {
  family                   = "${local.name_prefix}-query-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.query_api_cpu
  memory                   = var.query_api_memory

  execution_role_arn = local.query_api_execution_role_arn
  task_role_arn      = local.query_api_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "query-api"
      image     = local.query_api_image_uri
      essential = true

      portMappings = [
        {
          containerPort = var.query_api_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars match the pydantic-settings schema in
      # services/query-api/src/panakoes_query_api/config.py. Ingestion
      # + sessions table names pull from the data module's outputs; the
      # summaries table is not yet provisioned (see header comment) and
      # is passed as the literal name the service config expects.
      environment = [
        { name = "SERVICE_NAME", value = "query-api" },
        { name = "LOG_LEVEL", value = var.query_api_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        { name = "DDB_INGESTION_TABLE", value = data.terraform_remote_state.data.outputs.ingestion_table_name },
        { name = "DDB_SUMMARIES_TABLE", value = "${local.name_prefix}-summaries" },
        { name = "DDB_SESSIONS_TABLE", value = data.terraform_remote_state.data.outputs.streaming_sessions_table_name },
        # JWT validator env contract. query-api's pydantic-settings
        # schema uses the JWT_* prefix (config.py fields jwt_secret /
        # jwt_issuer / jwt_audience), matching cost-api / admin-api.
        { name = "JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "JWT_AUDIENCE", value = var.auth_jwt_audience },
      ]

      # JWT validation secret. query-api validates JWTs but does not
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
          awslogs-group         = local.query_api_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.query_api_container_port}${var.query_api_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "query-api"
  })
}

# ---------------------------------------------------------------------------
# query-api ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "query_api" {
  name            = "${local.name_prefix}-query-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.query_api.arn
  desired_count   = var.query_api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.query_api_task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.query_api.arn
    container_name   = "query-api"
    container_port   = var.query_api_container_port
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
    Service = "query-api"
  })

  depends_on = [aws_lb_listener.query_api]
}
