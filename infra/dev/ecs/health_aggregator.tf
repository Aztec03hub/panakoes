# ===========================================================================
# health-aggregator service: NLB + target group + listener + SG + task def + service
#
# Tier 1 admin-dashboard backend. Python / FastAPI on ARM64 Fargate. The
# service polls ECS DescribeServices, ELBv2 DescribeTargetHealth, and
# CloudWatch Logs FilterLogEvents to compute per-service health for the
# dashboard. It validates JWTs minted by the auth service using the
# shared `jwt-signing-secret` (read at task start by the execution
# role).
#
# Shape matches cost-api (port 8000, internal NLB, IP target type,
# HTTP /health probe, container SG locked to the API Gateway VPC Link
# SG inbound + VPC-CIDR + S3 prefix list egress, Fargate awsvpc, ARM64
# runtime). Differences from cost-api:
#
#   - No DynamoDB egress rule. health-aggregator's only AWS dependencies
#     are ECS, ELBv2, and CloudWatch Logs, all of which are served via
#     interface VPC endpoints (Logs has an interface endpoint; ECS and
#     ELBv2 control-plane APIs are reached via the regional STS / EC2
#     interface endpoints already provisioned in `infra/dev/vpc-endpoints/`).
#     VPC-CIDR egress + S3-prefix-list egress (for ECR image layer
#     downloads) is sufficient.
# ===========================================================================

locals {
  health_aggregator_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["health-aggregator"]
  health_aggregator_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["health-aggregator"]
  health_aggregator_log_group_name     = data.terraform_remote_state.observability.outputs.log_group_names["health-aggregator"]
  # W2-T3: references the parallel v2 ECR repo on the consolidated app-data CMK. See infra/dev/ecr/main.tf header for the migration pattern.
  health_aggregator_image_uri = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-health-aggregator-v2:${var.health_aggregator_image_tag}"
}

# ---------------------------------------------------------------------------
# health-aggregator task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "health_aggregator_task" {
  name        = "${local.name_prefix}-health-aggregator-task"
  description = "Security group for the health-aggregator ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints) plus the S3 gateway-endpoint prefix list."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-health-aggregator-task"
    Service = "health-aggregator"
  })
}

resource "aws_vpc_security_group_ingress_rule" "health_aggregator_task_from_vpc_link" {
  security_group_id            = aws_security_group.health_aggregator_task.id
  description                  = "Allow API Gateway VPC Link to reach the health-aggregator container port."
  from_port                    = var.health_aggregator_container_port
  to_port                      = var.health_aggregator_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "health-aggregator"
  })
}

resource "aws_vpc_security_group_ingress_rule" "health_aggregator_task_from_alb" {
  security_group_id            = aws_security_group.health_aggregator_task.id
  description                  = "Allow shared ALB to reach the health-aggregator container port."
  from_port                    = var.health_aggregator_container_port
  to_port                      = var.health_aggregator_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = data.terraform_remote_state.alb.outputs.alb_sg_id

  tags = merge(local.common_tags, {
    Service = "health-aggregator"
  })
}

resource "aws_vpc_security_group_ingress_rule" "health_aggregator_task_healthcheck" {
  security_group_id = aws_security_group.health_aggregator_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the health-aggregator container port."
  from_port         = var.health_aggregator_container_port
  to_port           = var.health_aggregator_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "health-aggregator"
  })
}

resource "aws_vpc_security_group_egress_rule" "health_aggregator_task_egress" {
  security_group_id = aws_security_group.health_aggregator_task.id
  description       = "Allow the health-aggregator task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS, ECS, ELBv2) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "health-aggregator"
  })
}

resource "aws_vpc_security_group_egress_rule" "health_aggregator_task_egress_internet" {
  security_group_id = aws_security_group.health_aggregator_task.id
  description       = "Allow HTTPS to the internet for AWS APIs (Secrets Manager, ECR, CloudWatch Logs) -- required on public subnets without VPC interface endpoints."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"

  tags = merge(local.common_tags, {
    Service = "health-aggregator"
  })
}

resource "aws_vpc_security_group_egress_rule" "health_aggregator_task_egress_s3" {
  security_group_id = aws_security_group.health_aggregator_task.id
  description       = "Allow the health-aggregator task to reach S3 (for ECR layer downloads) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "health-aggregator"
  })
}

# ---------------------------------------------------------------------------
# health-aggregator task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "health_aggregator" {
  family                   = "${local.name_prefix}-health-aggregator"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.health_aggregator_cpu
  memory                   = var.health_aggregator_memory

  execution_role_arn = local.health_aggregator_execution_role_arn
  task_role_arn      = local.health_aggregator_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "health-aggregator"
      image     = local.health_aggregator_image_uri
      essential = true

      portMappings = [
        {
          name          = "health-aggregator"
          containerPort = var.health_aggregator_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars match the pydantic-settings schema in
      # services/health-aggregator/src/panakoes_health_aggregator/config.py.
      # ECS_CLUSTER pins the cluster the aggregator polls; the value
      # matches the cluster this module provisions.
      environment = [
        { name = "SERVICE_NAME", value = "health-aggregator" },
        { name = "LOG_LEVEL", value = var.health_aggregator_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        { name = "ECS_CLUSTER", value = local.cluster_name },
        # JWT validator env contract (panakoes_auth_client.from_env reads
        # JWT_SECRET / JWT_ISSUER / JWT_AUDIENCE). Values must match the
        # AUTH_JWT_ISSUER / AUTH_JWT_AUDIENCE the auth service signs with
        # or token validation will fail.
        { name = "JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "JWT_AUDIENCE", value = var.auth_jwt_audience },
      ]

      # JWT validation secret. health-aggregator validates JWTs but does
      # not sign them.
      secrets = [
        {
          name      = "JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.health_aggregator_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      # Container-level health check uses python's stdlib urllib so we
      # do not need curl in the runtime image.
      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.health_aggregator_container_port}${var.health_aggregator_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "health-aggregator"
  })
}

# ---------------------------------------------------------------------------
# health-aggregator ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "health_aggregator" {
  name            = "${local.name_prefix}-health-aggregator"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.health_aggregator.arn
  desired_count   = var.health_aggregator_desired_count

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
    security_groups  = [aws_security_group.health_aggregator_task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = data.terraform_remote_state.alb.outputs.target_group_arns["health-aggregator"]
    container_name   = "health-aggregator"
    container_port   = var.health_aggregator_container_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = data.terraform_remote_state.service_discovery.outputs.namespace_arn

    service {
      port_name      = "health-aggregator"
      discovery_name = "health-aggregator"

      client_alias {
        port     = var.health_aggregator_container_port
        dns_name = "health-aggregator"
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
    Service = "health-aggregator"
  })

  depends_on = [data.terraform_remote_state.alb]
}
