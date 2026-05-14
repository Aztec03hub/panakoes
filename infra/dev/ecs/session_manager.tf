# ===========================================================================
# session-manager service: NLB + target group + listener + SG + task def + service
#
# Python / FastAPI on ARM64 Fargate. Owns streaming-session lifecycle
# rows in DynamoDB (`panakoes-dev-streaming-sessions`); spawns and
# tracks the per-session g4dn GPU workers via separate orchestration.
# JWT-validates every request against the shared HS256 secret.
#
# Mirrors the cost-api / admin-api shape. State is DynamoDB-only; no
# S3 or external API calls needed at the data-plane layer (image pull
# from S3 still requires the S3 prefix-list egress rule).
# ===========================================================================

locals {
  session_manager_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["session-manager"]
  session_manager_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["session-manager"]
  session_manager_log_group_name     = data.terraform_remote_state.observability.outputs.log_group_names["session-manager"]
  session_manager_image_uri          = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-session-manager:${var.session_manager_image_tag}"
}

resource "aws_lb" "session_manager" {
  name               = "${local.name_prefix}-session-manager"
  internal           = true
  load_balancer_type = "network"
  subnets            = local.private_subnet_ids

  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = false

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

resource "aws_lb_target_group" "session_manager" {
  name        = "${local.name_prefix}-session-manager"
  port        = var.session_manager_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  deregistration_delay = var.session_manager_deregistration_delay_seconds

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = var.session_manager_health_check_path
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
    timeout             = 6
    matcher             = "200"
  }

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

resource "aws_lb_listener" "session_manager" {
  load_balancer_arn = aws_lb.session_manager.arn
  port              = var.session_manager_container_port
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.session_manager.arn
  }

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

# ---------------------------------------------------------------------------
# session-manager task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "session_manager_task" {
  name        = "${local.name_prefix}-session-manager-task"
  description = "Security group for the session-manager ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints) plus S3 and DynamoDB gateway-endpoint prefix lists."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-session-manager-task"
    Service = "session-manager"
  })
}

resource "aws_vpc_security_group_ingress_rule" "session_manager_task_from_vpc_link" {
  security_group_id            = aws_security_group.session_manager_task.id
  description                  = "Allow API Gateway VPC Link to reach the session-manager container port."
  from_port                    = var.session_manager_container_port
  to_port                      = var.session_manager_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

resource "aws_vpc_security_group_ingress_rule" "session_manager_task_from_alb" {
  security_group_id            = aws_security_group.session_manager_task.id
  description                  = "Allow shared ALB to reach the session-manager container port."
  from_port                    = var.session_manager_container_port
  to_port                      = var.session_manager_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = data.terraform_remote_state.alb.outputs.alb_sg_id

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

resource "aws_vpc_security_group_ingress_rule" "session_manager_task_healthcheck" {
  security_group_id = aws_security_group.session_manager_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the session-manager container port."
  from_port         = var.session_manager_container_port
  to_port           = var.session_manager_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

resource "aws_vpc_security_group_egress_rule" "session_manager_task_egress" {
  security_group_id = aws_security_group.session_manager_task.id
  description       = "Allow the session-manager task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

resource "aws_vpc_security_group_egress_rule" "session_manager_task_egress_s3" {
  security_group_id = aws_security_group.session_manager_task.id
  description       = "Allow the session-manager task to reach S3 (for ECR layer downloads) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

resource "aws_vpc_security_group_egress_rule" "session_manager_task_egress_dynamodb" {
  security_group_id = aws_security_group.session_manager_task.id
  description       = "Allow the session-manager task to reach DynamoDB (streaming-sessions table) via the DynamoDB gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.dynamodb.id

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

# ---------------------------------------------------------------------------
# session-manager task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "session_manager" {
  family                   = "${local.name_prefix}-session-manager"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.session_manager_cpu
  memory                   = var.session_manager_memory

  execution_role_arn = local.session_manager_execution_role_arn
  task_role_arn      = local.session_manager_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "session-manager"
      image     = local.session_manager_image_uri
      essential = true

      portMappings = [
        {
          name          = "session-manager"
          containerPort = var.session_manager_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars target the project-standard JWT contract
      # (JWT_SECRET / JWT_ISSUER / JWT_AUDIENCE; NEVER the AUTH_JWT_* prefix,
      # see PR #218). services/session-manager/src/panakoes_session_manager/config.py
      # currently reads AUTH_JWT_* and needs a follow-up rename to align
      # with the platform contract; tracked in the run report.
      environment = [
        { name = "SERVICE_NAME", value = "session-manager" },
        { name = "LOG_LEVEL", value = var.session_manager_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        { name = "SESSIONS_TABLE_NAME", value = "${local.name_prefix}-streaming-sessions" },
        { name = "JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "JWT_AUDIENCE", value = var.auth_jwt_audience },
      ]

      secrets = [
        {
          name      = "JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.session_manager_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.session_manager_container_port}${var.session_manager_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "session-manager"
  })
}

# ---------------------------------------------------------------------------
# session-manager ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "session_manager" {
  name            = "${local.name_prefix}-session-manager"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.session_manager.arn
  desired_count   = var.session_manager_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.public_subnet_ids
    security_groups  = [aws_security_group.session_manager_task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.session_manager.arn
    container_name   = "session-manager"
    container_port   = var.session_manager_container_port
  }

  load_balancer {
    target_group_arn = data.terraform_remote_state.alb.outputs.target_group_arns["session-manager"]
    container_name   = "session-manager"
    container_port   = var.session_manager_container_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = data.terraform_remote_state.service_discovery.outputs.namespace_arn

    service {
      port_name      = "session-manager"
      discovery_name = "session-manager"

      client_alias {
        port     = var.session_manager_container_port
        dns_name = "session-manager"
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
    Service = "session-manager"
  })

  depends_on = [aws_lb_listener.session_manager, data.terraform_remote_state.alb]
}
