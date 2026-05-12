output "api_id" {
  description = "ID of the panakoes-dev-streaming-ws WebSocket API. Used by the future authorizer-attach PR and by downstream modules that need to reference this API in CloudWatch dashboards or alarms."
  value       = aws_apigatewayv2_api.main.id
}

output "api_arn" {
  description = "ARN of the panakoes-dev-streaming-ws WebSocket API."
  value       = aws_apigatewayv2_api.main.arn
}

output "api_endpoint" {
  description = "Default endpoint of the WebSocket API (`wss://<api-id>.execute-api.<region>.amazonaws.com`). Append `/<stage>` for the stage-scoped URL."
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "stage_name" {
  description = "Name of the deployed stage (`dev`)."
  value       = aws_apigatewayv2_stage.main.name
}

output "stage_invoke_url" {
  description = "Full WebSocket stage invoke URL (`wss://<api-id>.execute-api.<region>.amazonaws.com/<stage_name>`). The browser-mic demo and the smoke runbook both target this URL."
  value       = "${aws_apigatewayv2_api.main.api_endpoint}/${aws_apigatewayv2_stage.main.name}"
}

output "frame_queue_url" {
  description = "URL of the streaming frame SQS queue. The smoke runbook polls this queue to confirm route dispatch."
  value       = aws_sqs_queue.frames.id
}

output "frame_queue_arn" {
  description = "ARN of the streaming frame SQS queue. The follow-up streaming-router Lambda's IAM policy grants `sqs:ReceiveMessage` against this ARN."
  value       = aws_sqs_queue.frames.arn
}

output "access_log_group_name" {
  description = "Name of the CloudWatch log group receiving WebSocket access logs."
  value       = aws_cloudwatch_log_group.access.name
}

output "kms_key_arn" {
  description = "ARN of the CMK encrypting the WebSocket access log group."
  value       = aws_kms_key.ws_logs.arn
}
