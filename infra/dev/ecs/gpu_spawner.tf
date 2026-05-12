# ===========================================================================
# gpu-spawner service: NLB + target group + listener + SG + task def + service
#
# Python / FastAPI service that issues `ec2:RunInstances` calls to launch
# tagged g4dn.xlarge GPU Spot instances running the streaming
# faster-whisper transcriber AMI (`gpu-transcribe`, pinned in
# `infra/dev/batch/variables.tf`). The session-manager mints a service-
# actor JWT to call the spawn endpoint; the spawn route validates the
# JWT (HS256, shared secret from Secrets Manager) and then performs the
# EC2 launch through the task role's tightly-scoped `ec2:RunInstances`
# grant. The launched instance phones home over WebSocket to the
# session-manager endpoint defined in `SESSION_MANAGER_WS_ENDPOINT`.
#
# Mirrors the cost-api / admin-api / query-api service shape (NLB
# internal-only, IP target type, HTTP /health probe, container SG locked
# to the API Gateway VPC Link SG inbound + VPC-CIDR + S3 prefix list +
# DynamoDB prefix list egress, Fargate awsvpc, ARM64 runtime).
#
# Differences from the read-API services:
#
#   - The task role's primary IAM surface is `ec2:RunInstances` +
#     `ec2:TerminateInstances` + `ec2:CreateTags` + `iam:PassRole` on
#     the gpu-instance role (already wired in `infra/dev/iam/main.tf`;
#     this PR flips the task role's trust principal from
#     `lambda.amazonaws.com` to `ecs-tasks.amazonaws.com` and adds the
#     execution role for ECS image-pull + secrets injection).
#
#   - No DynamoDB read/write today. The service is stateless on the
#     application side; the DDB prefix-list egress rule is still
#     included for parity with the other ECS services so a future
#     spawn-state DDB write does not require a SG round-trip.
#
#   - GPU launch parameters are injected as environment variables
#     (`GPU_AMI_ID`, `GPU_SECURITY_GROUP_ID`, `GPU_SUBNET_ID`,
#     `GPU_INSTANCE_TYPE`, `GPU_IAM_INSTANCE_PROFILE`,
#     `SESSION_MANAGER_WS_ENDPOINT`) rather than baked into the image.
#     `GPU_AMI_ID` resolves through the `gpu_spawner_ami_id` variable
#     (default pinned to the bake from the AMI rotation in
#     `infra/dev/batch/variables.tf`); `GPU_IAM_INSTANCE_PROFILE`
#     consumes the IAM module output `gpu_instance_profile_name`.
# ===========================================================================

locals {
  gpu_spawner_task_role_arn      = data.terraform_remote_state.iam.outputs.task_role_arns["gpu-spawner"]
  gpu_spawner_execution_role_arn = data.terraform_remote_state.iam.outputs.execution_role_arns["gpu-spawner"]
  gpu_spawner_log_group_name     = data.terraform_remote_state.observability.outputs.log_group_names["gpu-spawner"]
  gpu_spawner_image_uri          = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-gpu-spawner:${var.gpu_spawner_image_tag}"
  gpu_instance_profile_name      = data.terraform_remote_state.iam.outputs.gpu_instance_profile_name
}

resource "aws_lb" "gpu_spawner" {
  name               = "${local.name_prefix}-gpu-spawner"
  internal           = true
  load_balancer_type = "network"
  subnets            = local.private_subnet_ids

  enable_cross_zone_load_balancing = true
  enable_deletion_protection       = false

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
  })
}

resource "aws_lb_target_group" "gpu_spawner" {
  name        = "${local.name_prefix}-gpu-spawner"
  port        = var.gpu_spawner_container_port
  protocol    = "TCP"
  target_type = "ip"
  vpc_id      = local.vpc_id

  deregistration_delay = var.gpu_spawner_deregistration_delay_seconds

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = var.gpu_spawner_health_check_path
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
    timeout             = 6
    matcher             = "200"
  }

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
  })
}

resource "aws_lb_listener" "gpu_spawner" {
  load_balancer_arn = aws_lb.gpu_spawner.arn
  port              = var.gpu_spawner_container_port
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gpu_spawner.arn
  }

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
  })
}

# ---------------------------------------------------------------------------
# gpu-spawner task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "gpu_spawner_task" {
  name        = "${local.name_prefix}-gpu-spawner-task"
  description = "Security group for the gpu-spawner ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints including the EC2 API endpoint that ec2:RunInstances hits) plus S3 and DynamoDB gateway-endpoint prefix lists."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-gpu-spawner-task"
    Service = "gpu-spawner"
  })
}

