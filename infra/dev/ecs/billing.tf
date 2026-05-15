# ===========================================================================
# billing service: NLB + target group + listener + SG + task def + service
#
# Python / FastAPI on ARM64 Fargate. Stripe TEST-mode integration for
# checkout sessions, subscription state, and webhook ingest. Persists
# billing events in DynamoDB.
#
# Mirrors the cost-api / admin-api shape. Differences:
#
#   - Resolves three secrets at startup: jwt-signing-secret, stripe-test-key,
#     stripe-webhook-signing-secret (per the IAM allowlist in
#     infra/dev/iam/main.tf).
#   - Egress allows VPC CIDR (interface endpoints), the S3 prefix list
#     (ECR layer downloads), and the DynamoDB prefix list (billing-events
#     table). Stripe API calls leave via the NAT-less VPC through Internet
#     access provisioned separately at the platform layer; this slice
#     ships with the same egress shape as the other Python services, and
#     a follow-up will add an explicit egress rule covering the Stripe
#     API endpoint range if/when egress tightening lands.
# ===========================================================================

locals {
  billing_task_role_arn         = data.terraform_remote_state.iam.outputs.task_role_arns["billing"]
  billing_execution_role_arn    = data.terraform_remote_state.iam.outputs.execution_role_arns["billing"]
  billing_log_group_name        = data.terraform_remote_state.observability.outputs.log_group_names["billing"]
  billing_image_uri             = "${local.ecr_account_id}.dkr.ecr.${data.aws_region.current.region}.amazonaws.com/${local.name_prefix}-billing:${var.billing_image_tag}"
  stripe_test_key_secret_arn    = local.secret_arns["stripe-test-key"]
  stripe_webhook_secret_arn_val = local.secret_arns["stripe-webhook-signing-secret"]
}

# ---------------------------------------------------------------------------
# billing task security group
# ---------------------------------------------------------------------------

resource "aws_security_group" "billing_task" {
  name        = "${local.name_prefix}-billing-task"
  description = "Security group for the billing ECS Fargate tasks. Inbound from the API Gateway VPC Link SG only; egress to the VPC CIDR (interface endpoints) plus S3 and DynamoDB gateway-endpoint prefix lists."
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-billing-task"
    Service = "billing"
  })
}

resource "aws_vpc_security_group_ingress_rule" "billing_task_from_vpc_link" {
  security_group_id            = aws_security_group.billing_task.id
  description                  = "Allow API Gateway VPC Link to reach the billing container port."
  from_port                    = var.billing_container_port
  to_port                      = var.billing_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = local.api_gateway_vpc_link_sg_id

  tags = merge(local.common_tags, {
    Service = "billing"
  })
}

resource "aws_vpc_security_group_ingress_rule" "billing_task_from_alb" {
  security_group_id            = aws_security_group.billing_task.id
  description                  = "Allow shared ALB to reach the billing container port."
  from_port                    = var.billing_container_port
  to_port                      = var.billing_container_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = data.terraform_remote_state.alb.outputs.alb_sg_id

  tags = merge(local.common_tags, {
    Service = "billing"
  })
}

resource "aws_vpc_security_group_ingress_rule" "billing_task_healthcheck" {
  security_group_id = aws_security_group.billing_task.id
  description       = "Allow NLB health checks (originate from inside the VPC) on the billing container port."
  from_port         = var.billing_container_port
  to_port           = var.billing_container_port
  ip_protocol       = "tcp"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "billing"
  })
}

resource "aws_vpc_security_group_egress_rule" "billing_task_egress" {
  security_group_id = aws_security_group.billing_task.id
  description       = "Allow the billing task to reach interface VPC endpoints (Secrets Manager, ECR, Logs, STS, KMS) within the VPC CIDR."
  ip_protocol       = "-1"
  cidr_ipv4         = local.vpc_cidr_block

  tags = merge(local.common_tags, {
    Service = "billing"
  })
}

