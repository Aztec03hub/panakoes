# syntax=docker/dockerfile:1.7
#
# Container-image Lambda for the Cost Rollup Aggregator.
#
# Why container image (not zip):
#   - Path-dep on `services/cost-api/` (we reuse its `TenantRollupStore`)
#     is awkward to ship as a zip layer.
#   - Container images ship with the AWS Lambda Runtime Interface Client
#     pre-installed, so the only thing we provide is the handler source
#     plus its dependencies.
#
# Build context MUST be the repo root so the COPY of `services/cost-api`
# resolves. Mirrors the pattern used by `services/event-router/Dockerfile`.

FROM public.ecr.aws/lambda/python:3.12

# Copy the path-dep sibling first so subsequent installs see it. Only
# the source tree is copied (not tests / .venv) to keep the image small.
COPY services/cost-api/pyproject.toml /tmp/cost-api/pyproject.toml
COPY services/cost-api/README.md /tmp/cost-api/README.md
COPY services/cost-api/src /tmp/cost-api/src

# Install dependencies into the Lambda task root. We list each runtime
# dep explicitly so the resolved versions are visible to the image
# scanner. The cost-api install pulls only the storage modules we
# actually import (`tenant_rollup`, `models`); the FastAPI surface is
# inert at runtime because no route handler is invoked.
RUN pip install --no-cache-dir \
        "boto3>=1.35" \
        "structlog>=24.4" \
        /tmp/cost-api \
    && rm -rf /tmp/cost-api

# Copy the handler source last so source-only changes invalidate fewer
# layers on rebuild.
COPY services/cost-rollup-aggregator/src/panakoes_cost_rollup_aggregator/ ${LAMBDA_TASK_ROOT}/panakoes_cost_rollup_aggregator/

# Lambda invokes `<module>.<function>` per the CMD below.
CMD ["panakoes_cost_rollup_aggregator.handler.lambda_handler"]
