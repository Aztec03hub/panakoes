# syntax=docker/dockerfile:1.7
#
# Container-image Lambda for the WebSocket $connect authorizer.
#
# Build context MUST be the repo root so the COPY of `services/auth-client`
# resolves. Mirrors `services/cost-rollup-aggregator/Dockerfile`.

FROM public.ecr.aws/lambda/python:3.12

# Sibling auth-client install (path-dep, mirrors cost-rollup-aggregator).
COPY services/auth-client/pyproject.toml /tmp/auth-client/pyproject.toml
COPY services/auth-client/README.md /tmp/auth-client/README.md
COPY services/auth-client/src /tmp/auth-client/src

RUN pip install --no-cache-dir \
        "structlog>=24.4" \
        "PyJWT[crypto]>=2.10.1" \
        /tmp/auth-client \
    && rm -rf /tmp/auth-client

COPY services/ws-authorizer/src/panakoes_ws_authorizer/ ${LAMBDA_TASK_ROOT}/panakoes_ws_authorizer/

CMD ["panakoes_ws_authorizer.authorizer.lambda_handler"]
