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

resource "aws_lb" "cost_api" {
  name               = "${local.name_prefix}-cost-api"
  internal           = true
  load_balancer_type = "network"
  subnets            = local.private_subnet_ids

  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = false

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

resource "aws_lb_target_group" "cost_api" {
  name        = "${local.name_prefix}-cost-api"
  port        = var.cost_api_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  deregistration_delay = var.cost_api_deregistration_delay_seconds

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = var.cost_api_health_check_path
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
    timeout             = 6
    matcher             = "200"
  }

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
}

resource "aws_lb_listener" "cost_api" {
  load_balancer_arn = aws_lb.cost_api.arn
  port              = var.cost_api_container_port
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.cost_api.arn
  }

  tags = merge(local.common_tags, {
    Service = "cost-api"
  })
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
        { name = "COST_CACHE_TABLE", value = "${local.name_prefix}-cost-cache" },
        { name = "TENANT_COST_ROLLUP_TABLE", value = "${local.name_prefix}-tenant-cost-rollup" },
        { name = "ALERT_STATE_TABLE", value = "${local.name_prefix}-alert-state" },
        { name = "AUDIT_LOG_TABLE", value = "${local.name_prefix}-audit-log" },
      ]

      # Only the JWT signing secret. cost-api validates JWTs but does
      # not sign them; it has no SQL backend (no DATABASE_URL).
      secrets = [
        {
          name      = "AUTH_JWT_SECRET"
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
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.cost_api_task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.cost_api.arn
    container_name   = "cost-api"
    container_port   = var.cost_api_container_port
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
    Service = "cost-api"
  })

  depends_on = [aws_lb_listener.cost_api]
}
