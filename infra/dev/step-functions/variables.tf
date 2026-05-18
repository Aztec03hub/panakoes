variable "aws_region" {
  description = "AWS region for the dev environment Step Functions resources. Must match the region of the Lambdas, Batch job queue, and CloudWatch log group it talks to (cross-region orchestration is intentionally out of scope for the dev environment)."
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

variable "long_audio_threshold_seconds" {
  description = "Duration boundary in seconds between the short-audio single-Batch-job path and the long-audio chunk-and-fan-out path. Default 600s (10 minutes) sits comfortably under the 900s Lambda hard ceiling so a single transcription Lambda invocation stays viable for short files even with cold-start and model-load overhead."
  type        = number
  default     = 600
}

variable "chunk_duration_seconds" {
  description = "Target chunk duration emitted by the ChunkAudio Lambda. 480s (8 minutes) leaves a 7.5-minute headroom under the Lambda execution ceiling for any per-chunk Lambda work and keeps Whisper's attention window inside its trained context length, which produces materially better diarization than 10-15 minute chunks."
  type        = number
  default     = 480
}

variable "chunk_overlap_seconds" {
  description = "Overlap between adjacent chunks in seconds. The MergeTranscripts step deduplicates by aligning the trailing N seconds of one chunk against the leading N seconds of the next. Whisper hallucinations cluster in the first and last 10-15 seconds of a chunk, so a 30s window gives the deduper room to find a clean stitch point."
  type        = number
  default     = 30
}

variable "map_max_concurrency" {
  description = "MaxConcurrency for the parallel-transcribe Map state. Caps the number of Batch jobs in flight at once. 8 keeps a single execution from monopolizing the Batch GPU job queue while still parallelizing aggressively enough that a 90-minute audio file finishes in roughly 12 minutes wall-clock instead of 90."
  type        = number
  default     = 8
}

variable "task_retry_max_attempts" {
  description = "Number of automatic retry attempts each task in the state machine performs before its retry policy gives up and the Catch handler runs. 3 follows the AWS-recommended default for transient throttling and Lambda cold-start failures."
  type        = number
  default     = 3
}

variable "task_retry_interval_seconds" {
  description = "Initial backoff interval before the first retry. The exponential backoff multiplier is fixed at 2.0, so 2/4/8 second intervals give roughly 14 seconds of total retry headroom across three attempts."
  type        = number
  default     = 2
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the state machine's execution log group. 7 days in dev as of the 2026-05-18 tier-1 cost cut (previously 30); long-term archive is the job of `infra/dev/storage/`'s log-archive S3 bucket via subscription filters in a future module."
  type        = number
  default     = 7
}
