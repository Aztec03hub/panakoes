variable "aws_region" {
  description = "AWS region for the dev environment IAM resources. IAM is global but the AWS provider still requires a region; resource ARNs that include a region (Secrets Manager, EC2, Lambda, EventBridge) use this value."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name used for tagging and resource naming."
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used for resource naming and tagging."
  type        = string
  default     = "panakoes"
}

variable "ses_sending_domain" {
  description = "SES verified domain that the notification service sends mail from. Restricts ses:SendEmail to that domain via the ses:FromAddress condition key."
  type        = string
  default     = "panakoes.com"
}

variable "gpu_instance_types" {
  description = "EC2 instance types the gpu-spawner is allowed to launch. Constrains ec2:RunInstances via the ec2:InstanceType condition key. g4dn.xlarge is the locked default per CLAUDE.md (Whisper streaming AMI)."
  type        = list(string)
  default     = ["g4dn.xlarge"]
}