resource "aws_vpc_security_group_ingress_rule" "gpu_spawner_task_from_vpc_link" {
  security_group_id            = aws_security_group.gpu_spawner_task.id
  description                  = "Allow API Gateway VPC Link to reach the gpu-spawner container port."
  from_port                    = var.gpu_spawner_container_port
  to_port                      = var.gpu_spawner_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
  })
}

resource "aws_vpc_security_group_ingress_rule" "gpu_spawner_task_healthcheck" {
  security_group_id = aws_security_group.gpu_spawner_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the gpu-spawner container port."
  from_port         = var.gpu_spawner_container_port
  to_port           = var.gpu_spawner_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
  })
}

resource "aws_vpc_security_group_egress_rule" "gpu_spawner_task_egress" {
  security_group_id = aws_security_group.gpu_spawner_task.id
  description       = "Allow the gpu-spawner task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS, EC2) within the VPC CIDR. The EC2 interface endpoint is what carries the ec2:RunInstances / TerminateInstances API calls without an internet route."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
  })
}

resource "aws_vpc_security_group_egress_rule" "gpu_spawner_task_egress_s3" {
  security_group_id = aws_security_group.gpu_spawner_task.id
  description       = "Allow the gpu-spawner task to reach S3 (for ECR layer downloads) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
  })
}

resource "aws_vpc_security_group_egress_rule" "gpu_spawner_task_egress_dynamodb" {
  security_group_id = aws_security_group.gpu_spawner_task.id
  description       = "Allow the gpu-spawner task to reach DynamoDB via the DynamoDB gateway endpoint prefix list. No DDB writes happen today; included for parity with the other ECS services so a future spawn-state table write does not require a SG amendment."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.dynamodb.id

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
  })
}

# ---------------------------------------------------------------------------
# gpu-spawner task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "gpu_spawner" {
  family                   = "${local.name_prefix}-gpu-spawner"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.gpu_spawner_cpu
  memory                   = var.gpu_spawner_memory

  execution_role_arn = local.gpu_spawner_execution_role_arn
  task_role_arn      = local.gpu_spawner_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "gpu-spawner"
      image     = local.gpu_spawner_image_uri
      essential = true

      portMappings = [
        {
          containerPort = var.gpu_spawner_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars match the pydantic-settings schema in
      # services/gpu-spawner/src/panakoes_gpu_spawner/config.py.
      environment = [
        { name = "SERVICE_NAME", value = "gpu-spawner" },
        { name = "LOG_LEVEL", value = var.gpu_spawner_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        # JWT validator env contract (HS256, shared secret). Issuer +
        # audience must match the auth service's signing claims so token
        # validation succeeds when session-manager calls the spawn route.
        { name = "JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "JWT_AUDIENCE", value = var.auth_jwt_audience },
        # GPU launch parameters injected at deploy time. GPU_AMI_ID is
        # pinned via this module's variable so a Packer-bake refresh
        # rotates the spawner via terraform-apply rather than a code
        # edit; GPU_IAM_INSTANCE_PROFILE consumes the IAM module output
        # so the spawner's iam:PassRole grant stays aligned with the
        # profile it actually attaches.
        { name = "GPU_AMI_ID", value = var.gpu_spawner_ami_id },
        { name = "GPU_INSTANCE_TYPE", value = var.gpu_spawner_instance_type },
        { name = "GPU_SECURITY_GROUP_ID", value = var.gpu_spawner_gpu_security_group_id },
        { name = "GPU_SUBNET_ID", value = var.gpu_spawner_gpu_subnet_id },
        { name = "GPU_IAM_INSTANCE_PROFILE", value = local.gpu_instance_profile_name },
        { name = "SESSION_MANAGER_WS_ENDPOINT", value = var.gpu_spawner_session_manager_ws_endpoint },
        { name = "PROJECT_TAG", value = var.project_name },
        { name = "GPU_SPAWNER_TAG", value = "${local.name_prefix}-gpu-spawner" },
      ]

      # JWT validation secret. gpu-spawner validates JWTs minted by the
      # auth service; it does not sign tokens itself.
      secrets = [
        {
          name      = "JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.gpu_spawner_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      # Container-level health check uses python's stdlib urllib so we
      # do not need curl in the runtime image.
      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.gpu_spawner_container_port}${var.gpu_spawner_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
  })
}

# ---------------------------------------------------------------------------
# gpu-spawner ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "gpu_spawner" {
  name            = "${local.name_prefix}-gpu-spawner"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.gpu_spawner.arn
  desired_count   = var.gpu_spawner_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.private_subnet_ids
    security_groups  = [aws_security_group.gpu_spawner_task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.gpu_spawner.arn
    container_name   = "gpu-spawner"
    container_port   = var.gpu_spawner_container_port
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
    Service = "gpu-spawner"
  })

  depends_on = [aws_lb_listener.gpu_spawner]
}
