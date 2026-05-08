locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "dev-batch"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # Pulled-from-remote-state values. Captured into locals so the
  # downstream resource blocks read short names rather than deep
  # traversals and so the contract with each upstream module is
  # documented in one place.
  vpc_id             = data.terraform_remote_state.network.outputs.vpc_id
  private_subnet_ids = data.terraform_remote_state.network.outputs.private_subnet_ids

  transcriber_batch_role_arn = data.terraform_remote_state.iam.outputs.task_role_arns["transcriber-batch"]
  gpu_instance_profile_arn   = data.terraform_remote_state.iam.outputs.gpu_instance_profile_arn

  # Default S3 buckets the transcriber-batch container reads from and
  # writes to. Constructed by name (not pulled from storage remote
  # state) so this module stays a leaf consumer of network + iam +
  # ecr only; the transcriber container itself is the source of truth
  # on what these env vars resolve to.
  s3_input_bucket  = "${local.name_prefix}-audio-uploads"
  s3_output_bucket = "${local.name_prefix}-transcripts"
}

# ===========================================================================
# IAM: AWS Batch service role
#
# The Batch service uses this role to call EC2, ECS, and Auto Scaling
# on the caller's behalf when it provisions the compute environment.
# The AWS-managed policy `AWSBatchServiceRole` is the documented
# minimum and changes infrequently.
# ===========================================================================

data "aws_iam_policy_document" "batch_service_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["batch.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "batch_service" {
  name                 = "${local.name_prefix}-batch-service"
  description          = "Service role AWS Batch assumes to manage the dev transcription compute environment."
  assume_role_policy   = data.aws_iam_policy_document.batch_service_trust.json
  max_session_duration = 3600

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "batch_service" {
  role       = aws_iam_role.batch_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"
}

# ===========================================================================
# IAM: EC2 Spot Fleet role
#
# Required by AWS Batch when allocation_strategy is one of the
# `SPOT_*` family. The role lets the Spot Fleet service tag, launch,
# and terminate the EC2 instances Batch fans out under the compute
# environment. The AWS-managed policy
# `AmazonEC2SpotFleetTaggingRole` is the documented minimum.
# ===========================================================================

data "aws_iam_policy_document" "spot_fleet_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["spotfleet.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "spot_fleet" {
  name                 = "${local.name_prefix}-batch-spot-fleet"
  description          = "EC2 Spot Fleet role used by the dev Batch compute environment for tagging and lifecycle of Spot instances."
  assume_role_policy   = data.aws_iam_policy_document.spot_fleet_trust.json
  max_session_duration = 3600

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "spot_fleet" {
  role       = aws_iam_role.spot_fleet.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole"
}

# ===========================================================================
# Security group for the Batch compute environment
#
# No inbound rules. Egress allowed to the world so the GPU instances
# can pull container images from ECR, fetch model weights from S3,
# and reach the AWS API. The compute environment lives in private
# subnets so this egress is NAT-bound, not directly internet-facing.
# ===========================================================================

resource "aws_security_group" "batch" {
  name        = "${local.name_prefix}-batch-compute"
  description = "Security group for the dev Batch GPU compute environment. No inbound; egress all (NAT-bound)."
  vpc_id      = local.vpc_id

  egress {
    description = "Egress to AWS service endpoints, ECR, and S3 via the VPC NAT gateway."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-batch-compute"
  })
}

# ===========================================================================
# AWS Batch compute environment
#
# Type MANAGED with allocation_strategy SPOT_CAPACITY_OPTIMIZED so the
# Spot fleet picks pools least likely to be interrupted, which matters
# for transcription jobs that take minutes per audio hour. Min/desired
# vCPUs at 0 means the environment scales to zero when the queue is
# idle: pure pay-per-use, no idle GPU bill. Max 16 vCPUs caps the
# blast radius of a runaway dispatch (4x g4dn.xlarge worth of Spot
# capacity).
#
# The compute environment references the GPU AMI by ID (var.gpu_ami_id).
# Today that is a placeholder; the AMI build ships in a follow-up that
# wires Whisper-large-v3 fp16, CUDA, and the transcriber-batch
# container runtime onto a hardened base.
# ===========================================================================

resource "aws_batch_compute_environment" "transcribe" {
  name         = "${local.name_prefix}-transcribe"
  type         = "MANAGED"
  state        = "ENABLED"
  service_role = aws_iam_role.batch_service.arn

  compute_resources {
    type                = "SPOT"
    allocation_strategy = "SPOT_CAPACITY_OPTIMIZED"

    min_vcpus     = 0
    desired_vcpus = 0
    max_vcpus     = 16

    instance_type = ["g4dn.xlarge"]
    image_id      = var.gpu_ami_id

    subnets            = local.private_subnet_ids
    security_group_ids = [aws_security_group.batch.id]

    instance_role       = local.gpu_instance_profile_arn
    spot_iam_fleet_role = aws_iam_role.spot_fleet.arn

    tags = merge(local.common_tags, {
      Name = "${local.name_prefix}-transcribe-instance"
    })
  }

  tags = local.common_tags

  # The Batch service updates `desired_vcpus` autonomously as jobs
  # arrive and complete. Keeping it under Terraform management would
  # cause permanent plan drift. Ignore it after creation.
  lifecycle {
    ignore_changes = [compute_resources[0].desired_vcpus]
  }

  depends_on = [
    aws_iam_role_policy_attachment.batch_service,
    aws_iam_role_policy_attachment.spot_fleet,
  ]
}

# ===========================================================================
# Job queue
#
# Single queue at priority 1 attached to the transcribe compute
# environment. Higher-priority queues for paid-tier customers can land
# later; at slice 1 we run a single FIFO queue.
# ===========================================================================

resource "aws_batch_job_queue" "transcribe" {
  name     = "${local.name_prefix}-transcribe-queue"
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.transcribe.arn
  }

  tags = local.common_tags
}

