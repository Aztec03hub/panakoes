# syntax=docker/dockerfile:1.7
#
# Container-image Lambda for the Event Router.
#
# Why container image (not zip):
#   - Path-dep on `services/audit-lib/` is awkward to ship as a layer.
#   - Container images ship with the AWS Lambda Runtime Interface Client
#     pre-installed, so the only thing we provide is the handler source
#     plus its dependencies.
#   - 10 GB image limit is plentiful for this service.
#
# Build context MUST be the repo root (`docker build -f services/event-router/Dockerfile .`)
# so the COPY of `../audit-lib` resolves. We mirror the pattern used by
# `services/ingestion-api/Dockerfile`.

FROM public.ecr.aws/lambda/python:3.12

# Copy the path-dep siblings first so subsequent installs see them.
COPY services/audit-lib /tmp/audit-lib
COPY services/otel-lib /tmp/otel-lib

# Install dependencies into the Lambda task root. We list each runtime
# dep explicitly here (rather than sourcing requirements.txt, which uses
# a relative path that does not resolve from the Lambda image build
# context) so the resolved versions are visible to the image scanner.
RUN pip install --no-cache-dir \
        "boto3>=1.35" \
        "structlog>=24.4" \
        /tmp/audit-lib \
        /tmp/otel-lib \
    && rm -rf /tmp/audit-lib /tmp/otel-lib

# Copy the handler source last so source-only changes invalidate fewer
# layers on rebuild.
COPY services/event-router/src/panakoes_event_router/ ${LAMBDA_TASK_ROOT}/panakoes_event_router/

# Lambda invokes `<module>.<function>` per the CMD below.
CMD ["panakoes_event_router.handler.lambda_handler"]
