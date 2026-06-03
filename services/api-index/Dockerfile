# syntax=docker/dockerfile:1.7
#
# Container-image Lambda for the public HTTP API root index.
#
# No third-party runtime dependencies (the handler is pure stdlib), so
# there is nothing to pip install beyond the package itself. Build
# context is the repo root so the COPY path resolves; mirrors the
# sibling Lambda services' packaging.

FROM public.ecr.aws/lambda/python:3.12

COPY services/api-index/src/panakoes_api_index/ ${LAMBDA_TASK_ROOT}/panakoes_api_index/

CMD ["panakoes_api_index.handler.lambda_handler"]
