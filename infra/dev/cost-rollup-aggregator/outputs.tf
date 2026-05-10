output "lambda_function_name" {
  description = "Name of the cost-rollup-aggregator Lambda. The operator pushes a fresh container image to ECR and the next nightly run picks it up; manual invocation by name (e.g., for replay of a specific day) is `aws lambda invoke --function-name <this> --payload '{\"day\":\"YYYY-MM-DD\"}' /tmp/out.json`."
  value       = aws_lambda_function.aggregator.function_name
}

output "lambda_function_arn" {
  description = "ARN of the cost-rollup-aggregator Lambda. Required by the EventBridge Scheduler target wiring (already wired in this module) and by any future cross-module IAM policy that references the function."
  value       = aws_lambda_function.aggregator.arn
}

output "schedule_arn" {
  description = "ARN of the EventBridge Scheduler rule that fires the Lambda nightly at 02:00 UTC. Disabling the schedule (without destroying it) is `aws scheduler update-schedule --name <local-name> --state DISABLED`; useful during a CE outage."
  value       = aws_scheduler_schedule.nightly.arn
}

output "ecr_repository_url" {
  description = "ECR repository the Lambda pulls its image from. The operator pushes here with `docker push <this>:latest` after building the image; the Lambda's `image_uri` is pinned to `:latest` and lifecycle-ignored so a re-apply does not race the push."
  value       = data.terraform_remote_state.ecr.outputs.repository_urls["cost-rollup-aggregator"]
}

output "log_group_name" {
  description = "CloudWatch Log Group the Lambda writes to. Useful for `aws logs tail <this> --follow` during a manual replay."
  value       = aws_cloudwatch_log_group.aggregator.name
}

output "execution_role_arn" {
  description = "IAM role the Lambda assumes at runtime. Useful for granting cross-module IAM trust if a future writer needs to assume the same identity."
  value       = aws_iam_role.aggregator.arn
}
