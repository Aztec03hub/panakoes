# Long-audio Step Functions smoke test

## Purpose

End-to-end invocation check for the `panakoes-dev-long-audio` Step Functions state machine. The state machine orchestrates duration detection, chunking, parallel Batch fan-out, transcript merging, and final-transcript write for any audio asset uploaded to `panakoes-dev-audio-uploads-*`. This runbook lets an operator confirm the state machine is deployed, invocable, and routes correctly into its first state, before any of the dependent Lambdas or the GPU Batch job queue are wired up.

## When to use this runbook

- After applying `infra/dev/step-functions/` for the first time, to confirm the deploy worked.
- After any change to the state machine definition (`infra/dev/step-functions/main.tf`).
- After deploying one of the dependent Lambdas (`detect-duration`, `chunk-audio`, `merge-transcripts`, `write-final-transcript`, `notify-failure`) to confirm the new Lambda is reachable from the state machine and that the IAM grant is in place.
- As a post-incident sanity check after a CloudWatch Logs / KMS / IAM rotation that touches the state-machine surface.

## Prerequisites

- AWS CLI configured with the `panakoes-admin` profile against `us-east-1`.
- IAM rights to call `stepfunctions:StartExecution`, `stepfunctions:DescribeExecution`, `stepfunctions:GetExecutionHistory`, `s3:PutObject` / `s3:DeleteObject` on `panakoes-dev-audio-uploads-*`.
- `aws stepfunctions list-state-machines --region us-east-1` returns an entry named `panakoes-dev-long-audio`.

Verify:

```bash
AWS_PROFILE=panakoes-admin aws stepfunctions list-state-machines --region us-east-1 \
  --query 'stateMachines[?name==`panakoes-dev-long-audio`].stateMachineArn' --output text
```

Expected output: `arn:aws:states:us-east-1:<account>:stateMachine:panakoes-dev-long-audio`.

## Expected state machine graph

```
DetectDuration (Task: lambda detect-duration)
  -> DurationChoice (Choice)
      < 600s -> SubmitBatchSingle (Task: batch:submitJob.sync)
                   -> WriteFinalTranscriptFromSingle (Task: lambda write-final-transcript)
                        -> Success (Succeed)
      >= 600s -> ChunkAudio (Task: lambda chunk-audio)
                   -> TranscribeChunksMap (Map, MaxConcurrency, INLINE)
                        per chunk: SubmitChunkBatchJob (Task: batch:submitJob.sync)
                   -> MergeTranscripts (Task: lambda merge-transcripts)
                        -> WriteFinalTranscriptFromChunks (Task: lambda write-final-transcript)
                             -> Success (Succeed)

Any task error -> NotifyFailure (Task: lambda notify-failure) -> FailTerminal (Fail)
```

The 10-minute threshold is `var.long_audio_threshold_seconds` (default 600). Files at or above the threshold take the chunked path.

## Procedure

### Lane A: state machine reachability (Lambdas not yet deployed)

This lane runs first against a fresh deploy, where none of the dependent Lambdas exist. The smoke proves the state machine starts, enters `DetectDuration`, exhausts the documented retry policy on `Lambda.ResourceNotFoundException`, routes to `NotifyFailure` via the `States.ALL` catch, and reaches terminal `FAILED` with a Lambda-not-found cause. That is the expected outcome until the Lambdas land.

1. **Generate a stub audio payload.** The DetectDuration Lambda is what would read the file, and it does not exist yet, so the file contents do not matter. Use any small placeholder:

   ```bash
   echo "stub-for-smoke-test" > /tmp/smoke-stub.wav
   SMOKE_KEY="smoke-tests/long-audio-deploy-$(date -u +%Y%m%dT%H%M%SZ).wav"
   AWS_PROFILE=panakoes-admin aws s3 cp /tmp/smoke-stub.wav \
     "s3://panakoes-dev-audio-uploads-f4ccc582/${SMOKE_KEY}" --region us-east-1
   ```

2. **Start an execution.**

   ```bash
   INPUT=$(printf '{"ingestion_id":"smoke-deploy-001","user_id":"smoke-user","audio_s3_uri":"s3://panakoes-dev-audio-uploads-f4ccc582/%s"}' "${SMOKE_KEY}")
   EXEC_ARN=$(AWS_PROFILE=panakoes-admin aws stepfunctions start-execution \
     --region us-east-1 \
     --state-machine-arn arn:aws:states:us-east-1:659225405128:stateMachine:panakoes-dev-long-audio \
     --name "smoke-$(date -u +%Y%m%dT%H%M%SZ)" \
     --input "$INPUT" --query executionArn --output text)
   echo "$EXEC_ARN"
   ```