resource "aws_vpc_security_group_egress_rule" "billing_task_egress_s3" {
  security_group_id = aws_security_group.billing_task.id
  description       = "Allow the billing task to reach S3 (for ECR layer downloads) via the S3 gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.s3.id

  tags = merge(local.common_tags, {
    Service = "billing"
  })
}

resource "aws_vpc_security_group_egress_rule" "billing_task_egress_dynamodb" {
  security_group_id = aws_security_group.billing_task.id
  description       = "Allow the billing task to reach DynamoDB (billing-events table) via the DynamoDB gateway endpoint prefix list."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = data.aws_prefix_list.dynamodb.id

  tags = merge(local.common_tags, {
    Service = "billing"
  })
}

resource "aws_vpc_security_group_egress_rule" "billing_task_egress_internet" {
  security_group_id = aws_security_group.billing_task.id
  description       = "Allow HTTPS to the internet for AWS APIs (Secrets Manager, ECR, CloudWatch Logs) -- required on public subnets without VPC interface endpoints."
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"

  tags = merge(local.common_tags, {
    Service = "billing"
  })
}

# ---------------------------------------------------------------------------
# billing task definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "billing" {
  family                   = "${local.name_prefix}-billing"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.billing_cpu
  memory                   = var.billing_memory

  execution_role_arn = local.billing_execution_role_arn
  task_role_arn      = local.billing_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "billing"
      image     = local.billing_image_uri
      essential = true

      portMappings = [
        {
          name          = "billing"
          containerPort = var.billing_container_port
          protocol      = "tcp"
        }
      ]

      # Env vars match services/billing/src/panakoes_billing/config.py.
      environment = [
        { name = "SERVICE_NAME", value = "billing" },
        { name = "LOG_LEVEL", value = var.billing_log_level },
        { name = "AWS_REGION", value = data.aws_region.current.region },
        { name = "DDB_BILLING_TABLE", value = "${local.name_prefix}-billing-events" },
        # JWT validator env contract (JWT_SECRET / JWT_ISSUER / JWT_AUDIENCE,
        # NOT the AUTH_JWT_* prefix; that mismatch caused PR #218).
        { name = "JWT_ISSUER", value = var.auth_jwt_issuer },
        { name = "JWT_AUDIENCE", value = var.auth_jwt_audience },
      ]

      # Stripe secrets are mapped to the env-var names the app config
      # expects: STRIPE_API_KEY (from `panakoes-dev/stripe-test-key`) and
      # STRIPE_WEBHOOK_SECRET (from `panakoes-dev/stripe-webhook-signing-secret`).
      # The config validator rejects any value not starting with sk_test_
      # so a misrotation to a live key fails the container at startup.
      secrets = [
        {
          name      = "JWT_SECRET"
          valueFrom = local.jwt_signing_secret_arn
        },
        {
          name      = "STRIPE_API_KEY"
          valueFrom = local.stripe_test_key_secret_arn
        },
        {
          name      = "STRIPE_WEBHOOK_SECRET"
          valueFrom = local.stripe_webhook_secret_arn_val
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = local.billing_log_group_name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = "ecs"
        }
      }

      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.billing_container_port}${var.billing_health_check_path}', timeout=3).status == 200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }
    }
  ])

  tags = merge(local.common_tags, {
    Service = "billing"
  })
}

# ---------------------------------------------------------------------------
# billing ECS service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "billing" {
  name            = "${local.name_prefix}-billing"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.billing.arn
  desired_count   = var.billing_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.public_subnet_ids
    security_groups  = [aws_security_group.billing_task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = data.terraform_remote_state.alb.outputs.target_group_arns["billing"]
    container_name   = "billing"
    container_port   = var.billing_container_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = data.terraform_remote_state.service_discovery.outputs.namespace_arn

    service {
      port_name      = "billing"
      discovery_name = "billing"

      client_alias {
        port     = var.billing_container_port
        dns_name = "billing"
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
    Service = "billing"
  })

  depends_on = [data.terraform_remote_state.alb]
}
