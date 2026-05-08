# Dev Environment Step Functions

Per-environment Terraform configuration creating the long-audio
transcription state machine for the `dev` environment. State lives at
`dev/step-functions/terraform.tfstate` in the shared S3 backend
created by `infra/bootstrap/`.

## Why this exists

AWS Lambda has a hard 15-minute (900-second) execution ceiling. Any
audio file long enough that even chunked transcription cannot fit
inside a single Lambda needs an external orchestrator. Step Functions
Standard workflows are the AWS-native fit: they run for up to a year,
emit a full audit trail to CloudWatch, and let us model the
chunk/fan-out/merge pipeline declaratively rather than as a chain of
Lambdas calling each other.

The state machine handles two paths in one definition:

1. **Short audio (`< long_audio_threshold_seconds`)**: a single AWS
   Batch GPU job transcribes the file end-to-end. No chunking, no
   merging.
2. **Long audio (`>=` threshold)**: a `ChunkAudio` Lambda splits the
   file into overlapping chunks, a parallel `Map` state submits a
   Batch job per chunk, and a `MergeTranscripts` Lambda reassembles
   the per-chunk transcripts into a single transcript.

Both paths converge at `WriteFinalTranscript`, which writes the
canonical transcript to the `panakoes-dev-transcripts` S3 bucket and
flips the ingestion record's status to `transcribed`.

## State machine flow

```
                       StartExecution
                             |
                             v
                       DetectDuration
                             |
                             v
                       DurationChoice
                       /            \
              < 600s   |              | >= 600s
                       v              v
                SubmitBatchSingle   ChunkAudio
                       |              |
                       v              v
                       |         TranscribeChunksMap (MaxConcurrency=8)
                       |              |
                       |              v
                       |         MergeTranscripts
                       |              |
                       v              v
                WriteFinalTranscriptFromSingle | FromChunks
                                  \   /
                                   v
                                Success

   Any task failure -> NotifyFailure -> FailTerminal
```

`DetectDuration`, `ChunkAudio`, `MergeTranscripts`,
`WriteFinalTranscript`, and `NotifyFailure` are all Lambda-backed
Tasks. The two Batch states use the synchronous `.sync` integration so
the state machine waits for each Batch job to finish before the next
state runs.

## Workflow type: STANDARD vs EXPRESS

Standard, deliberately. Express workflows cap at five minutes total
execution and do not persist execution history beyond CloudWatch
metrics, which would break:

- any chunked run that takes longer than five minutes wall-clock
  (most of them, since GPU job startup alone can eat 30 to 90 seconds
  per chunk before model inference even begins);
- the postmortem audit trail we get from Standard's per-state input
  and output capture.

Standard bills per state transition (roughly $0.025 per 1000), which
for a 90-minute file fanning out to 12 chunks costs well under a
penny per execution. The cost discipline lives in the Batch GPU
runtime, not the orchestrator.

## Retry and failure handling

Every Task carries a retry policy targeting transient Lambda and
Step-Functions failures (`Lambda.ServiceException`,
`Lambda.AWSLambdaException`, `Lambda.SdkClientException`,
`Lambda.TooManyRequestsException`, `States.TaskFailed`,
`States.Timeout`) with three attempts and 2-second initial backoff,
exponential factor 2.0 (so 2/4/8 second pauses).

Every Task also catches `States.ALL` and routes to `NotifyFailure`,
which:

1. Updates the ingestion record's status to `failed` in
   `panakoes-dev-ingestion`.
2. Emits a `panakoes.transcribe / TranscriptionFailed` event onto the
   project EventBridge bus.
3. Returns control to a terminal `Fail` state so CloudWatch sees the
   execution as failed.

`NotifyFailure` itself has no retry policy. If the failure-notifier
fails, we want a fast terminal state in CloudWatch rather than
infinite retry loops in the catch handler.

## Forward references

Several resources this module references do not exist yet. They are
constructed as ARNs / names so the state machine and IAM policies are
correct the moment the resources are created, with no broader
`Resource = "*"` gap in the meantime.

| Resource                                          | Status            | Note                                                                                       |
|---------------------------------------------------|-------------------|--------------------------------------------------------------------------------------------|
| `panakoes-dev-detect-duration` Lambda             | not created       | Probes audio duration via streaming ffprobe header read.                                   |
| `panakoes-dev-chunk-audio` Lambda                 | not created       | Splits audio into overlapping chunks; writes chunks back to the audio-uploads bucket.      |
| `panakoes-dev-merge-transcripts` Lambda           | not created       | Reassembles per-chunk transcripts; deduplicates the overlap window.                        |
| `panakoes-dev-write-final-transcript` Lambda      | not created       | Writes canonical transcript to `panakoes-dev-transcripts`; updates ingestion record.       |
| `panakoes-dev-notify-failure` Lambda              | not created       | Updates ingestion status to `failed`; emits `TranscriptionFailed` to EventBridge.          |
| `panakoes-dev-transcribe-batch` Batch job queue   | not created       | Lives in the forthcoming `infra/dev/batch/` module.                                        |
| `panakoes-dev-transcribe-batch` Batch job def     | not created       | Lives in the forthcoming `infra/dev/batch/` module.                                        |
| `panakoes-dev` EventBridge bus                    | not created       | Lives in the forthcoming `infra/dev/eventbridge/` module.                                  |
| Shared dev CloudWatch CMK                         | not created       | Lives in the forthcoming `infra/dev/observability/` module. Fallback CMK provisioned here. |