3. **Poll for terminal state.** With six retries on `Lambda.ServiceException` / friends and BackoffRate 2.0, terminal `FAILED` is reached in under three minutes.

   ```bash
   until [ "$(AWS_PROFILE=panakoes-admin aws stepfunctions describe-execution \
     --region us-east-1 --execution-arn "$EXEC_ARN" \
     --query status --output text)" != "RUNNING" ]; do sleep 15; done
   ```

4. **Inspect the failure.**

   ```bash
   AWS_PROFILE=panakoes-admin aws stepfunctions describe-execution \
     --region us-east-1 --execution-arn "$EXEC_ARN" \
     --query '{status:status,error:error,cause:cause}' --output json
   ```

5. **Clean up.**

   ```bash
   AWS_PROFILE=panakoes-admin aws s3 rm \
     "s3://panakoes-dev-audio-uploads-f4ccc582/${SMOKE_KEY}" --region us-east-1
   ```

### Lane B: DetectDuration deployed, downstream Lambdas not yet (future)

Once `panakoes-dev-detect-duration` lands, the smoke advances one state. Repeat Lane A with a real 12-minute synthetic WAV so DetectDuration returns a duration above the 600s threshold and the execution enters `ChunkAudio`:

```bash
ffmpeg -f lavfi -i anullsrc=r=16000:cl=mono -t 720 /tmp/smoke-12min.wav
```

Upload and start as in Lane A. Expected terminal state: `FAILED` at `ChunkAudio` with `Lambda.ResourceNotFoundException` for `panakoes-dev-chunk-audio` (until that Lambda lands).

### Lane C: full pipeline deployed

When all five Lambdas and the `panakoes-dev-transcribe-batch` job queue exist, the smoke runs the full long-audio path. Do NOT use Lane C until the GPU AMI (`docs/runbooks/gpu-ami-bake.md`) is the active default for the transcribe-batch compute environment; otherwise Batch jobs queue indefinitely.

Expected terminal state: `SUCCEEDED`. The `WriteFinalTranscriptFromChunks` output is a final transcript object at `s3://panakoes-dev-transcripts-*/transcripts/<user>/<ingestion>/final.json`.

## Verification

For Lane A (state machine reachability):

- `status == "FAILED"`.
- `error == "Lambda.ResourceNotFoundException"`.
- `cause` mentions `panakoes-dev-notify-failure` (the failure-path Lambda was reached after the Catch handler fired on the DetectDuration retry exhaustion).
- The execution history shows: `ExecutionStarted -> TaskStateEntered:DetectDuration -> TaskFailed x N (Lambda.ResourceNotFoundException) -> TaskStateEntered:NotifyFailure -> TaskFailed (Lambda.ResourceNotFoundException) -> ExecutionFailed`. Pull via `aws stepfunctions get-execution-history --execution-arn "$EXEC_ARN"`.

For Lane B / C, success means the execution reached the expected terminal state (`FAILED` at the documented missing Lambda for Lane B, `SUCCEEDED` for Lane C) and the CloudWatch log group `/aws/states/panakoes-dev-long-audio` contains a full execution-history log group with `level=ALL` events including the per-state input and output payloads.

## Rollback

The smoke test is observe-only against the deployed state machine. The only side effects are:

- A small S3 object in `panakoes-dev-audio-uploads-*` (deleted in step 5).
- One execution history record retained in the state machine for 90 days (the AWS default for Standard workflows).

No rollback is needed beyond the S3 cleanup. If the cleanup step was skipped, run:

```bash
AWS_PROFILE=panakoes-admin aws s3 rm \
  "s3://panakoes-dev-audio-uploads-f4ccc582/smoke-tests/" --recursive --region us-east-1
```

## References

- `infra/dev/step-functions/main.tf`: state machine definition (states, retry policy, catch handler).
- `infra/dev/step-functions/variables.tf`: tunables (`long_audio_threshold_seconds`, `chunk_duration_seconds`, `chunk_overlap_seconds`, `map_max_concurrency`, `task_retry_*`).
- `docs/runbooks/gpu-ami-bake.md`: prerequisite for Lane C (the transcribe-batch Compute Environment needs the gpu-transcribe AMI before any Batch jobs the Map state submits will leave the queue).
- `CLAUDE.md` Locked Architectural Decisions: "Step Functions fan-out for files > 10 minutes".
- AWS docs: [Step Functions service integrations](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-service-integrations.html) (`.sync` semantics for `batch:submitJob`), [Map state](https://docs.aws.amazon.com/step-functions/latest/dg/amazon-states-language-map-state.html).
