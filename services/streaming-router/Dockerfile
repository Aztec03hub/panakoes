# syntax=docker/dockerfile:1.7
#
# Container-image Lambda for the streaming-router.

FROM public.ecr.aws/lambda/python:3.12

RUN pip install --no-cache-dir \
        "boto3>=1.35" \
        "structlog>=24.4"

COPY services/streaming-router/src/panakoes_streaming_router/ ${LAMBDA_TASK_ROOT}/panakoes_streaming_router/

CMD ["panakoes_streaming_router.router.lambda_handler"]
