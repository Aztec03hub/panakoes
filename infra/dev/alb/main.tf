# ---------------------------------------------------------------------------
# Security group for the shared internal ALB.
#
# Ingress: port 80 from the VPC CIDR (10.10.0.0/16). This covers the
# API Gateway VPC Link ENIs (in private subnets) and any internal callers.
#
# Egress: all TCP within the VPC CIDR so the ALB can reach ECS task IPs
# on their container ports (8000 for Python services, 8080 for auth) and
# perform health checks. The broad egress range avoids per-port rules and
# is safe because egress is bounded to the VPC CIDR only.
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "panakoes-dev-alb-sg"
  description = "Security group for the shared internal ALB. Allows HTTP from VPC CIDR (10.10.0.0/16), allows all egress to VPC for health checks."
  vpc_id      = data.terraform_remote_state.network.outputs.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "alb_http_from_vpc" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = data.terraform_remote_state.network.outputs.vpc_cidr_block
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP from VPC (covers API Gateway VPC Link ENIs and internal callers)."
}

resource "aws_vpc_security_group_egress_rule" "alb_egress_to_vpc" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = data.terraform_remote_state.network.outputs.vpc_cidr_block
  from_port         = 0
  to_port           = 65535
  ip_protocol       = "tcp"
  description       = "Egress to VPC for health checks and target forwarding."
}

# ---------------------------------------------------------------------------
# The shared internal ALB.
#
# Placed in private subnets to match the VPC Link subnet placement
# (sg-031e19cbcc6c33ea3, subnets: subnet-0569c7f8ed0bd37f4,
# subnet-077b6d21274538423, subnet-03d396f07050b97a0). The VPC Link
# routes traffic to the ALB via private ENIs; the ALB then forwards
# to ECS task IPs (in public subnets) across the VPC fabric.
#
# Deletion protection is off for dev: allows `terraform destroy` to
# remove the ALB without a manual console change.
# ---------------------------------------------------------------------------
resource "aws_lb" "this" {
  name               = "panakoes-dev-alb"
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.terraform_remote_state.network.outputs.private_subnet_ids

  enable_deletion_protection = false
}

# ---------------------------------------------------------------------------
# HTTP listener on port 80. Default action returns a JSON 404 for any
# request that does not match a service path rule.
# ---------------------------------------------------------------------------
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "application/json"
      message_body = "{\"detail\": \"not found\"}"
      status_code  = "404"
    }
  }
}

# ---------------------------------------------------------------------------
# Per-service configuration: target group attributes.
#
# auth runs on port 8080 (TypeScript/Hono); all Python/FastAPI services
# run on port 8000. health-aggregator exposes /healthz (not /health).
#
# deregistration_delay = 30s (vs default 300s) lets ECS task replacement
# cycles complete in ~30s rather than waiting five minutes for connections
# to drain. Appropriate for dev where traffic is low and latency over
# correctness is acceptable.
# ---------------------------------------------------------------------------
locals {
  services = {
    auth = {
      name              = "dev-auth-tg"
      port              = 8080
      health_check_path = "/health"
    }
    admin-api = {
      name              = "dev-admin-api-tg"
      port              = 8000
      health_check_path = "/health"
    }
    billing = {
      name              = "dev-billing-tg"
      port              = 8000
      health_check_path = "/health"
    }
    cost-api = {
      name              = "dev-cost-api-tg"
      port              = 8000
      health_check_path = "/health"
    }
    health-aggregator = {
      name              = "dev-health-aggregator-tg"
      port              = 8000
      health_check_path = "/healthz"
    }
    ingestion-api = {
      name              = "dev-ingestion-api-tg"
      port              = 8000
      health_check_path = "/health"
    }
    query-api = {
      name              = "dev-query-api-tg"
      port              = 8000
      health_check_path = "/health"
    }
    session-manager = {
      name              = "dev-session-manager-tg"
      port              = 8000
      health_check_path = "/health"
    }
    notification = {
      name              = "dev-notification-tg"
      port              = 8000
      health_check_path = "/health"
    }
    summarization = {
      name              = "dev-summarization-tg"
      port              = 8000
      health_check_path = "/health"
    }
    gpu-spawner = {
      name              = "dev-gpu-spawner-tg"
      port              = 8000
      health_check_path = "/health"
    }
  }

  # ---------------------------------------------------------------------------
  # Per-service listener rules: header-based routing priorities.
  #
  # Wave 1 (PR #358): rules now match on the X-Panakoes-Service request
  # header injected by the API Gateway integration, not the incoming path.
  # This decouples the ALB from the gateway's path-stripping behavior:
  # the gateway strips /v1/<service>/ before forwarding, so path-pattern
  # rules would never match the stripped paths; header-based rules work
  # regardless of what the gateway does with the path.
  #
  # The 3 internal-only services (summarization, notification, gpu-spawner)
  # are removed: they use ECS Service Connect for inter-service calls and
  # have no API Gateway route, so no ALB listener rule is needed.
  #
  # Priority values are spaced by 10 to leave room for future inserts.
  # ---------------------------------------------------------------------------
  listener_rules = {
    auth                = { priority = 10 }
    "admin-api"         = { priority = 20 }
    billing             = { priority = 30 }
    "cost-api"          = { priority = 40 }
    "health-aggregator" = { priority = 50 }
    "ingestion-api"     = { priority = 60 }
    "query-api"         = { priority = 70 }
    "session-manager"   = { priority = 80 }
  }
}

# ---------------------------------------------------------------------------
# Target groups (one per ECS service, target_type = "ip").
#
# All use target_type = "ip" because Fargate tasks do not have stable
# instance IDs; ECS registers the task's ENI IP address directly.
#
# Health check settings:
#   healthy_threshold   = 3 (3 consecutive successes to mark healthy)
#   unhealthy_threshold = 3 (3 consecutive failures to mark unhealthy)
#   interval            = 30s
#   timeout             = 5s
#   matcher             = "200" (strict 200-only; services return 200 on /health)
# ---------------------------------------------------------------------------
resource "aws_lb_target_group" "services" {
  for_each = local.services

  name        = each.value.name
  port        = each.value.port
  protocol    = "HTTP"
  vpc_id      = data.terraform_remote_state.network.outputs.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = each.value.health_check_path
    protocol            = "HTTP"
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
    matcher             = "200"
  }

  deregistration_delay = 30
}

# ---------------------------------------------------------------------------
# Listener rules: path-based routing from the HTTP listener to each
# service target group. One rule per service. Any request not matching
# a rule falls through to the listener's default 404 action.
# ---------------------------------------------------------------------------
resource "aws_lb_listener_rule" "services" {
  for_each = local.listener_rules

  listener_arn = aws_lb_listener.http.arn
  priority     = each.value.priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.services[each.key].arn
  }

  # Wave 1: match on the X-Panakoes-Service header injected by the API
  # Gateway integration instead of the incoming path. The gateway strips
  # the /v1/<service>/ prefix before forwarding; the header identifies
  # the target service without depending on the original path shape.
  condition {
    http_header {
      http_header_name = "x-panakoes-service"
      values           = [each.key]
    }
  }
}
