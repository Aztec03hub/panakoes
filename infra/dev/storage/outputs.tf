output "audio_uploads_bucket_name" {
  description = "Name of the audio-uploads S3 bucket. Clients PUT raw audio here via the Ingestion API."
  value       = aws_s3_bucket.audio_uploads.id
}

output "audio_uploads_bucket_arn" {
  description = "ARN of the audio-uploads S3 bucket. Use in IAM policies that grant the Ingestion API and transcription Lambdas access."
  value       = aws_s3_bucket.audio_uploads.arn
}

output "audio_uploads_kms_key_arn" {
  description = "ARN of the CMK encrypting the audio-uploads bucket. Required in IAM policies that grant kms:Decrypt to consumers."
  value       = aws_kms_key.audio_uploads.arn
}

output "transcripts_bucket_name" {
  description = "Name of the transcripts S3 bucket. The transcription Lambda writes JSON output here."
  value       = aws_s3_bucket.transcripts.id
}

output "transcripts_bucket_arn" {
  description = "ARN of the transcripts S3 bucket."
  value       = aws_s3_bucket.transcripts.arn
}

output "transcripts_kms_key_arn" {
  description = "ARN of the CMK encrypting the transcripts bucket."
  value       = aws_kms_key.transcripts.arn
}

output "log_archive_bucket_name" {
  description = "Name of the log-archive S3 bucket. CloudWatch Logs flow here for long-term Athena-queryable storage."
  value       = aws_s3_bucket.log_archive.id
}

output "log_archive_bucket_arn" {
  description = "ARN of the log-archive S3 bucket."
  value       = aws_s3_bucket.log_archive.arn
}

output "log_archive_kms_key_arn" {
  description = "ARN of the CMK encrypting the log-archive bucket."
  value       = aws_kms_key.log_archive.arn
}
