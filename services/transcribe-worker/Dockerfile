# syntax=docker/dockerfile:1.7
#
# Container-image Lambda for the Transcribe Worker.
#
# Why container image (not zip):
#   - Path-deps on five sibling packages (audit-lib, otel-lib,
#     transcriber-lib, transcriber-groq, ingestion-api) are awkward to
#     ship as a Lambda layer.
#   - Container images ship with the AWS Lambda Runtime Interface Client
#     pre-installed, so the only thing we provide is the handler source
#     plus its dependencies.
#   - 10 GB image limit is plentiful for this service.
#
# Build context MUST be the repo root
# (`docker build -f services/transcribe-worker/Dockerfile .`)
# so the COPYs of sibling packages resolve. Mirrors `services/event-router/Dockerfile`.

FROM public.ecr.aws/lambda/python:3.12

# Copy the path-dep siblings first so subsequent installs see them.
# The ingestion-api package is what bridges this Lambda to the existing
# `transcribe_ingestion` orchestration (single-source-of-truth path).
COPY services/audit-lib /tmp/audit-lib
COPY services/otel-lib /tmp/otel-lib
COPY services/transcriber-lib /tmp/transcriber-lib
COPY services/transcriber-groq /tmp/transcriber-groq
COPY services/ingestion-api /tmp/ingestion-api

# Install dependencies into the Lambda task root. We list each runtime
# dep explicitly here (rather than sourcing requirements.txt, which uses
# relative paths that do not resolve from the Lambda image build context)
# so the resolved versions are visible to the image scanner.
RUN pip install --no-cache-dir \
        "boto3>=1.35" \
        "structlog>=24.4" \
        /tmp/audit-lib \
        /tmp/otel-lib \
        /tmp/transcriber-lib \
        /tmp/transcriber-groq \
        /tmp/ingestion-api \
    && rm -rf /tmp/audit-lib /tmp/otel-lib /tmp/transcriber-lib /tmp/transcriber-groq /tmp/ingestion-api

# Copy the handler source last so source-only changes invalidate fewer
# layers on rebuild.
COPY services/transcribe-worker/src/panakoes_transcribe_worker/ \
     ${LAMBDA_TASK_ROOT}/panakoes_transcribe_worker/

# Lambda invokes `<module>.<function>` per the CMD below.
CMD ["panakoes_transcribe_worker.handler.lambda_handler"]
