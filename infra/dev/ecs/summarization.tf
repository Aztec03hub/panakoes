# ===========================================================================
# summarization service: NLB + target group + listener + SG + task def + service
#
# Python / FastAPI on ARM64 Fargate. Reads raw transcripts from the
# transcripts S3 bucket, calls the Anthropic Claude API (Haiku 4.5 default,
# Sonnet 4.6 for paid-tier "deep summary"), writes results to the summaries
# S3 bucket and the summaries DynamoDB table. JWT validation is shared
# (HS256) with the rest of the platform.
#
# Mirrors the cost-api / admin-api shape. Differences:
#
#   - ANTHROPIC_API_KEY secret resolved at task start by the execution
#     role (allowlist in infra/dev/iam/main.tf).
#   - Egress includes the S3 prefix list (transcripts + summaries buckets
#     reached via the S3 gateway endpoint, NOT the VPC CIDR) and the
#     DynamoDB prefix list (summaries table).
# ===========================================================================

locals {
  summarization_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["summarization"]
  summarization_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["summarization"]
  summarization_log_group_name     = data.terraform_remote_state.observability.outputs.log_group_names["summarization"]
  summarization_image_uri          = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-summarization:${var.summarization_image_tag}"
  anthropic_api_key_secret_arn     = local.secret_arns["anthropic-api-key"]
}

resource "aws_lb" "summarization" {
  name               = "${local.name_prefix}-summarization"
  internal           = true
  load_balancer_type = "network"
  subnets            = local.private_subnet_ids

  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = false

  tags = merge(local.common_tags, {
    Service = "summarization"
  })
}

resource "aws_lb_target_group" "summarization" {
  name        = "${local.name_prefix}-summarization"
  port        = var.summarization_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  deregistration_delay = var.summarization_deregistration_delay_seconds

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = var.summarization_health_check_path
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
    timeout             = 6
    matcher             = "200"
  }

  tags = merge(local.common_tags, {
    Service = "summarization"
  })
}

resource "aws_lb_listener" "summarization" {
  load_balancer_arn = aws_lb.summarization.arn
  port              = var.summarization_container_port
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.summarization.arn
  }

  tags = merge(local.common_tags, {
    Service = "summarization"
  })
}

# ---------------------------------------------------------------------------
# summarization task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "summarization_task" {
  name        = "${local.name_prefix}-summarization-task"
  description = "Security group for the summarization ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints) plus S3 and DynamoDB gateway-endpoint prefix lists."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-summarization-task"
    Service = "summarization"
  })
}

resource "aws_vpc_security_group_ingress_rule" "summarization_task_from_vpc_link" {
  security_group_id            = aws_security_group.summarization_task.id
  description                  = "Allow API Gateway VPC Link to reach the summarization container port."
  from_port                    = var.summarization_container_port
  to_port                      = var.summarization_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "summarization"
  })
}

resource "aws_vpc_security_group_ingress_rule" "summarization_task_healthcheck" {
  security_group_id = aws_security_group.summarization_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the summarization container port."
  from_port         = var.summarization_container_port
  to_port           = var.summarization_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "summarization"
  })
}

resource "aws_vpc_security_group_egress_rule" "summarization_task_egress" {
  security_group_id = aws_security_group.summarization_task.id
  description       = "Allow the summarization task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "summarization"
  })
}

resource "aws_vpc_security_group_egress_rule" "summarization_task_egress_s3" {
  security_group_id = aws_security_group.summarization_task.id
  description       = "Allow the summarization task to reach S3 (ECR layer downloads + transcripts/summaries buckets) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "summarization"
  })
}

resource "aws_vpc_security_group_egress_rule" "summarization_task_egress_dynamodb" {
  security_group_id = aws_security_group.summarization_task.id
  description       = "Allow the summarization task to reach DynamoDB (summaries table) via the DynamoDB gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.dynamodb.id

  tags = merge(local.common_tags, {
    Service = "summarization"
  })
}

# ---------------------------------------------------------------------------
# summarization task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "summarization" {
  family                   = "${local.name_prefix}-summarization"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.summarization_cpu
  memory                   = var.summarization_memory

  execution_role_arn = local.summarization_execution_role_arn
  task_role_arn      = local.summarization_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "summarization"
      image     = local.summarization_image_uri
      essential = true

      portMappings = [
        {
          containerPort = var.summarization_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars match services/summarization/src/panakoes_summarization/config.py.
      environment = [
        { name = "SERVICE_NAME", value = "summarization" },
        { name = "LOG_LEVEL", value = var.summarization_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        { name = "S3_TRANSCRIPTS_BUCKET", value = "${local.name_prefix}-transcripts" },
        { name = "S3_SUMMARIES_BUCKET", value = "${local.name_prefix}-summaries" },
        { name = "DDB_SUMMARIES_TABLE", value = "${local.name_prefix}-summaries" },
        # JWT validator env contract (JWT_SECRET / JWT_ISSUER / JWT_AUDIENCE,
        # NOT the AUTH_JWT_* prefix; that mismatch caused PR #218).
        { name = "JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "JWT_AUDIENCE", value = var.auth_jwt_audience },
      ]

      secrets = [
        {
          name      = "JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
        {
          name      = "ANTHROPIC_API_KEY"
          valueFrom = local.anthropic_api_key_secret_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.summarization_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.summarization_container_port}${var.summarization_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "summarization"
  })
}

# ---------------------------------------------------------------------------
# summarization ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "summarization" {
  name            = "${local.name_prefix}-summarization"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.summarization.arn
  desired_count   = var.summarization_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.summarization_task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.summarization.arn
    container_name   = "summarization"
    container_port   = var.summarization_container_port
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
    Service = "summarization"
  })

  depends_on = [aws_lb_listener.summarization]
}
