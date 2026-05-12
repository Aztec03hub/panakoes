# ===========================================================================
# notification service: NLB + target group + listener + SG + task def + service
#
# Python / FastAPI on ARM64 Fargate. Sends transactional email via SES
# (SMTP creds resolved at task start from `panakoes-dev/ses-smtp-credentials`)
# and persists outbound notification + webhook delivery state in DynamoDB.
# Mirrors the cost-api / admin-api shape.
# ===========================================================================

locals {
  notification_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["notification"]
  notification_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["notification"]
  notification_log_group_name     = data.terraform_remote_state.observability.outputs.log_group_names["notification"]
  notification_image_uri          = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-notification:${var.notification_image_tag}"
  ses_smtp_secret_arn             = local.secret_arns["ses-smtp-credentials"]
}

resource "aws_lb" "notification" {
  name               = "${local.name_prefix}-notification"
  internal           = true
  load_balancer_type = "network"
  subnets            = local.private_subnet_ids

  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = false

  tags = merge(local.common_tags, {
    Service = "notification"
  })
}

resource "aws_lb_target_group" "notification" {
  name        = "${local.name_prefix}-notification"
  port        = var.notification_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  deregistration_delay = var.notification_deregistration_delay_seconds

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = var.notification_health_check_path
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
    timeout             = 6
    matcher             = "200"
  }

  tags = merge(local.common_tags, {
    Service = "notification"
  })
}

resource "aws_lb_listener" "notification" {
  load_balancer_arn = aws_lb.notification.arn
  port              = var.notification_container_port
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.notification.arn
  }

  tags = merge(local.common_tags, {
    Service = "notification"
  })
}

# ---------------------------------------------------------------------------
# notification task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "notification_task" {
  name        = "${local.name_prefix}-notification-task"
  description = "Security group for the notification ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints) plus S3 and DynamoDB gateway-endpoint prefix lists."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-notification-task"
    Service = "notification"
  })
}

resource "aws_vpc_security_group_ingress_rule" "notification_task_from_vpc_link" {
  security_group_id            = aws_security_group.notification_task.id
  description                  = "Allow API Gateway VPC Link to reach the notification container port."
  from_port                    = var.notification_container_port
  to_port                      = var.notification_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "notification"
  })
}

resource "aws_vpc_security_group_ingress_rule" "notification_task_healthcheck" {
  security_group_id = aws_security_group.notification_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the notification container port."
  from_port         = var.notification_container_port
  to_port           = var.notification_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "notification"
  })
}

resource "aws_vpc_security_group_egress_rule" "notification_task_egress" {
  security_group_id = aws_security_group.notification_task.id
  description       = "Allow the notification task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS, SES) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "notification"
  })
}

resource "aws_vpc_security_group_egress_rule" "notification_task_egress_s3" {
  security_group_id = aws_security_group.notification_task.id
  description       = "Allow the notification task to reach S3 (for ECR layer downloads) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "notification"
  })
}

resource "aws_vpc_security_group_egress_rule" "notification_task_egress_dynamodb" {
  security_group_id = aws_security_group.notification_task.id
  description       = "Allow the notification task to reach DynamoDB (notification table) via the DynamoDB gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.dynamodb.id

  tags = merge(local.common_tags, {
    Service = "notification"
  })
}

# ---------------------------------------------------------------------------
# notification task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "notification" {
  family                   = "${local.name_prefix}-notification"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.notification_cpu
  memory                   = var.notification_memory

  execution_role_arn = local.notification_execution_role_arn
  task_role_arn      = local.notification_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "notification"
      image     = local.notification_image_uri
      essential = true

      portMappings = [
        {
          containerPort = var.notification_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars match services/notification/src/panakoes_notification/config.py.
      environment = [
        { name = "SERVICE_NAME", value = "notification" },
        { name = "LOG_LEVEL", value = var.notification_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        { name = "SES_FROM_ADDRESS", value = var.notification_ses_from_address },
        { name = "DDB_NOTIFICATION_TABLE", value = "${local.name_prefix}-notification" },
        # JWT validator env contract (JWT_SECRET / JWT_ISSUER / JWT_AUDIENCE,
        # NOT the AUTH_JWT_* prefix; that mismatch caused PR #218).
        { name = "JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "JWT_AUDIENCE", value = var.auth_jwt_audience },
      ]

      # SES_SMTP is a JSON-shaped secret (username + password). The
      # application reads the full JSON via SES_SMTP env var and parses
      # locally; the SES SMTP endpoint itself is reached from the VPC
      # without a Secrets Manager round-trip after startup.
      secrets = [
        {
          name      = "JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
        {
          name      = "SES_SMTP"
          valueFrom = local.ses_smtp_secret_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.notification_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.notification_container_port}${var.notification_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "notification"
  })
}

# ---------------------------------------------------------------------------
# notification ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "notification" {
  name            = "${local.name_prefix}-notification"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.notification.arn
  desired_count   = var.notification_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.notification_task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.notification.arn
    container_name   = "notification"
    container_port   = var.notification_container_port
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
    Service = "notification"
  })

  depends_on = [aws_lb_listener.notification]
}