When the `infra/dev/batch/` and `infra/dev/observability/` modules
land, this module's `terraform_remote_state` blocks resolve their
outputs and the constructed-fallback locals collapse cleanly. Until
then the `try()` wrappers fall back to the constructed values so the
module remains self-applicable.

## Lambda contracts

The state machine wires inputs and outputs in a specific shape. The
Lambdas are tracked separately in the backlog; documenting the shape
here keeps the contract stable across the slice that ships them.

| Lambda                  | Input                                                                                       | Output                                                                                |
|-------------------------|---------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| `detect_duration`       | `{ ingestion_id, user_id, audio_s3_uri }`                                                   | `{ ingestion_id, user_id, audio_s3_uri, duration_seconds }`                           |
| `chunk_audio`           | `{ ingestion_id, user_id, audio_s3_uri, duration_seconds, chunk_duration_seconds, chunk_overlap_seconds }` | `{ ingestion_id, user_id, chunks: [ { index, s3_uri, start_seconds, end_seconds } ] }` |
| `merge_transcripts`     | `{ ingestion_id, user_id, chunk_results: [...] }`                                           | `{ ingestion_id, user_id, merged_transcript_s3_uri }`                                 |
| `write_final_transcript`| `{ ingestion_id, user_id, merged_transcript_s3_uri | mode: "merged"|"single" }`             | `{ ingestion_id, final_transcript_s3_uri, status: "transcribed" }`                    |
| `notify_failure`        | `{ ingestion_id, user_id, error: { Error, Cause }, execution_arn, state_machine_arn }`      | `{ ingestion_id, status: "failed" }`                                                  |

## IAM least-privilege approach

The state machine's role can:

- `lambda:InvokeFunction` on the five Lambda ARNs listed above and
  nothing else (no wildcard on function name).
- `batch:SubmitJob` and `batch:TerminateJob` on the
  `panakoes-dev-transcribe-batch` job queue and definition only.
  `batch:DescribeJobs` is on `*` because the AWS API has no
  resource-level authorization for that verb (same exception as
  `ec2:DescribeInstances`).
- `events:PutEvents` on the project EventBridge bus only.
- The CloudWatch Logs management surface required for the Standard
  workflow's log-delivery configuration (the `logs:*LogDelivery`
  verbs require `Resource = "*"` per AWS docs).
- `kms:Encrypt`/`Decrypt`/`GenerateDataKey`/`DescribeKey` on the log
  group's CMK only.
- The `events:Put*` verbs on the
  `StepFunctionsGetEventsForBatchJobsRule` managed rule that the
  Map `.sync` integration uses internally (AWS-documented).

## Logging configuration

`level = ALL` and `include_execution_data = true`. Yes, this is
verbose. The verbosity is the point: a failed long-audio run can be
replayed offline from the CloudWatch log group because every state's
input and output payload is captured.

Retention is 30 days; long-term archive ships to the `log-archive` S3
bucket via subscription filter (forthcoming).

The log group is KMS-encrypted by either the shared dev observability
CMK (when applied) or a fallback CMK created in this module.

## Apply

```bash
cd infra/dev/step-functions
AWS_PROFILE=lafayettelabs terraform init
AWS_PROFILE=lafayettelabs terraform plan
AWS_PROFILE=lafayettelabs terraform apply
```

`terraform init` downloads the AWS provider and initializes the S3
backend. The `dev/storage`, `dev/data`, and `dev/iam` configurations
must already be applied (the first two for context, IAM for forward
compatibility with downstream wiring). The `dev/batch` and
`dev/observability` configurations are forward-referenced via
`try()` and may be applied before or after this module.

## Consuming outputs from other configs

The event-router Lambda will eventually call StartExecution on the
state machine. Wire it via `terraform_remote_state`:

```hcl
data "terraform_remote_state" "step_functions" {
  backend = "s3"
  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/step-functions/terraform.tfstate"
    region = "us-east-1"
  }
}

# event-router env var:
# STATE_MACHINE_ARN = data.terraform_remote_state.step_functions.outputs.state_machine_arn
```

## Outputs

| Output                  | Type           | Purpose                                                  |
|-------------------------|----------------|----------------------------------------------------------|
| `state_machine_arn`     | `string`       | StartExecution target.                                   |
| `state_machine_name`    | `string`       | CLI-friendly name.                                       |
| `state_machine_role_arn`| `string`       | IAM role the state machine assumes at runtime.           |
| `log_group_name`        | `string`       | CloudWatch log group name.                               |
| `log_group_arn`         | `string`       | CloudWatch log group ARN.                                |
| `log_group_kms_key_arn` | `string`       | KMS CMK encrypting the log group.                        |
| `lambda_function_names` | `map(string)`  | Logical role -> Lambda function name (forward contract). |