# ===========================================================================
# Job definition
#
# Container-type job pinned to 4 vCPUs / 15000 MiB / 1 GPU on the
# g4dn.xlarge host (which carries 4 vCPU / 16 GiB / 1 NVIDIA T4). The
# 15000 MiB cap leaves ~1 GiB headroom for the host's ECS agent and
# kernel; setting memory equal to the host total is a known
# Batch-on-EC2 footgun that keeps jobs RUNNABLE forever.
#
# Image is the transcriber-batch ECR repository's `:latest` tag,
# resolved with a try() against the ECR remote state so this module
# can validate before that module is applied. Job role
# (transcriber-batch task role) provides the runtime identity the
# container assumes (S3 read on audio-uploads, S3 write on
# transcripts, DDB UpdateItem on the ingestion table, KMS on both
# bucket CMKs).
# ===========================================================================

resource "aws_batch_job_definition" "transcribe" {
  name = "${local.name_prefix}-transcribe-batch"
  type = "container"

  container_properties = jsonencode({
    image      = local.transcriber_batch_image_uri
    jobRoleArn = local.transcriber_batch_role_arn
    vcpus      = 4
    memory     = 15000

    resourceRequirements = [
      {
        type  = "GPU"
        value = "1"
      },
    ]

    environment = [
      {
        name  = "S3_INPUT_BUCKET"
        value = local.s3_input_bucket
      },
      {
        name  = "S3_OUTPUT_BUCKET"
        value = local.s3_output_bucket
      },
      {
        name  = "MODEL_PATH"
        value = "/opt/whisper/models/large-v3.pt"
      },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = "/aws/batch/${local.name_prefix}-transcribe"
        awslogs-region        = data.aws_region.current.region
        awslogs-stream-prefix = "transcribe"
      }
    }
  })

  tags = local.common_tags
}

# CloudWatch log group the job definition's awslogs driver targets.
# Created here so the first job run does not race the agent on
# CreateLogGroup.
resource "aws_cloudwatch_log_group" "batch" {
  name              = "/aws/batch/${local.name_prefix}-transcribe"
  retention_in_days = 30

  tags = local.common_tags
}

# ===========================================================================
# Operational alerting: SNS topic + CloudWatch alarm on FailedJobs
#
# The system-alerts SNS topic is the project-wide destination for
# CloudWatch alarm notifications. No other module creates it today,
# so this module owns it; future alarm-emitting modules should
# `terraform_remote_state` it from `dev/batch/` rather than re-create
# it.
#
# The FailedJobs alarm trips on any failed Batch job in a 5-minute
# window. Treat-missing-data = `notBreaching` so a stretch with zero
# job activity does not page (no jobs == no failures).
# ===========================================================================

resource "aws_sns_topic" "system_alerts" {
  name = "${local.name_prefix}-system-alerts"

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "failed_jobs" {
  alarm_name          = "${local.name_prefix}-batch-failed-jobs"
  alarm_description   = "Fires when one or more transcription Batch jobs fail in any 5-minute window. Investigate via the /aws/batch/${local.name_prefix}-transcribe log group and the Batch console."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FailedJobs"
  namespace           = "AWS/Batch"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    JobQueue = aws_batch_job_queue.transcribe.name
  }

  alarm_actions = [aws_sns_topic.system_alerts.arn]
  ok_actions    = [aws_sns_topic.system_alerts.arn]

  tags = local.common_tags
}
